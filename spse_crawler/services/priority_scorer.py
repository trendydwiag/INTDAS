"""IT Tender Priority Scoring engine.

Auto-tags tenders as IT priority using a three-phase evaluation:

1. KBLI check: if the package has a proven IT KBLI code → always IT (score=100).
2. Narrow keyword check: if the package name contains precise IT phrases → IT (score=80).
3. Exclusion check: if the package name contains civil/construction terms → reject (score=0).

Order matters: IT keyword match takes priority over exclusion keywords.
This prevents false negatives like "Pemasangan Kabel Fiber Optik Gedung"
(IT work happening inside a building) from being rejected by the "gedung" rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Fallback KBLI codes (used only when DB is unavailable) ──────────────────
DEFAULT_IT_KBLI_CODES: set[str] = {"62019", "62090", "62029"}

# ── Exclusion keywords: civil / construction / non-IT terms ──────────────────
# If NONE of the NARROW_IT_KEYWORDS matched AND any of these appear in the
# package name, the package is REJECTED as IT (score=0).
EXCLUSION_KEYWORDS: list[str] = [
    "perpipaan",
    "pipa",
    "spam",          # Sistem Penyediaan Air Minum
    "drainase",
    "irigasi",
    "jalan",
    "jembatan",
    "gedung",
    "bangunan",
    "sanitasi",
    "paving",
    "air minum",
    "limbah",
    "pembetonan",
    "aspal",
    "konstruksi",
    "infrastruktur jalan",
    "infrastruktur air",
    "tata air",
    "pengairan",
    "dam",
    "bendungan",
    "talud",
    "turap",
    "retaining wall",
    "jalan raya",
    "jalan tol",
    "jalan provinsi",
    "jalan kabupaten",
    "jalan kota",
    "perkerasan",
    "lampu jalan",
    "street light",
    "penerangan jalan",
    "trotoar",
    "culvert",
    "gorong-gorong",
    "box culvert",
    "saluran air",
    "water treatment",
    "ipal",
    "wwtp",
    "wtp",
    "pdam",
    "tirta",
    "cuci mobil",
    "car wash",
    "cuci motor",
    "laundry",
    "catering",
    "katering",
    "rumah sakit",
    "puskesmas",
    "kesehatan",
    "obat",
    "farmasi",
    "apotek",
    "medis",
    "kedokteran",
    "poliklinik",
    "ambulans",
    "kendaraan",
    "kendaraan dinas",
    "kendaraan operasional",
    "roda dua",
    "roda empat",
    "sepeda motor",
    "mobil dinas",
    "pengadaan mobil",
    "pengadaan kendaraan",
    "alat berat",
    "excavator",
    "buldozer",
    "crane",
    "forklift",
    "generator",
    "genset",
    "gardu listrik",
    "trafo",
    "transformator",
    "kabel tegangan tinggi",
    "tiang listrik",
    "solar panel",
    "panel surya",
    "plts",
    "pln",
    "material",
    "bahan bangunan",
    "semen",
    "besi",
    "beton",
    "ready mix",
    "ready-mix",
    "cor beton",
    # Engineering surveys that use databases/applications but aren't IT
    "database jalan",
    "database jembatan",
    "database irigasi",
    "database sungai",
    "inventarisasi jalan",
    "inventarisasi jembatan",
    "pengukuran tanah",
    "citra satelit",
    "citra",
    "pemetaan",
    "ukur tanah",
    "kadastral",
    "astaka",
    "geodet",
    "road management",
    "penerbitan sk jalan",
]

# ── Narrow IT keywords: precise phrases that unambiguously indicate IT work ───
# These must be contextually specific to IT/telecom. Single ambiguous words
# like "jaringan" are intentionally excluded.
NARROW_IT_KEYWORDS: list[str] = [
    # Network (with context — not bare "jaringan")
    "jaringan komputer",
    "jaringan internet",
    "jaringan lokal",
    "jaringan data",
    "jaringan wireless",
    "jaringan nirkabel",
    "lan/wan",
    "lan dan wan",
    "fiber optik",
    "fiber optic",
    "infrastructure network",
    "jaringan komunikasi",
    "jaringan telekomunikasi",
    # Software / applications (bare "aplikasi" excluded — too broad, matches civil apps)
    "software",
    "perangkat lunak",
    "website",
    "web application",
    "sistem informasi",
    "sistem informasi manajemen",
    "sistem manajemen",
    "enterprise application",
    # Data
    "data center",
    "datacenter",
    "data warehouse",
    "big data",
    "data analytics",
    "dashboard",
    # Security
    "cyber security",
    "keamanan siber",
    "keamanan jaringan",
    "firewall",
    "intrusion detection",
    "siem",
    "soc",
    "vulnerability",
    "penetration test",
    # Infrastructure
    "server",
    "cloud",
    "cloud computing",
    "cloud server",
    "virtualisasi",
    "vmware",
    "hypervisor",
    # Government IT
    "e-government",
    "e-government",
    "e-office",
    "e-procurement",
    "eprocurement",
    "spse",
    "sipd",
    "erdkk",
    "apps",
    # Bandwidth / connectivity
    "sewa bandwidth",
    "bandwidth",
    "leased line",
    "internet dedicated",
    "vpn",
    "internet",
    # Licenses
    "lisensi",
    "license",
    "licensing",
    # IT services
    "managed service",
    "it support",
    "helpdesk it",
    "service desk",
    "devops",
    "devsecops",
    # Development
    "rekayasa perangkat lunak",
    "rpl",
    "pengembangan software",
    "pengembangan aplikasi",
    "pengembangan sistem",
    "pemrograman",
    # Emerging tech
    "artificial intelligence",
    "kecerdasan buatan",
    "machine learning",
    "deep learning",
    "iot",
    "internet of things",
    "blockchain",
    "digitalisasi",
    "transformasi digital",
    "smart city",
    "smartdistrict",
    # GIS / geospatial (IT-specific)
    "geospasial",
    "gis",
    "sistem geografis",
    "geographic information",
    # Telecom
    "telekomunikasi",
    "tower telekomunikasi",
    "base transceiver",
    "microwave",
    "vsat",
    # Testing / QA
    "uji sistem",
    "testing sistem",
    "quality assurance",
    "qa testing",
    "user acceptance",
    "uat",
    # ERP / CRM
    "erp",
    "crm",
    "enterprise resource",
    # Backup / DR
    "backup",
    "disaster recovery",
    "bcp",
    "redundancy",
]

# Precompiled regex for fast matching (word-boundary aware for phrases)
_EXCLUSION_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|\b|\s)(" + "|".join(re.escape(kw).replace(r"\ ", r"\s+") for kw in EXCLUSION_KEYWORDS) + r")(?:\b|\s|$)",
    re.IGNORECASE,
)

_IT_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|\b|\s)(" + "|".join(re.escape(kw).replace(r"\ ", r"\s+") for kw in NARROW_IT_KEYWORDS) + r")(?:\b|\s|$)",
    re.IGNORECASE,
)


@dataclass
class PriorityScore:
    is_it_priority: bool
    priority_score: int  # 0-100
    matched_keywords: list[str]
    matched_reasons: list[str]


def get_active_kbli_codes() -> set[str]:
    """Load active KBLI codes from KbliMaster. Falls back to defaults on error."""
    try:
        from spse_crawler.web.models import KbliMaster
        codes = set(KbliMaster.objects.filter(is_active=True).values_list("code", flat=True))
        return codes if codes else DEFAULT_IT_KBLI_CODES
    except Exception:
        return DEFAULT_IT_KBLI_CODES


def score_it_priority(
    *,
    kbli_code: str = "",
    nama_paket: str = "",
    jenis_pengadaan: str = "",
    active_kbli_codes: set[str] | None = None,
) -> PriorityScore:
    """Calculate IT priority score for a tender package.

    Three-phase evaluation (order matters):
      Phase 1 — KBLI match: if code is in active KBLI from DB → always IT (score=100).
      Phase 2 — Narrow IT keywords: if precise IT phrases found → IT (score=80).
      Phase 3 — Exclusion keywords: if civil/construction terms found → reject (score=0).

    Phase 2 runs BEFORE Phase 3 so that IT work happening in a construction
    context (e.g. "Pemasangan Fiber Optik Gedung") is correctly tagged as IT.
    """
    nama_lower = (nama_paket or "").lower().strip()

    # ── Phase 1: KBLI code (definitive) ──
    if active_kbli_codes is None:
        active_kbli_codes = get_active_kbli_codes()
    if kbli_code in active_kbli_codes:
        return PriorityScore(
            is_it_priority=True,
            priority_score=100,
            matched_keywords=[kbli_code],
            matched_reasons=[f"KBLI {kbli_code} (IT)"],
        )

    if not nama_lower:
        return PriorityScore(
            is_it_priority=False,
            priority_score=0,
            matched_keywords=[],
            matched_reasons=[],
        )

    # ── Phase 2: Narrow IT keywords (checked FIRST) ──
    it_hits = _IT_PATTERN.findall(nama_lower)
    if it_hits:
        unique_it = list(dict.fromkeys(k.strip() for k in it_hits))
        return PriorityScore(
            is_it_priority=True,
            priority_score=80,
            matched_keywords=unique_it,
            matched_reasons=[f"IT keywords: {', '.join(unique_it[:3])}"],
        )

    # ── Phase 3: Exclusion keywords (only if no IT keyword matched) ──
    exc_hits = _EXCLUSION_PATTERN.findall(nama_lower)
    if exc_hits:
        unique_exc = list(dict.fromkeys(k.strip() for k in exc_hits))
        return PriorityScore(
            is_it_priority=False,
            priority_score=0,
            matched_keywords=unique_exc,
            matched_reasons=[f"Exclusion: {', '.join(unique_exc[:3])}"],
        )

    # No match
    return PriorityScore(
        is_it_priority=False,
        priority_score=0,
        matched_keywords=[],
        matched_reasons=[],
    )
