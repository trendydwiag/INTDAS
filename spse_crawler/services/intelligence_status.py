"""Intelligence status / readiness for a company — read-only, deterministic.

Answers: does the system already have enough persisted intelligence data to
produce a meaningful Tender Radar / Opportunity Intelligence for the company?

Pure read model. NEVER:
  - calls an AI provider / LLM
  - creates AIMatchResult
  - creates OpportunityScore
  - mutates any row
  - scores anything

Uses efficient aggregate COUNT queries (no N+1).
"""

from __future__ import annotations

from django.db.models import Q as DQ

from spse_crawler.ai_match.models import AIMatchResult
from spse_crawler.web.models import OpportunityScore, TenderResult

# Tender stages considered terminal (closed / not worth analyzing).
_TERMINAL_KEYWORDS = ("selesai", "kontrak", "pembatalan", "dibatalkan", "gugur")


def _active_tenders_qs():
    deny = DQ(tahap_saat_ini__icontains="selesai") | DQ(
        tahap_saat_ini__icontains="kontrak"
    ) | DQ(tahap_saat_ini__icontains="pembatalan") | DQ(
        tahap_saat_ini__icontains="dibatalkan"
    ) | DQ(tahap_saat_ini__icontains="gugur")
    return TenderResult.objects.exclude(deny)


def _pct(part: int, whole: int) -> int:
    if whole <= 0:
        return 0
    return int(round(part * 100.0 / whole))


def _readiness_level(
    tender_count: int,
    ai_count: int,
    ready_count: int,
    ai_pct: int,
    opp_pct: int,
) -> str:
    """Derive the readiness level from persisted counts and coverage.

    NOT_READY -> no analyzable tender, OR no AI match at all, OR no ready score.
    READY     -> tender + AI match + ready score present AND full coverage.
    PARTIAL   -> tender + AI match + ready score present but coverage incomplete.
    """
    if tender_count == 0 or ai_count == 0 or ready_count == 0:
        return "NOT_READY"
    if ai_pct >= 100 and opp_pct >= 100:
        return "READY"
    return "PARTIAL"


def compute_status(company_id: int) -> dict:
    """Compute the intelligence readiness status for a company."""
    active_ids = _active_tenders_qs().values_list("id", flat=True)
    tender_ids = list(active_ids)

    tender_count = len(tender_ids)

    # AI matches for this company, restricted to active tenders.
    ai_matches = AIMatchResult.objects.filter(
        company_id=company_id, tender_id__in=tender_ids
    ).count() if tender_ids else 0

    # Opportunity scores for this company, restricted to active tenders.
    opp_qs = OpportunityScore.objects.filter(company_id=company_id)
    if tender_ids:
        opp_qs = opp_qs.filter(tender_id__in=tender_ids)
    opportunity_scores = opp_qs.count()
    ready_opportunities = opp_qs.filter(status="ready").count()

    ai_match_percent = _pct(ai_matches, tender_count)
    opportunity_score_percent = _pct(ready_opportunities, tender_count)
    level = _readiness_level(
        tender_count, ai_matches, ready_opportunities,
        ai_match_percent, opportunity_score_percent,
    )

    ready = level == "READY"

    message = _message(level, tender_count, ai_matches, ready_opportunities)

    return {
        "company_id": company_id,
        "readiness": {
            "ready": ready,
            "level": level,
        },
        "counts": {
            "tenders": tender_count,
            "ai_matches": ai_matches,
            "opportunity_scores": opportunity_scores,
            "ready_opportunities": ready_opportunities,
        },
        "coverage": {
            "ai_match_percent": ai_match_percent,
            "opportunity_score_percent": opportunity_score_percent,
        },
        "message": message,
    }


def _message(level: str, tenders: int, ai_matches: int, ready: int) -> str:
    if level == "READY":
        return (
            f"Pipeline siap: {ready} peluang layak tersedia untuk {tenders} tender aktif."
        )
    if level == "NOT_READY":
        if tenders == 0:
            return "Belum ada tender aktif yang dapat dianalisis."
        if ai_matches == 0:
            return "Belum ada analisis AI. Jalankan AI Match untuk tender aktif."
        return "Belum ada skor peluang yang siap. Selesaikan Opportunity Score."
    # PARTIAL
    if ready == 0:
        return (
            f"Analisis AI tersedia untuk {ai_matches}/{tenders} tender, "
            "namun belum ada Opportunity Score yang siap."
        )
    return (
        f"Pipeline sebagian siap: {ready} peluang siap dari {tenders} tender aktif."
    )
