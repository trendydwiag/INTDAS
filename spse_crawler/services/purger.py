"""Database purger — removes expired / inactive tender records.

Modes:
  - non_prakualifikasi: delete records where is_prakualifikasi=False
  - inactive: delete records with Selesai / Dikontrak / Batal / Gagal status
  - all: wipe everything (full reset)
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from loguru import logger

from spse_crawler.web.models import TenderResult


@dataclass
class FlushResult:
    mode: str
    deleted_count: int
    remaining_count: int


_INACTIVE_TAHA_PATTERNS: tuple[str, ...] = (
    "selesai",
    "kontrak",
    "penandatanganan",
    "pembatalan",
    "batal",
    "gagal",
)


def flush_non_prakualifikasi() -> FlushResult:
    """Delete all records that are NOT prakualifikasi (inactive / old data)."""
    qs = TenderResult.objects.filter(is_prakualifikasi=False)
    count = qs.count()
    qs.delete()
    remaining = TenderResult.objects.count()
    logger.info("[PURGE] non_prakualifikasi: deleted {}, remaining {}", count, remaining)
    return FlushResult(mode="non_prakualifikasi", deleted_count=count, remaining_count=remaining)


def flush_inactive() -> FlushResult:
    """Delete records whose tahap indicates finished / contracted / cancelled."""
    condition = Q()
    for pattern in _INACTIVE_TAHA_PATTERNS:
        condition |= Q(tahap_saat_ini__icontains=pattern)
    qs = TenderResult.objects.filter(condition)
    count = qs.count()
    qs.delete()
    remaining = TenderResult.objects.count()
    logger.info("[PURGE] inactive: deleted {}, remaining {}", count, remaining)
    return FlushResult(mode="inactive", deleted_count=count, remaining_count=remaining)


def flush_non_retained() -> FlushResult:
    """History-safe auto-purge: delete only records that are neither active nor
    retained-awarded, so historical awarded/decided/completed tenders (and active
    tenders) survive recurring crawler execution.

    Semantics (aligned with the Stage 2 retention gate in ``DetailParser``):
      - Active prakualifikasi tenders (``is_prakualifikasi=True``) are KEPT.
      - Active non-prakualifikasi tenders whose ``tahap_saat_ini`` matches an
        eligible (accept) keyword are KEPT.
      - Awarded / decided / completed tenders whose ``tahap_saat_ini`` matches an
        awarded keyword are KEPT.
      - Only remaining records (aborted / cancelled / failed / unrecognised-status
        stale rows that are non-prakualifikasi) are deleted.

    Deleting a tender cascades to its participants/winner/matches/scores, so this
    function intentionally never targets retained tenders.
    """
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.submissions.models import TenderSubmissionStatus

    retained_condition = Q()
    for kw in DetailParser._TAHAP_ACCEPT_KEYWORDS:
        retained_condition |= Q(tahap_saat_ini__icontains=kw)
    for kw in DetailParser._TAHAP_AWARDED_KEYWORDS:
        retained_condition |= Q(tahap_saat_ini__icontains=kw)

    exempt_tender_ids = set(
        TenderSubmissionStatus.objects.filter(
            status__in=_SUBMITTED_STATUSES,
        ).values_list("tender_id", flat=True)
    )

    qs = (
        TenderResult.objects.filter(is_prakualifikasi=False)
        .exclude(retained_condition)
        .exclude(id__in=exempt_tender_ids)
    )
    count = qs.count()
    qs.delete()
    remaining = TenderResult.objects.count()
    logger.info("[PURGE] non_retained: deleted {}, remaining {}", count, remaining)
    return FlushResult(mode="non_retained", deleted_count=count, remaining_count=remaining)


def flush_all() -> FlushResult:
    """Delete ALL records — full database reset."""
    count = TenderResult.objects.count()
    TenderResult.objects.all().delete()
    remaining = TenderResult.objects.count()
    logger.info("[PURGE] all: deleted {}, remaining {}", count, remaining)
    return FlushResult(mode="all", deleted_count=count, remaining_count=remaining)


# Keywords that mark a tender as COMPLETED / NON-ACTIONABLE.
_COMPLETED_TAHAP_PATTERNS: tuple[str, ...] = (
    "selesai",
    "tender selesai",
    "pascakualifikasi",
    "penandatanganan",
    "penandatanganan kontrak",
)

# Submission statuses that indicate the user has actively engaged with
# a tender — these tenders are EXEMPT from completed-tender purging.
_SUBMITTED_STATUSES: tuple[str, ...] = (
    "sudah_submit",
    "menang",
    "cocok_diproses",
)


def purge_completed_tenders() -> FlushResult:
    """Remove completed/finished tenders that have no active submission.

    A tender with ``tahap_saat_ini`` containing "selesai" is considered
    completed and therefore un-actionable.  However, if the user's company
    has already submitted (``TenderSubmissionStatus`` with status
    ``sudah_submit`` or ``menang``), the tender is EXEMPT and kept for
    monitoring purposes.

    This prevents the database from accumulating garbage data — completed
    tenders that can no longer be participated in.
    """
    from spse_crawler.submissions.models import TenderSubmissionStatus

    # Build "completed" condition
    completed_q = Q()
    for pattern in _COMPLETED_TAHAP_PATTERNS:
        completed_q |= Q(tahap_saat_ini__icontains=pattern)

    # Find tender IDs that have an active submission (exempt from purge)
    exempt_tender_ids = set(
        TenderSubmissionStatus.objects.filter(
            status__in=_SUBMITTED_STATUSES,
        ).values_list("tender_id", flat=True)
    )

    qs = TenderResult.objects.filter(completed_q).exclude(id__in=exempt_tender_ids)
    count = qs.count()
    qs.delete()
    remaining = TenderResult.objects.count()
    logger.info(
        "[PURGE] completed: deleted {} (exempted {} with submissions), remaining {}",
        count, len(exempt_tender_ids), remaining,
    )
    return FlushResult(mode="completed", deleted_count=count, remaining_count=remaining)


def flush(mode: str = "non_prakualifikasi") -> FlushResult:
    """Dispatch to the correct flush function based on mode."""
    if mode == "non_prakualifikasi":
        return flush_non_prakualifikasi()
    elif mode == "inactive":
        return flush_inactive()
    elif mode == "completed":
        return purge_completed_tenders()
    elif mode == "all":
        return flush_all()
    else:
        raise ValueError(f"Unknown flush mode: {mode!r}. Use 'non_prakualifikasi', 'inactive', 'completed', or 'all'.")
