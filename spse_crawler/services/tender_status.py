"""Canonical tender status definitions and normalization helpers.

Enforces the critical business rule:
  ONLY tenders with status 'Pengumuman Prakualifikasi' (case-insensitive,
  presentation artifacts stripped) are considered ACTIVE and SUBMITTABLE.

All other tender stages (Evaluasi Administrasi, Evaluasi Teknis, Pengumuman
Pemenang, Kontrak, Selesai, Batal, Gagal, etc.) are excluded from active
intelligence processing (AI Match, Opportunity Score, Tender Radar).
"""

from __future__ import annotations

import re
from typing import Any

# Canonical active status constant
CANONICAL_ACTIVE_STATUS: str = "pengumuman prakualifikasi"


def normalize_tahap(tahap: Any) -> str:
    """Normalize a tender stage string deterministically.

    Steps:
      1. Handle None / non-string safely
      2. Convert to string and trim leading/trailing whitespace
      3. Lowercase
      4. Strip SPSE DataTables presentation artifact: '[...]'
      5. Collapse consecutive whitespaces into a single space

    Examples:
      'Pengumuman Prakualifikasi'         -> 'pengumuman prakualifikasi'
      'pengumuman prakualifikasi [...]'   -> 'pengumuman prakualifikasi'
      '  pengumuman prakualifikasi [...] '-> 'pengumuman prakualifikasi'
      'Pengumuman Hasil Prakualifikasi'   -> 'pengumuman hasil prakualifikasi'
      'Evaluasi Administrasi'             -> 'evaluasi administrasi'
    """
    if tahap is None:
        return ""
    s = str(tahap).strip().lower()
    if not s:
        return ""
    # Strip presentation artifact [...]
    s = s.replace("[...]", "").strip()
    # Collapse multiple whitespaces
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def is_submittable_tender(tender_or_tahap: Any) -> bool:
    """Check whether a tender or raw tahap string represents an active/submittable tender.

    Strict equality against CANONICAL_ACTIVE_STATUS after normalization.
    NO fuzzy matching (no substring/startswith).
    Only 'pengumuman prakualifikasi' returns True.
    """
    if tender_or_tahap is None:
        return False

    if isinstance(tender_or_tahap, str):
        raw_tahap = tender_or_tahap
    else:
        raw_tahap = getattr(tender_or_tahap, "tahap_saat_ini", None)

    if not raw_tahap:
        return False

    return normalize_tahap(raw_tahap) == CANONICAL_ACTIVE_STATUS
