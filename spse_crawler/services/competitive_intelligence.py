"""Competitive & Winner Intelligence — read-only foundation.

Phase 4 foundation. Provides read-only, deterministic lookups over the
*already persisted* tender data to expose whatever competitive/historical
information is genuinely available, and honestly reports gaps.

Data availability (from current schema, see TenderResult):
  AVAILABLE
    - TenderResult.peserta_count   -> reliable participant COUNT per tender.
    - TenderResult.participants    -> participant company name + NPWP, captured
      from the /peserta page (Phase 5 TenderParticipant), external identity only.
    - TenderResult metadata        -> instansi, kbli, lokasi, nama_paket, hps,
                                      tahap_saat_ini, tanggal_dibuat, etc.
  NOT AVAILABLE FROM CURRENT DATA
    - Winner / pemenang identity and winning value  (no parser; the awarded
      page HTML is not demonstrably parseable from this repo — see
      TenderWinner foundation, which is intentionally not populated).
    - A TenderResult <-> CompanyProfile relationship representing actual
      participation/wins (participant identity is external "name + NPWP" and is
      NOT auto-linked to a tenant CompanyProfile — entity resolution deferred).

This module NEVER:
  - calls an AI provider / LLM
  - mutates any row
  - runs the crawler
  - creates OpportunityScore / AIMatchResult / any new data as a side effect
  - fabricates or infers participation / wins / winning price

Any aggregate that cannot be backed by persisted rows is returned as `null`
(or omitted) and the response carries a `data_available` / `not_available`
flag so callers never mistake gaps for real zero values.
"""

from __future__ import annotations

from collections import Counter

from spse_crawler.companies.models import CompanyProfile
from spse_crawler.web.models import TenderResult


# ---------------------------------------------------------------------------
# Company Intelligence
# ---------------------------------------------------------------------------

def get_company_intelligence(company_id: int) -> dict:
    """Return read-only historical intelligence for a CompanyProfile.

    Backed by the persisted, conservatively-linked participant/winner rows
    (Phase 6C). Metrics reflect ONLY rows actually captured:
      - total_participation = count of linked participations
      - total_wins = count of linked wins
      - total_winning_value = sum of captured winning values
      - win_rate = total_wins / total_participation (basis documented; reflects
        only the crawled/retained set, never the full universe)

    When no participation/winner rows exist, values stay ``None`` with
    ``data_available = False`` (NOT_AVAILABLE is preserved). Frequencies and
    recency lists are never fabricated.
    """
    try:
        company = CompanyProfile.objects.get(pk=company_id)
    except CompanyProfile.DoesNotExist:
        return {
            "status": "not_found",
            "company": None,
            "statistics": None,
            "recent_tenders": [],
            "recent_wins": [],
        }

    participations = list(
        company.participations.select_related("tender").order_by("-tender__scraped_at")
    )
    wins = list(company.wins.select_related("tender").order_by("-created_at"))

    total_participation = len(participations)
    total_wins = len(wins)
    total_winning_value = sum(w.winning_value or 0 for w in wins)

    data_available = total_participation > 0 or total_wins > 0

    # Recency lists (real rows only).
    recent_tenders = []
    for p in participations[:10]:
        t = p.tender
        recent_tenders.append({
            "tender_id": t.id_lelang,
            "nama_paket": t.nama_paket,
            "instansi": t.instansi,
            "kode_instansi": t.kode_instansi,
            "lokasi": t.lokasi_pekerjaan,
            "kbli_code": t.kbli_code,
            "tahap": t.tahap_saat_ini,
            "hps": t.hps,
            "tanggal_dibuat": t.tanggal_dibuat.isoformat() if t.tanggal_dibuat else None,
        })
    recent_wins = []
    for w in wins[:10]:
        t = w.tender
        recent_wins.append({
            "tender_id": t.id_lelang,
            "nama_paket": t.nama_paket,
            "instansi": t.instansi,
            "winning_value": w.winning_value,
            "harga_negosiasi": w.harga_negosiasi,
            "company_name": w.company_name,
            "winning_date": w.created_at.isoformat() if w.created_at else None,
        })

    # Frequencies over the observed participation universe.
    inst_counter: Counter = Counter()
    loc_counter: Counter = Counter()
    kbli_counter: Counter = Counter()
    for p in participations:
        t = p.tender
        inst_key = (t.instansi or "").strip() or (t.kode_instansi or "").strip()
        if inst_key:
            inst_counter[inst_key] += 1
        loc = (t.lokasi_pekerjaan or "").strip()
        if loc:
            loc_counter[loc] += 1
        kbli = (t.kbli_code or "").strip()
        if kbli:
            kbli_counter[kbli] += 1

    institution_frequency = [
        {"instansi": k, "count": c} for k, c in inst_counter.most_common(20)
    ]
    location_frequency = [
        {"location": k, "count": c} for k, c in loc_counter.most_common(20)
    ]
    kbli_frequency = [
        {"kbli_code": k, "count": c} for k, c in kbli_counter.most_common(20)
    ]

    win_rate = (
        round(total_wins / total_participation, 4)
        if total_participation > 0 else None
    )

    if not data_available:
        statistics = {
            "total_participation": None,
            "total_wins": None,
            "total_winning_value": None,
            "win_rate": None,
            "win_rate_basis": "wins / captured participations (unavailable)",
        }
        coverage = {
            "winner_coverage": None,
            "participation_coverage": None,
            "winner_coverage_basis": (
                "no captured participation/winner rows for this company"
            ),
        }
        limitation = (
            "NOT AVAILABLE FROM CURRENT DATA — no participant/winner records "
            "are persisted and linked to this company"
        )
    else:
        statistics = {
            "total_participation": total_participation,
            "total_wins": total_wins,
            "total_winning_value": total_winning_value,
            "win_rate": win_rate,
            "win_rate_basis": (
                "wins / captured participations; reflects only the crawled/retained "
                "subset, not the full tender universe"
            ),
        }
        coverage = {
            "winner_coverage": win_rate,
            "participation_coverage": total_participation,
            "winner_coverage_basis": (
                "fraction of captured participations that resulted in a captured win; "
                "coverage is limited to tenders where winner data was actually persisted"
            ),
        }
        limitation = (
            "Company intelligence is derived from captured participant/winner rows "
            "linked conservatively by EXACT identity only. Win-rate and coverage "
            "reflect the crawled subset, never the complete historical universe."
        )

    return {
        "status": "ok",
        "company": {
            "id": company.id,
            "name": company.name,
            "nib": company.nib,
            "npwp": company.npwp,
        },
        "statistics": statistics,
        "data_available": data_available,
        "coverage": coverage,
        "recent_tenders": recent_tenders,
        "recent_wins": recent_wins,
        "institution_frequency": institution_frequency,
        "location_frequency": location_frequency,
        "kbli_frequency": kbli_frequency,
        "data_limitation": limitation,
    }


