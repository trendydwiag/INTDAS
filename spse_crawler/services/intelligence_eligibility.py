"""Deterministic eligibility pre-filter for the intelligence pipeline.

Decides whether a tender should be sent to AI Match for a given company.
Uses ONLY reliable, already-persisted TenderResult / CompanyProfile data.

Never invokes an LLM. If a tender cannot be meaningfully analyzed, it is
reported as ineligible with a machine-readable and human-readable reason.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

# Terminal tender stages — never eligible for AI analysis.
TERMINAL_STAGE_KEYWORDS = (
    "selesai",
    "kontrak",
    "pembatalan",
    "dibatalkan",
    "gugur",
)

# Minimum length of usable requirement/detail text before a tender is
# considered analyzable. A tender with no text cannot be matched.
MIN_USABLE_TEXT_LENGTH = 10


def _extract_deadline_date(tender) -> date | None:
    """Return the tender's effective deadline date from jadwal_json.

    Prefers the most recent 'sampai' date across the schedule.
    Returns None if unavailable.
    """
    try:
        jadwal = tender.jadwal_json or []
    except Exception:
        jadwal = []
    if not isinstance(jadwal, list):
        return None

    candidates: list[date] = []
    from datetime import datetime

    for stage in jadwal:
        if not isinstance(stage, dict):
            continue
        raw = stage.get("sampai") or stage.get("mulai")
        if not raw:
            continue
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                candidates.append(datetime.strptime(str(raw), fmt).date())
                break
            except (ValueError, TypeError):
                continue
    if not candidates:
        return None
    return max(candidates)


def usable_requirement_text(tender) -> str:
    """Return the best available requirement/detail text for eligibility.

    Priority: requirement_text, syarat_kualifikasi, nama_paket.
    """
    for field in ("requirement_text", "syarat_kualifikasi", "nama_paket"):
        val = getattr(tender, field, "") or ""
        if len(val.strip()) >= MIN_USABLE_TEXT_LENGTH:
            return val.strip()
    return ""


def is_terminal_stage(tender) -> bool:
    """Whether the tender is in a terminal (closed/irrelevant) stage."""
    tahap = (getattr(tender, "tahap_saat_ini", "") or "").lower()
    return any(kw in tahap for kw in TERMINAL_STAGE_KEYWORDS)


def is_expired(tender, today: date | None = None) -> bool:
    """Whether the tender deadline has passed."""
    dl = _extract_deadline_date(tender)
    if dl is None:
        return False  # no deadline data -> cannot determine expiry -> not expired
    today = today or timezone.localdate()
    return dl < today


def is_eligible(tender, company) -> tuple[bool, str | None, str | None]:
    """Determine whether *tender* is eligible for AI Match for *company*.

    Returns (eligible, skip_reason, error_reason):
      - eligible True  -> proceed with AI Match
      - eligible False with skip_reason -> deterministic SKIP (insufficient/inactive data)
      - eligible False with error_reason -> invalid input (missing company/tender)
    """
    from spse_crawler.companies.models import CompanyProfile

    if company is None or not isinstance(company, CompanyProfile):
        return False, None, "missing_company"
    if not getattr(company, "is_active", True):
        return False, "company_inactive", None
    if not company.kbli_codes_from_quals:
        return False, "company_no_qualifications", None

    if tender is None:
        return False, None, "missing_tender"

    if is_terminal_stage(tender):
        return False, "insufficient_tender_data", None

    if not usable_requirement_text(tender):
        return False, "insufficient_tender_data", None

    if is_expired(tender):
        return False, "insufficient_tender_data", None

    return True, None, None


def eligibility_summary(tender, company) -> dict:
    """Return a structured eligibility summary (for observability)."""
    eligible, skip_reason, error_reason = is_eligible(tender, company)
    return {
        "eligible": eligible,
        "skip_reason": skip_reason,
        "error_reason": error_reason,
        "terminal": is_terminal_stage(tender) if tender else None,
        "expired": is_expired(tender) if tender else None,
        "usable_text_len": len(usable_requirement_text(tender)) if tender else 0,
        "company_active": bool(getattr(company, "is_active", True)) if company else None,
        "company_kbli_count": len(company.kbli_codes_from_quals) if company else 0,
    }
