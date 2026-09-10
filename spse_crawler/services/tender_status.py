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

# Canonical active status constant (retained for backward-compatibility)
CANONICAL_ACTIVE_STATUS: str = "pengumuman prakualifikasi"

# Canonical submittable stage categories (active submission window)
SUBMITTABLE_STAGE_KEYWORDS: tuple[str, ...] = (
    # 1. Pengumuman
    "pengumuman prakualifikasi",
    "pengumuman pra-kualifikasi",
    "pengumuman kualifikasi",
    "pengumuman pemilihan",
    # 2. Download / Unduh Dokumen
    "download dokumen kualifikasi",
    "download dokumen pemilihan",
    "download dokumen prakualifikasi",
    "unduh dokumen kualifikasi",
    "unduh dokumen pemilihan",
    "unduh dokumen prakualifikasi",
    "pengambilan dokumen kualifikasi",
    "pengambilan dokumen pemilihan",
    # 3. Penjelasan Dokumen (Aanwijzing)
    "penjelasan dokumen prakualifikasi",
    "penjelasan dokumen kualifikasi",
    "penjelasan dokumen pemilihan",
    "pemberian penjelasan",
    "aanwijzing",
    # 4. Kirim Persyaratan / Data / Pemasukan Dokumen
    "kirim persyaratan kualifikasi",
    "kirim data kualifikasi",
    "kirim dokumen kualifikasi",
    "kirim penawaran",
    "pemasukan dokumen kualifikasi",
    "pemasukan kualifikasi",
    "pemasukan data kualifikasi",
    "pemasukan dokumen penawaran",
    "penyampaian dokumen kualifikasi",
    "penyampaian kualifikasi",
    "penyampaian data kualifikasi",
    "upload dokumen kualifikasi",
    "upload dokumen penawaran",
)

# Stages that are past submission window or closed/aborted
NON_SUBMITTABLE_KEYWORDS: tuple[str, ...] = (
    "evaluasi",
    "pembuktian",
    "hasil",
    "sanggah",
    "pemenang",
    "kontrak",
    "selesai",
    "batal",
    "gagal",
    "lelang ulang",
    "tender ulang",
)


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

    Submittable stages include:
      1. Pengumuman Prakualifikasi / Pemilihan
      2. Download / Unduh Dokumen Kualifikasi / Dokumen Pemilihan
      3. Penjelasan Dokumen (Pemberian Penjelasan / Aanwijzing)
      4. Kirim Persyaratan / Data Kualifikasi / Pemasukan Dokumen Kualifikasi

    Excluded stages include:
      Evaluasi, Pembuktian Kualifikasi, Pengumuman Hasil, Sanggahan,
      Penetapan/Pengumuman Pemenang, Kontrak, Selesai, Batal, Gagal.
    """
    if tender_or_tahap is None:
        return False

    if isinstance(tender_or_tahap, str):
        raw_tahap = tender_or_tahap
    else:
        raw_tahap = getattr(tender_or_tahap, "tahap_saat_ini", None)

    if not raw_tahap:
        return False

    norm = normalize_tahap(raw_tahap)
    if not norm:
        return False

    # Hard rejection: excluded stages (evaluasi, pemenang, kontrak, selesai, batal, etc.)
    if any(kw in norm for kw in NON_SUBMITTABLE_KEYWORDS):
        return False

    # Check if normalized tahap matches any submittable stage keywords
    return any(kw in norm for kw in SUBMITTABLE_STAGE_KEYWORDS)
