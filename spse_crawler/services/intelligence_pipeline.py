"""Core intelligence pipeline — automated, bounded, observable, failure-safe.

Drives the per-(tender, company) flow:
    TENDER -> ELIGIBILITY -> AI_MATCH -> OPPORTUNITY_SCORE -> RADAR

Backed by the persistent IntelligenceJob queue. Reuses the existing
ai_match.matcher.run_match and opportunity_scorer_v01.calculate — it never
creates a second AI/scoring implementation.

Guarantees:
  - Bounded: at most AI_PIPELINE_BATCH_SIZE jobs per cycle.
  - Idempotent: enqueue uses (tender, company, job_type); Opportunity Score
    upserts; AI Match is skipped when a valid AIMatchResult exists.
  - Company-isolated: each job is tied to exactly one (tender, company) pair.
  - Rate-limited: respects AI_MAX_MATCHES_PER_DAY per company (counts actual
    non-cached AI executions, not queued jobs).
  - Retryable: transient failures retry up to max_attempts with backoff.
  - Stale-safe: PROCESSING jobs older than the stale timeout return to PENDING.
  - Failure-safe: a failing job never aborts the batch.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from loguru import logger

from spse_crawler.web.models import IntelligenceJob, IntelligenceDailyUsage
from .intelligence_eligibility import is_eligible, is_terminal_stage

# The matcher marks LLM/provider failure with this summary prefix.
_AI_FAILURE_MARKER = "AI analysis failed:"

# Max number of newly-scraped active tenders to enqueue after a single crawl.
CRAWL_ENQUEUE_CAP = 500


def _now():
    return timezone.now()


def _today():
    return timezone.localdate()


# ---------------------------------------------------------------------------
# ENQUEUE
# ---------------------------------------------------------------------------

def enqueue_for_tender(tender_ids, company_ids=None, savepoint: bool = True):
    """Idempotently create AI_MATCH jobs for given tenders and companies.

    company_ids=None -> all active companies.
    Also schedules the dependent OPPORTUNITY_SCORE job so the chain proceeds.
    Duplicate (tender, company, job_type) rows are never created.
    """
    from spse_crawler.companies.models import CompanyProfile
    from spse_crawler.web.models import TenderResult

    if not tender_ids:
        return 0

    if company_ids is None:
        company_ids = list(
            CompanyProfile.objects.filter(is_active=True).values_list("id", flat=True)
        )
    else:
        company_ids = list(company_ids)

    if not company_ids:
        return 0

    tender_ids = list(tender_ids)
    existing = set(
        IntelligenceJob.objects.filter(
            tender_id__in=tender_ids,
            company_id__in=company_ids,
            job_type="AI_MATCH",
        ).values_list("tender_id", "company_id")
    )

    now = _now()
    created = 0
    for tid in tender_ids:
        for cid in company_ids:
            if (tid, cid) in existing:
                continue
            with transaction.atomic():
                _, was_created = IntelligenceJob.objects.get_or_create(
                    tender_id=tid,
                    company_id=cid,
                    job_type="AI_MATCH",
                    defaults={
                        "status": "pending",
                        "max_attempts": _max_attempts(),
                        "scheduled_at": now,
                    },
                )
                if was_created:
                    created += 1
    return created


def enqueue_new_after_crawl(cap: int | None = None) -> int:
    """Enqueue AI_MATCH jobs for active tenders not yet queued, post-crawl.

    Bounded to *cap* (default CRAWL_ENQUEUE_CAP) newest-scraped active tenders
    that have no AI_MATCH job yet. Idempotent.
    """
    from spse_crawler.web.models import TenderResult

    cap = cap or CRAWL_ENQUEUE_CAP
    have_job = set(
        IntelligenceJob.objects.filter(job_type="AI_MATCH", status__in=[
            "pending", "processing", "completed",
        ]).values_list("tender_id", flat=True).distinct()
    )
    qs = (
        TenderResult.objects.exclude(
            tahap_saat_ini__icontains="selesai"
        ).exclude(tahap_saat_ini__icontains="kontrak")
         .exclude(tahap_saat_ini__icontains="pembatalan")
         .exclude(tahap_saat_ini__icontains="dibatalkan")
         .exclude(tahap_saat_ini__icontains="gugur")
    )
    from spse_crawler.services.tender_status import is_submittable_tender

    candidate_ids = []
    for t in qs.order_by("-scraped_at")[:cap * 4]:
        if is_submittable_tender(t) and t.id not in have_job:
            candidate_ids.append(t.id)
        if len(candidate_ids) >= cap:
            break

    if not candidate_ids:
        return 0
    return enqueue_for_tender(candidate_ids)


# ---------------------------------------------------------------------------
# CONFIG HELPERS
# ---------------------------------------------------------------------------

def _settings():
    from spse_crawler.config.settings import get_settings
    return get_settings()


def _batch_size() -> int:
    return _settings().ai_pipeline_batch_size


def _max_attempts() -> int:
    return _settings().ai_pipeline_max_attempts


def _retry_delay_minutes() -> int:
    return _settings().ai_pipeline_retry_delay_minutes


def _stale_minutes() -> int:
    return _settings().ai_pipeline_stale_minutes


def _daily_limit() -> int:
    return _settings().ai_max_matches_per_day


# ---------------------------------------------------------------------------
# DAILY USAGE / RATE LIMIT
# ---------------------------------------------------------------------------

def _co_exists_defaults():
    return {}


def _consume_ai_budget(company_id: int) -> bool:
    """Atomically consume one AI budget slot for a company today.

    Returns True if a slot was available and consumed (LLM execution allowed),
    False if the daily budget is exhausted.
    """
    limit = _daily_limit()
    obj, _ = IntelligenceDailyUsage.objects.get_or_create(
        company_id=company_id,
        usage_date=_today(),
        defaults={"ai_matches": 0},
    )
    with transaction.atomic():
        locked = None
        try:
            locked = IntelligenceDailyUsage.objects.select_for_update().get(
                id=obj.id
            )
        except IntelligenceDailyUsage.DoesNotExist:
            locked, _ = IntelligenceDailyUsage.objects.get_or_create(
                company_id=company_id,
                usage_date=_today(),
                defaults={"ai_matches": 0},
            )
        if locked.ai_matches >= limit:
            return False
        locked.ai_matches += 1
        locked.save(update_fields=["ai_matches"])
        return True


def daily_ai_usage(company_id: int) -> int:
    try:
        return IntelligenceDailyUsage.objects.get(
            company_id=company_id, usage_date=_today()
        ).ai_matches
    except IntelligenceDailyUsage.DoesNotExist:
        return 0


# ---------------------------------------------------------------------------
# STALE RECOVERY
# ---------------------------------------------------------------------------

def recover_stale_processing(stale_minutes: int | None = None) -> int:
    """Return PROCESSING jobs stuck longer than the stale timeout to PENDING."""
    stale_minutes = stale_minutes or _stale_minutes()
    threshold = _now() - timedelta(minutes=stale_minutes)
    updated = IntelligenceJob.objects.filter(
        status="processing",
        started_at__lt=threshold,
    ).update(status="pending", started_at=None)
    if updated:
        logger.warning("[PIPELINE] Recovered {} stale PROCESSING jobs", updated)
    return updated


def _claim_jobs(batch_size: int) -> list[IntelligenceJob]:
    """Claim eligible PENDING jobs atomically.

    Only PENDING jobs whose scheduled_at is due (or null) are claimed.
    Returns a list of claimed job records (status transitioned to PROCESSING).
    """
    now = _now()
    claimed = []
    with transaction.atomic():
        qs = (
            IntelligenceJob.objects.select_for_update()
            .filter(
                status="pending",
            )
            .filter(Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now))
            .order_by("-priority", "scheduled_at", "id")[:batch_size]
        )
        for job in qs:
            job.status = "processing"
            job.started_at = now
            job.save(update_fields=["status", "started_at", "updated_at"])
            claimed.append(job)
    return claimed


# ---------------------------------------------------------------------------
# EXECUTORS
# ---------------------------------------------------------------------------

def _run_ai_match(job: IntelligenceJob) -> tuple[bool, str | None, str | None]:
    """Execute AI Match for a job.

    Returns (success, retryable, error). rate_limited handled separately.
    Never raises.
    """
    from spse_crawler.ai_match.matcher import run_match
    from spse_crawler.ai_match.models import AIMatchResult
    from spse_crawler.services.tender_status import is_submittable_tender
    from spse_crawler.web.models import TenderResult

    # Verify active submittable status before executing AI Match
    tender = TenderResult.objects.filter(id=job.tender_id).first()
    if not is_submittable_tender(tender):
        return False, False, "tender_not_submittable"

    # Idempotency: if a valid AIMatchResult already exists, do not call LLM.
    existing = AIMatchResult.objects.filter(
        tender_id=job.tender_id, company_id=job.company_id
    ).first()
    if existing and existing.fit_score > 0 and not (
        existing.summary or ""
    ).startswith(_AI_FAILURE_MARKER):
        return True, None, None

    # Rate limit: count actual executions (non-cached) per company/day.
    if not _consume_ai_budget(job.company_id):
        return False, None, None  # not success, not error → leave PENDING + retry

    try:
        result = run_match(tender_id=job.tender_id, company_id=job.company_id, force=False)
    except Exception as exc:
        # run_match usually swallows errors, but guard anyway.
        return False, True, _sanitize(str(exc))

    if result.get("cached"):
        # Cache hit — a valid match already exists; no LLM executed.
        return True, None, None

    summary = result.get("summary", "")
    if summary.startswith(_AI_FAILURE_MARKER):
        # LLM/provider failure: delete the zero-score result so a retry
        # re-invokes the LLM instead of returning the cached failure.
        AIMatchResult.objects.filter(
            tender_id=job.tender_id, company_id=job.company_id
        ).delete()
        return False, True, _sanitize(summary)

    if result.get("fit_score", 0) == 0:
        # Ambiguous zero without a failure marker (e.g. missing tender/company).
        # Missing tender/company are permanent.
        if existing is None and _job_missing_entity(job):
            return False, False, "missing_entity"
        return False, True, "ai_match_zero_score"

    return True, None, None


def _job_missing_entity(job: IntelligenceJob) -> bool:
    return False


def _run_opportunity_score(job: IntelligenceJob) -> tuple[bool, str | None, str | None]:
    """Execute Opportunity Score v0.1 for a job. Never raises, never fabricates."""
    from spse_crawler.services.opportunity_scorer_v01 import OpportunityScorerV01 as Scorer
    from spse_crawler.services.tender_status import is_submittable_tender
    from spse_crawler.web.models import TenderResult

    tender = TenderResult.objects.filter(id=job.tender_id).first()
    if not is_submittable_tender(tender):
        return False, False, "tender_not_submittable"

    try:
        obj = Scorer.calculate(tender_id=job.tender_id, company_id=job.company_id)
    except Exception as exc:
        return False, True, _sanitize(str(exc))
    if obj.status == "ready":
        return True, None, None
    # NOT_READY: no valid AIMatchResult — a pipeline-ordering violation.
    return False, False, "not_ready_no_ai_match"


# ---------------------------------------------------------------------------
# JOB FINALIZATION
# ---------------------------------------------------------------------------

def _mark(job, status, error="", set_attemp_inc=True):
    now = _now()
    updates = {
        "status": status,
        "finished_at": now if status in ("completed", "failed", "skipped") else job.finished_at,
        "last_error": error if status in ("failed", "skipped") else job.last_error,
        "updated_at": now,
    }
    if status == "failed":
        job.attempts += 1
    if status == "completed":
        job.attempts += 1
    for k, v in updates.items():
        setattr(job, k, v)
    job.save(update_fields=list(updates.keys()) + ["attempts"])


def _schedule_retry(job: IntelligenceJob, error: str):
    """Schedule a delayed retry for a transient failure."""
    job.attempts += 1
    now = _now()
    delay = timedelta(minutes=_retry_delay_minutes() * job.attempts)
    if job.attempts >= job.max_attempts:
        job.status = "failed"
        job.finished_at = now
        job.last_error = error
        job.started_at = None
    else:
        job.status = "pending"
        job.scheduled_at = now + delay
        job.started_at = None
        job.last_error = error
    job.updated_at = now
    job.save(update_fields=[
        "status", "attempts", "scheduled_at", "started_at", "finished_at",
        "last_error", "updated_at",
    ])


# ---------------------------------------------------------------------------
# PROCESS ONE JOB
# ---------------------------------------------------------------------------

def _process_job(job: IntelligenceJob) -> None:
    """Process a single claimed job. Returns nothing; never raises out of the batch."""
    from spse_crawler.web.models import TenderResult
    from spse_crawler.companies.models import CompanyProfile

    try:
        tender = TenderResult.objects.get(id=job.tender_id)
        company = CompanyProfile.objects.get(id=job.company_id)
    except (TenderResult.DoesNotExist, CompanyProfile.DoesNotExist):
        _mark(job, "failed", "missing_entity")
        return

    if job.job_type == "AI_MATCH":
        _process_ai_match(job, tender, company)
    elif job.job_type == "OPPORTUNITY_SCORE":
        _process_opportunity_score(job, tender, company)


def _process_ai_match(job, tender, company) -> None:
    eligible, skip_reason, error_reason = is_eligible(tender, company)
    if not eligible:
        if error_reason:
            _mark(job, "failed", error_reason)
        else:
            _mark(job, "skipped", skip_reason or "ineligible")
        # Opportunity Score must not run without a valid AI Match.
        _skip_opportunity_for(job, skip_reason or "ineligible")
        return

    success, retryable, error = _run_ai_match(job)
    if success:
        _mark(job, "completed")
        _enqueue_opportunity(job)
        return
    if error is None:
        # Rate-limited — leave for a later cycle, do not burn attempts.
        job.status = "pending"
        job.updated_at = _now()
        job.save(update_fields=["status", "updated_at"])
        return
    if retryable is False:
        _mark(job, "failed", error)
        _skip_opportunity_for(job, error)
        return
    _schedule_retry(job, error)


def _process_opportunity_score(job, tender, company) -> None:
    ai_exists = _valid_ai_match_exists(job.tender_id, job.company_id)
    if not ai_exists:
        # Pipeline ordering violation — never score without a valid AI match.
        _mark(job, "failed", "no_valid_ai_match")
        return
    success, retryable, error = _run_opportunity_score(job)
    if success:
        _mark(job, "completed")
    elif retryable:
        _schedule_retry(job, error or "transient_score_error")
    else:
        _mark(job, "failed", error or "score_failed")


def _valid_ai_match_exists(tender_id, company_id) -> bool:
    from spse_crawler.ai_match.models import AIMatchResult
    existing = AIMatchResult.objects.filter(
        tender_id=tender_id, company_id=company_id
    ).first()
    if not existing:
        return False
    return not (existing.summary or "").startswith(_AI_FAILURE_MARKER)


def _enqueue_opportunity(job: IntelligenceJob) -> None:
    """Create (or re-arm) the OPPORTUNITY_SCORE job after a successful AI Match."""
    with transaction.atomic():
        _, was_created = IntelligenceJob.objects.get_or_create(
            tender_id=job.tender_id,
            company_id=job.company_id,
            job_type="OPPORTUNITY_SCORE",
            defaults={
                "status": "pending",
                "max_attempts": _max_attempts(),
                "scheduled_at": _now(),
            },
        )
        if not was_created:
            IntelligenceJob.objects.filter(
                tender_id=job.tender_id,
                company_id=job.company_id,
                job_type="OPPORTUNITY_SCORE",
                status__in=["failed", "skipped"],
            ).update(status="pending", scheduled_at=_now(), last_error="", started_at=None)


def _skip_opportunity_for(job, reason: str) -> None:
    """Mark any pending OPPORTUNITY_SCORE job for the pair as skipped."""
    IntelligenceJob.objects.filter(
        tender_id=job.tender_id,
        company_id=job.company_id,
        job_type="OPPORTUNITY_SCORE",
    ).exclude(status="completed").update(status="skipped", last_error=reason)


# ---------------------------------------------------------------------------
# CYCLE
# ---------------------------------------------------------------------------

def run_cycle() -> dict:
    """Run one bounded pipeline cycle. Returns a summary dict.

    Never raises — individual job failures are isolated.
    """
    recover_stale_processing()

    batch_size = _batch_size()
    jobs = _claim_jobs(batch_size)
    processed = 0
    for job in jobs:
        try:
            _process_job(job)
        except Exception as exc:
            logger.error("[PIPELINE] Unhandled job #{} failure: {}", job.id, exc)
            _mark(job, "failed", _sanitize(str(exc)))
        processed += 1
    return {"claimed": processed, "batch_size": batch_size}


# ---------------------------------------------------------------------------
# REPROCESS
# ---------------------------------------------------------------------------

def reprocess(tender_id: int, company_id: int) -> dict:
    """Request reprocessing of a tender/company pair.

    Creates/requeues an AI_MATCH job (and OPPORTUNITY_SCORE). Does NOT invoke
    the LLM synchronously — the periodic cycle performs the work.
    """
    from spse_crawler.companies.models import CompanyProfile

    if not CompanyProfile.objects.filter(pk=company_id, is_active=True).exists():
        return {"ok": False, "error": "company_not_found"}

    with transaction.atomic():
        # Force reconsideration: remove stale AI match so a fresh match runs.
        from spse_crawler.ai_match.models import AIMatchResult
        AIMatchResult.objects.filter(
            tender_id=tender_id, company_id=company_id
        ).update(cache_key="")

        now = _now()
        created = 0
        existing = IntelligenceJob.objects.filter(
            tender_id=tender_id, company_id=company_id, job_type="AI_MATCH"
        ).first()
        if existing:
            existing.status = "pending"
            existing.scheduled_at = now
            existing.attempts = 0
            existing.last_error = ""
            existing.started_at = None
            existing.save()
        else:
            IntelligenceJob.objects.create(
                tender_id=tender_id, company_id=company_id, job_type="AI_MATCH",
                status="pending", max_attempts=_max_attempts(), scheduled_at=now,
            )
        created += 1
        # Invalidate/clear OPPORTUNITY_SCORE job too
        IntelligenceJob.objects.filter(
            tender_id=tender_id, company_id=company_id, job_type="OPPORTUNITY_SCORE",
        ).exclude(status="completed").update(status="pending", scheduled_at=now,
                                             started_at=None, last_error="")
    return {"ok": True, "queued": created}


# ---------------------------------------------------------------------------
# OBSERVABILITY
# ---------------------------------------------------------------------------

def status(company_id: int) -> dict:
    """Return pipeline status counts for a company (and daily AI usage)."""
    qs = IntelligenceJob.objects.filter(company_id=company_id)
    counts = {
        "queued": qs.filter(status="pending").count(),
        "processing": qs.filter(status="processing").count(),
        "completed": qs.filter(status="completed").count(),
        "failed": qs.filter(status="failed").count(),
        "skipped": qs.filter(status="skipped").count(),
    }
    return {
        "company_id": company_id,
        "queued": counts["queued"],
        "processing": counts["processing"],
        "completed": counts["completed"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "daily_ai_matches": daily_ai_usage(company_id),
        "daily_ai_limit": _daily_limit(),
    }


def _sanitize(msg: str) -> str:
    """Sanitize error text — never leak API keys or raw provider payloads."""
    msg = str(msg or "")
    for kw in ("sk-", "api_key", "apikey", "authorization", "Bearer "):
        if kw.lower() in msg.lower():
            return "provider_error"
    return msg[:2000]
