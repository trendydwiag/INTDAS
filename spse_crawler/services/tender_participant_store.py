"""Idempotent persistence of tender participant data.

Phase 5 + 6C — Competitive Intelligence data foundation. This store reconciles
the normalized ``TenderParticipant`` collection for a tender against the
participant list produced by the crawler's ``/peserta`` parser, and applies
conservative entity resolution to link a participant to a ``CompanyProfile``.

Rules (Phase 6C):
- Upsert only. Existing historical records are never deleted on re-crawl.
- Missing/unavailable participant data on a re-crawl does NOT wipe records that
  were persisted earlier.
- Original source identity (``name``/``npwp``) is preserved as-is; it is never
  replaced by the CompanyProfile name.
- Auto-linking to ``CompanyProfile`` happens ONLY for an EXACT identity match
  (normalized NPWP / verified official identifier). Name-only matches are
  recorded as ``CANDIDATE_ONLY`` and are NEVER auto-linked. Conflicts are never
  auto-/silently merged.
- Never deletes/merges companies; never invents identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from spse_crawler.services.entity_resolution import (
    apply_resolution_to,
    resolve_company_identity,
)

if TYPE_CHECKING:
    from spse_crawler.web.models import TenderResult


@dataclass(frozen=True)
class SyncStats:
    """Outcome of a participant sync."""

    created: int
    updated: int


def sync_tender_participants(
    tender: "TenderResult",
    participants: list[dict],
    source_url: str = "",
    source_fetched_at: datetime | None = None,
) -> SyncStats:
    """Upsert the normalized participant rows for ``tender``.

    Idempotency key is ``(tender, name)``. Rows with an empty name are skipped
    (junk-avoidance). Re-crawls update ``npwp``/provenance and never delete
    previously stored participants (unavailable data cannot destroy history).
    Each participant is conservatively resolved to a ``CompanyProfile`` (EXACT
    match only; name-only/conflict are never auto-linked).
    """
    created = 0
    updated = 0
    for p in participants or []:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        obj, was_created = tender.participants.update_or_create(
            name=name,
            defaults={
                "npwp": (p.get("npwp") or "").strip(),
                "source_url": source_url,
                "source_fetched_at": source_fetched_at,
            },
        )
        result = resolve_company_identity(name=name, npwp=p.get("npwp"))
        if apply_resolution_to(obj, result):
            obj.save(update_fields=["company", "resolution_status", "updated_at"])
        if getattr(tender, "pk", None) and obj.company:
            try:
                _sync_participant_submission_and_watchlist(tender, obj.company, name, p)
            except Exception as e:
                logger.warning(
                    "PARTICIPANT_AUTO_WATCHLIST_FAIL tender_id={} company_id={} error={}",
                    _safe_tender_id(tender),
                    obj.company_id,
                    e,
                )
        if was_created:
            created += 1
        else:
            updated += 1
    logger.info(
        "PARTICIPANT_SYNC tender_id={} created={} updated={} total={}",
        _safe_tender_id(tender),
        created,
        updated,
        len(participants or []),
    )
    return SyncStats(created=created, updated=updated)


def _sync_participant_submission_and_watchlist(
    tender: "TenderResult",
    company,
    participant_name: str,
    p_data: dict,
) -> None:
    """Auto-add matched company to TenderWatchlist and TenderSubmissionStatus."""
    from spse_crawler.web.models_watchlist import TenderWatchlist
    from spse_crawler.submissions.models import TenderSubmissionStatus
    from spse_crawler.services.entity_resolution import (
        normalize_company_name,
        normalize_npwp,
        matches_masked_npwp,
    )

    alasan = (p_data.get("alasan") or "").strip()
    alasan_lower = alasan.lower()

    # Detect failure keywords in evaluation / alasan
    is_failed = any(
        kw in alasan_lower
        for kw in [
            "tidak lulus",
            "gugur",
            "tidak memenuhi",
            "ambang batas",
            "diskualifikasi",
            "batal",
        ]
    )

    # Check winner status
    winner = getattr(tender, "winner", None)
    is_winner = False
    if winner:
        if winner.company_id == company.id:
            is_winner = True
        elif winner.nama_pemenang and normalize_company_name(winner.nama_pemenang) == normalize_company_name(company.name):
            is_winner = True
        elif winner.npwp and company.npwp and (
            normalize_npwp(winner.npwp) == normalize_npwp(company.npwp)
            or matches_masked_npwp(winner.npwp, company.npwp)
        ):
            is_winner = True

    if is_failed:
        sub_status = "gagal"
        sub_notes = alasan
    elif is_winner:
        sub_status = "menang"
        sub_notes = f"Pemenang lelang ({winner.nama_pemenang or company.name})"
    else:
        sub_status = "sudah_submit"
        sub_notes = alasan or "Terdaftar sebagai peserta lelang di SPSE."

    user = company.users.first() if hasattr(company, "users") else None

    # 1. TenderWatchlist: auto-add if not already in watchlist
    TenderWatchlist.objects.get_or_create(
        company=company,
        tender=tender,
        defaults={
            "user": user,
            "notes": f"Otomatis masuk Watchlist dari peserta SPSE ({sub_status}).",
        },
    )

    # 2. TenderSubmissionStatus: do not downgrade existing 'menang'
    existing_sub = TenderSubmissionStatus.objects.filter(tender=tender, company=company).first()
    if existing_sub and existing_sub.status == "menang" and sub_status == "sudah_submit":
        sub_status = "menang"

    TenderSubmissionStatus.objects.update_or_create(
        tender=tender,
        company=company,
        defaults={
            "status": sub_status,
            "notes": sub_notes,
            "updated_by": user,
        },
    )


def sync_participant_submissions_for_tender(tender: "TenderResult") -> int:
    """Evaluate and sync submissions + watchlists for all participants of a tender."""
    count = 0
    for p in tender.participants.all():
        if not p.company_id:
            res = resolve_company_identity(name=p.name, npwp=p.npwp)
            if apply_resolution_to(p, res):
                p.save(update_fields=["company", "resolution_status", "updated_at"])
        if p.company:
            _sync_participant_submission_and_watchlist(
                tender=tender,
                company=p.company,
                participant_name=p.name,
                p_data={"name": p.name, "npwp": p.npwp},
            )
            count += 1
    return count


def _safe_tender_id(tender) -> str:
    """Return a safe identifier for logging (never log fetched source values)."""
    return str(getattr(tender, "id_lelang", "") or getattr(tender, "pk", ""))
