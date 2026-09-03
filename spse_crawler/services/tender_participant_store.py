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
        if was_created:
            created += 1
        else:
            updated += 1
    logger.info(
        "PARTICIPANT_SYNC tender_id={} created={} updated={} total={}",
        _safe_tender_id(tender), created, updated,
        len(participants or []),
    )
    return SyncStats(created=created, updated=updated)


def _safe_tender_id(tender) -> str:
    """Return a safe identifier for logging (never log fetched source values)."""
    return str(getattr(tender, "id_lelang", "") or getattr(tender, "pk", ""))
