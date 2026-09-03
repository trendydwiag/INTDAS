"""Idempotent persistence of tender winner data.

Phase 6B + 6C — Winner Intelligence. The source of truth is the live SPSE
``/evaluasi/{id_lelang}/pemenang`` page, verified during Phase 6B (real captured
HTML across multiple tenders). It exposes:

    Nama Pemenang | Alamat | NPWP | Harga Penawaran | Harga Terkoreksi
    | Harga Negosiasi

Rules (Phase 6C):
- Upsert only on ``tender`` (the ``TenderWinner`` relation is OneToOne).
- Original source identity (``company_name``/``npwp``/``alamat``) is preserved
  as-is; it is never replaced by the CompanyProfile name.
- Auto-linking to ``CompanyProfile`` happens ONLY for an EXACT identity match
  (normalized NPWP / verified official identifier). Winner identity is never
  inferred from participant ordering. Name-only/conflicts are never auto-linked.
- Value semantics are preserved distinctly (Harga Penawaran / Terkoreksi /
  Negosiasi), never collapsed. ``winning_value`` is the final agreed price
  (Harga Negosiasi).
- Records are never fabricated; ``company_name`` must be present to persist.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from spse_crawler.services.entity_resolution import (
    apply_resolution_to,
    mask_identifier,
    resolve_company_identity,
)
from spse_crawler.web.models import TenderWinner

if TYPE_CHECKING:
    from spse_crawler.web.models import TenderResult


def sync_tender_winner(
    tender: "TenderResult",
    winner: dict | None,
    source_url: str = "",
    source_fetched_at: datetime | None = None,
) -> bool:
    """Upsert the winner record for ``tender`` from the parsed /pemenang page.

    Returns True when a winner record was created/updated, False when no winner
    data is available (does nothing — never fabricates, never deletes an
    existing winner on a re-crawl where the page is temporarily unavailable).
    Applies conservative entity resolution to link an EXACT winner match.
    """
    if not winner:
        logger.debug("WINNER_UNAVAILABLE tender_id={} — no winner data", _safe_tender_id(tender))
        return False
    company = (winner.get("company_name") or "").strip()
    if not company:
        logger.debug("WINNER_UNAVAILABLE tender_id={} — empty winner name", _safe_tender_id(tender))
        return False
    obj, _ = TenderWinner.objects.update_or_create(
        tender=tender,
        defaults={
            "company_name": company,
            "npwp": (winner.get("npwp") or "").strip(),
            "alamat": (winner.get("alamat") or "").strip(),
            "harga_penawaran": winner.get("harga_penawaran"),
            "harga_terkoreksi": winner.get("harga_terkoreksi"),
            "harga_negosiasi": winner.get("harga_negosiasi"),
            "winning_value": winner.get("winning_value"),
            "source_url": source_url,
            "source_fetched_at": source_fetched_at,
        },
    )
    result = resolve_company_identity(name=company, npwp=winner.get("npwp"))
    if apply_resolution_to(obj, result):
        obj.save(update_fields=["company", "resolution_status", "updated_at"])
    _log_winner_stored(obj, result)
    return True


def _safe_tender_id(tender) -> str:
    """Return a safe identifier for logging (never log fetched source values)."""
    return str(getattr(tender, "id_lelang", "") or getattr(tender, "pk", ""))


def _log_winner_stored(obj, result) -> None:
    logger.info(
        "WINNER_STORED tender_id={} name={} npwp_masked={} status={}",
        _safe_tender_id(obj.tender),
        obj.company_name,
        mask_identifier(obj.npwp),
        obj.resolution_status or result.status.value,
    )