# ---------------------------------------------------------------------------
# Winner Intelligence
# ---------------------------------------------------------------------------

def get_tender_winner(tender_id: int) -> dict:
    """Return the winner for a tender, if reliably persisted.

    Winner data comes from the verified SPSE ``/evaluasi/{id}/pemenang`` source
    (Phase 6B) when a ``TenderWinner`` row was captured. If no winner row is
    persisted this is ``not_available`` (never fabricated). The winner's source
    identity (``company_name``/``npwp``/values) is returned as-is, alongside a
    ``resolution_status`` describing any conservative ``CompanyProfile`` link.
    """
    try:
        tender = TenderResult.objects.get(pk=tender_id)
    except TenderResult.DoesNotExist:
        return {
            "status": "not_found",
            "tender_id": tender_id,
            "winner": None,
        }

    if not hasattr(tender, "winner") or tender.winner is None:
        return {
            "status": "not_available",
            "tender_id": tender_id,
            "winner": None,
            "data_limitation": (
                "NOT AVAILABLE FROM CURRENT DATA — no winner record is persisted "
                "for this tender"
            ),
        }

    w = tender.winner
    return {
        "status": "ok",
        "tender_id": tender_id,
        "winner": {
            "company_name": w.company_name,
            "npwp": w.npwp,
            "alamat": w.alamat,
            "winning_value": w.winning_value,
            "harga_penawaran": w.harga_penawaran,
            "harga_terkoreksi": w.harga_terkoreksi,
            "harga_negosiasi": w.harga_negosiasi,
            "resolution_status": w.resolution_status,
            "resolved_company_id": w.company_id,
            "source_url": w.source_url,
        },
    }


# ---------------------------------------------------------------------------
# Tender Competition Summary
# ---------------------------------------------------------------------------

def get_tender_competition(tender_id: int) -> dict:
    """Return the participant count and names for a tender.

    The participant COUNT (TenderResult.peserta_count) is reliable and real.
    Participant NAMES come from persisted ``TenderParticipant`` rows captured
    from the /peserta page (Phase 5); when none are recorded they are returned
    empty (never fabricated). When no participant count is recorded, returns
    not_available rather than a guessed zero.
    """
    try:
        tender = TenderResult.objects.get(pk=tender_id)
    except TenderResult.DoesNotExist:
        return {
            "status": "not_found",
            "tender_id": tender_id,
            "participant_count": None,
            "participants": [],
        }

    count = tender.peserta_count
    if count <= 0:
        return {
            "status": "not_available",
            "tender_id": tender_id,
            "participant_count": None,
            "participants": [],
            "data_limitation": (
                "NOT AVAILABLE FROM CURRENT DATA — no participant count recorded"
            ),
        }

    participants = list(
        tender.participants.order_by("name").values("name", "npwp")
    )
    return {
        "status": "ok",
        "tender_id": tender_id,
        "participant_count": int(count),
        "participants": participants,
        "data_note": (
            "participant names reflect persisted /peserta capture (empty until "
            "the tender has been enriched)"
            if not participants
            else "participant names from /peserta page capture"
        ),
    }
