"""Deterministic Opportunity Score calculator (LEGACY — Phase 1.5).

DEPRECATED: This is the legacy scorer from Phase 1.5. It is preserved
untouched for backward compatibility. New code should use
``opportunity_scorer_v01.calculate()`` which implements the v0.1
specification with persistence, AIMatchResult prerequisite, and
weight redistribution for missing data.

This module will be removed in a future consolidation phase.

---

Computes a 0-100 score from 5 weighted components:
  - Qualification Fit      40%
  - Financial Fit          20%
  - Experience Fit         15%
  - Deadline Feasibility   10%
  - Strategic Relevance    15%

No LLM required — purely rule-based for fast, explainable results.

Scoring formula:
  total = clamp(qual * 0.40 + fin * 0.20 + exp * 0.15 + dl * 0.10 + strat * 0.15)

Each component is independently clamped to [0, 100].
The weighted total is also clamped to [0, 100].

Recommendation thresholds:
  >= 85  → Sangat Direkomendasikan
  >= 70  → Direkomendasikan
  >= 50  → Perlu Ditinjau
  <  50  → Tidak Direkomendasikan
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Q
from django.utils import timezone as dj_timezone


WEIGHTS = {
    "qualification": 0.40,
    "financial": 0.20,
    "experience": 0.15,
    "deadline": 0.10,
    "strategic": 0.15,
}


@dataclass
class ScoreBreakdown:
    qualification_fit: int = 0
    financial_fit: int = 0
    experience_fit: int = 0
    deadline_feasibility: int = 0
    strategic_relevance: int = 0
    total: int = 0
    recommendation: str = ""
    details: list[dict] = field(default_factory=list)
    reason: str = ""
    strengths: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


def _clamp(v, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(v)))


def _parse_date(date_str: str):
    """Try multiple date formats and return a date object or None."""
    if not date_str:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _extract_submission_deadline(jadwal_json: list) -> tuple:
    """Extract the submission deadline from jadwal JSON.

    Two-pass priority approach:
      Pass 1: Find ANY item matching penawaran/submission/pengajuan (highest priority)
      Pass 2: Find ANY item matching kualifikasi/prakualifikasi
      Pass 3: Last available date as fallback

    Returns (deadline_date, tahap_name) or (None, None).
    """
    if not jadwal_json:
        return None, None

    p1_date = None
    p1_name = ""
    p2_date = None
    p2_name = ""
    fallback_date = None
    fallback_name = ""

    for item in jadwal_json:
        if not isinstance(item, dict):
            continue
        tahap = (item.get("tahap") or "").lower()
        end_str = item.get("sampai") or item.get("end") or ""
        d = _parse_date(end_str)
        if not d:
            continue

        # Priority 1: Submission/penawaran deadline
        if any(kw in tahap for kw in ["penawaran", "submission", "pengajuan", "ajuan"]):
            if p1_date is None or d > p1_date:
                p1_date = d
                p1_name = item.get("tahap", "")

        # Priority 2: Kualifikasi deadline
        elif any(kw in tahap for kw in ["kualifikasi", "pra-kualifikasi", "prakualifikasi"]):
            if p2_date is None or d > p2_date:
                p2_date = d
                p2_name = item.get("tahap", "")

        # Track fallback (any date)
        if fallback_date is None or d > fallback_date:
            fallback_date = d
            fallback_name = item.get("tahap", "")

    if p1_date:
        return p1_date, p1_name
    if p2_date:
        return p2_date, p2_name
    return fallback_date, fallback_name


def compute_opportunity_score(tender, company=None) -> ScoreBreakdown:
    """Compute deterministic opportunity score for a tender.

    If company is None, returns a generic score based only on tender-side metrics.
    """
    bd = ScoreBreakdown()

    # Cache DB queries for company qualifications
    quals = None
    active_kbli_codes = set()
    has_izin = False
    has_sbu = False
    has_experience = False
    has_sdm = False
    recent_experience_count = 0

    if company:
        from spse_crawler.companies.models import CompanyQualification
        quals = CompanyQualification.objects.filter(
            company=company, status="active"
        )
        for q in quals:
            for code in (q.kbli_codes or []):
                if code:
                    active_kbli_codes.add(str(code).strip())
            if q.kbli_code:
                active_kbli_codes.add(q.kbli_code.strip())

        has_izin = quals.filter(category="izin_usaha").exists()
        has_sbu = quals.filter(category="sbu").exists()
        has_experience = quals.filter(category="pengalaman_kerja").exists()
        has_sdm = quals.filter(category="sdm").exists()
        recent_experience_count = CompanyQualification.objects.filter(
            company=company,
            category="pengalaman_kerja",
            project_year__gte=dj_timezone.now().year - 3,
        ).count()

    # =====================================================================
    # 1. QUALIFICATION FIT (weight: 40%)
    # =====================================================================
    # Max raw: KBLI match=50, izin=12, sbu=12, pengalaman=13, sdm=13 → 100
    qual_score = 0
    qual_details = []

    if company:
        tender_kbli = (tender.kbli_code or "").strip()
        if tender_kbli and tender_kbli in active_kbli_codes:
            qual_score += 50
            qual_details.append({"item": f"KBLI {tender_kbli} sesuai", "status": "pass"})
        elif tender_kbli:
            qual_details.append({"item": f"KBLI {tender_kbli} tidak sesuai", "status": "fail"})
        else:
            qual_score += 25
            qual_details.append({"item": "KBLI tender tidak diketahui", "status": "needs_action"})

        if has_izin:
            qual_score += 12
            qual_details.append({"item": "Izin Usaha aktif", "status": "pass"})
        else:
            qual_details.append({"item": "Izin Usaha belum ada", "status": "fail"})
        if has_sbu:
            qual_score += 12
            qual_details.append({"item": "SBU aktif", "status": "pass"})
        else:
            qual_details.append({"item": "SBU belum ada", "status": "fail"})
        if has_experience:
            qual_score += 13
            qual_details.append({"item": "Pengalaman Kerja tercatat", "status": "pass"})
        else:
            qual_details.append({"item": "Belum ada pengalaman kerja", "status": "fail"})
        if has_sdm:
            qual_score += 13
            qual_details.append({"item": "SDM/Manajerial tercatat", "status": "pass"})
        else:
            qual_details.append({"item": "SDM/Manajerial belum ada", "status": "fail"})
    else:
        qual_score = 50
        qual_details.append({"item": "Profil perusahaan tidak tersedia", "status": "needs_action"})

    bd.qualification_fit = _clamp(qual_score)
    bd.details.extend(qual_details)

    # =====================================================================
    # 2. FINANCIAL FIT (weight: 20%)
    # =====================================================================
    # Based on modal_disetor / HPS ratio, fallback to penghasilan_tahunan / HPS
    fin_score = 50
    fin_details = []
    if company:
        hps = tender.hps or 0
        modal = company.modal_disetor or 0
        income = company.penghasilan_tahunan or 0

        if hps > 0 and modal > 0:
            ratio = modal / hps
            if ratio >= 0.5:
                fin_score = 95
                fin_details.append({"item": f"Modal mencukupi ({ratio:.0%} dari HPS)", "status": "pass"})
            elif ratio >= 0.25:
                fin_score = 75
                fin_details.append({"item": f"Modal cukup ({ratio:.0%} dari HPS)", "status": "pass"})
            elif ratio >= 0.1:
                fin_score = 50
                fin_details.append({"item": f"Modal marginal ({ratio:.0%} dari HPS)", "status": "needs_action"})
            else:
                fin_score = 20
                fin_details.append({"item": f"Modal tidak mencukupi ({ratio:.0%} dari HPS)", "status": "fail"})
        elif hps > 0 and income > 0:
            ratio = income / hps
            if ratio >= 1.0:
                fin_score = 80
                fin_details.append({"item": f"Penghasilan mencukupi ({ratio:.0%} dari HPS)", "status": "pass"})
            elif ratio >= 0.5:
                fin_score = 60
                fin_details.append({"item": f"Penghasilan marginal ({ratio:.0%} dari HPS)", "status": "needs_action"})
            else:
                fin_score = 30
                fin_details.append({"item": "Penghasilan tidak mencukupi", "status": "fail"})
        elif hps == 0:
            fin_score = 50
            fin_details.append({"item": "HPS tidak tersedia", "status": "needs_action"})
        else:
            fin_details.append({"item": "Data keuangan tidak lengkap", "status": "needs_action"})

        if not has_experience and fin_score > 50:
            fin_score = max(fin_score - 10, 20)
            fin_details.append({"item": "Penalti: tidak ada pengalaman kerja", "status": "needs_action"})
    bd.financial_fit = _clamp(fin_score)
    bd.details.extend(fin_details)

    # =====================================================================
    # 3. EXPERIENCE FIT (weight: 15%)
    # =====================================================================
    # Based on count of recent (3 years) pengalaman_kerja qualifications
    exp_score = 30
    exp_details = []
    if company:
        if recent_experience_count >= 3:
            exp_score = 95
            exp_details.append({"item": f"{recent_experience_count} proyek pengalaman (3 tahun terakhir)", "status": "pass"})
        elif recent_experience_count >= 1:
            exp_score = 70
            exp_details.append({"item": f"{recent_experience_count} proyek pengalaman", "status": "pass"})
        else:
            exp_score = 20
            exp_details.append({"item": "Tidak ada pengalaman tercatat (3 tahun terakhir)", "status": "fail"})
    else:
        exp_details.append({"item": "Data pengalaman tidak tersedia", "status": "needs_action"})
    bd.experience_fit = _clamp(exp_score)
    bd.details.extend(exp_details)

    # =====================================================================
    # 4. DEADLINE FEASIBILITY (weight: 10%)
    # =====================================================================
    # Uses submission deadline from jadwal_json
    dl_score = 60
    dl_details = []
    deadline_date, deadline_tahap = _extract_submission_deadline(tender.jadwal_json or [])
    days_left = None

    if deadline_date:
        days_left = (deadline_date - dj_timezone.now().date()).days
        if days_left > 14:
            dl_score = 95
            dl_details.append({"item": f"Sisa waktu {days_left} hari", "status": "pass"})
        elif days_left > 7:
            dl_score = 75
            dl_details.append({"item": f"Sisa waktu {days_left} hari", "status": "pass"})
        elif days_left > 3:
            dl_score = 50
            dl_details.append({"item": f"Sisa waktu {days_left} hari — mulai siapkan", "status": "needs_action"})
        elif days_left > 0:
            dl_score = 25
            dl_details.append({"item": f"Sisa waktu {days_left} hari — mendesak", "status": "fail"})
        else:
            dl_score = 5
            dl_details.append({"item": "Deadline sudah lewat", "status": "fail"})
    else:
        dl_details.append({"item": "Info deadline tidak tersedia", "status": "needs_action"})
    bd.deadline_feasibility = _clamp(dl_score)
    bd.details.extend(dl_details)

    # =====================================================================
    # 5. STRATEGIC RELEVANCE (weight: 15%)
    # =====================================================================
    # Signals: IT priority, prakualifikasi, competition level,
    # KBLI relevance, tender type, AI fit score
    strat_score = 40
    strat_details = []

    # IT priority (strong signal)
    if tender.is_it_priority:
        strat_score += 20
        strat_details.append({"item": "Tender IT Priority", "status": "pass"})

    # Pra-kualifikasi (larger pool)
    if tender.is_prakualifikasi:
        strat_score += 8
        strat_details.append({"item": "Pra-Kualifikasi", "status": "pass"})

    # KBLI relevance (if company available)
    if company and active_kbli_codes:
        tender_kbli = (tender.kbli_code or "").strip()
        if tender_kbli and tender_kbli in active_kbli_codes:
            strat_score += 12
            strat_details.append({"item": "KBLI sesuai bidang usaha", "status": "pass"})
        elif tender_kbli:
            strat_score -= 5
            strat_details.append({"item": "KBLI di luar bidang usaha", "status": "needs_action"})

    # Competition level
    if tender.peserta_count:
        if tender.peserta_count > 20:
            strat_score -= 8
            strat_details.append({"item": f"{tender.peserta_count} peserta — kompetisi tinggi", "status": "needs_action"})
        elif tender.peserta_count > 0:
            strat_score += 5
            strat_details.append({"item": f"{tender.peserta_count} peserta terdaftar", "status": "pass"})
    else:
        strat_score += 3
        strat_details.append({"item": "Belum ada peserta terdaftar", "status": "pass"})

    # Tender type (some methods are more favorable)
    metode = (tender.metode_pengadaan or "").lower()
    if "selectall" in metode or "select all" in metode:
        strat_score += 5
        strat_details.append({"item": "Metode Select All — kualifikasi semua", "status": "pass"})
    elif "best" in metode:
        strat_score += 3
        strat_details.append({"item": "Metode Best Value", "status": "pass"})

    # AI fit score boost
    if tender.ai_score and tender.ai_score > 70:
        strat_score += 10
        strat_details.append({"item": f"Kesesuaian Kualifikasi AI {tender.ai_score}%", "status": "pass"})
    elif tender.ai_score and tender.ai_score > 40:
        strat_score += 5

    bd.strategic_relevance = _clamp(strat_score)
    bd.details.extend(strat_details)

    # =====================================================================
    # WEIGHTED TOTAL
    # =====================================================================
    bd.total = _clamp(int(
        bd.qualification_fit * WEIGHTS["qualification"]
        + bd.financial_fit * WEIGHTS["financial"]
        + bd.experience_fit * WEIGHTS["experience"]
        + bd.deadline_feasibility * WEIGHTS["deadline"]
        + bd.strategic_relevance * WEIGHTS["strategic"]
    ))

    # =====================================================================
    # RECOMMENDATION (Bahasa Indonesia)
    # =====================================================================
    if bd.total >= 85:
        bd.recommendation = "Sangat Direkomendasikan"
    elif bd.total >= 70:
        bd.recommendation = "Direkomendasikan"
    elif bd.total >= 50:
        bd.recommendation = "Perlu Ditinjau"
    else:
        bd.recommendation = "Tidak Direkomendasikan"

    # =====================================================================
    # REASON + STRENGTHS + RISKS (for recommended endpoint)
    # =====================================================================
    bd.strengths = [d["item"] for d in bd.details if d.get("status") == "pass"]
    bd.risks = [d["item"] for d in bd.details if d.get("status") == "fail"]
    needs_action = [d["item"] for d in bd.details if d.get("status") == "needs_action"]

    # Build reason
    if bd.total >= 70:
        bd.reason = "Tender ini cocok dengan profil perusahaan."
        if bd.qualification_fit >= 70:
            bd.reason += " Kualifikasi memadai."
        if bd.financial_fit >= 70:
            bd.reason += " Kemampuan keuangan mencukupi."
        if bd.experience_fit >= 70:
            bd.reason += " Pengalaman relevan tersedia."
    elif bd.total >= 50:
        bd.reason = "Tender ini perlu ditinjau lebih lanjut."
        if bd.risks:
            bd.reason += f" Perhatikan: {bd.risks[0]}."
    else:
        bd.reason = "Tender ini kurang sesuai dengan profil perusahaan saat ini."
        if bd.qualification_fit < 40:
            bd.reason += " Kualifikasi belum memadai."

    return bd


def score_to_dict(bd: ScoreBreakdown) -> dict:
    """Convert ScoreBreakdown to API response dict with full explainability."""
    return {
        "opportunity_score": bd.total,
        "recommendation": bd.recommendation,
        "components": {
            "qualification": {
                "score": bd.qualification_fit,
                "weight_pct": int(WEIGHTS["qualification"] * 100),
                "contribution": round(bd.qualification_fit * WEIGHTS["qualification"], 1),
            },
            "financial": {
                "score": bd.financial_fit,
                "weight_pct": int(WEIGHTS["financial"] * 100),
                "contribution": round(bd.financial_fit * WEIGHTS["financial"], 1),
            },
            "experience": {
                "score": bd.experience_fit,
                "weight_pct": int(WEIGHTS["experience"] * 100),
                "contribution": round(bd.experience_fit * WEIGHTS["experience"], 1),
            },
            "deadline": {
                "score": bd.deadline_feasibility,
                "weight_pct": int(WEIGHTS["deadline"] * 100),
                "contribution": round(bd.deadline_feasibility * WEIGHTS["deadline"], 1),
            },
            "strategic": {
                "score": bd.strategic_relevance,
                "weight_pct": int(WEIGHTS["strategic"] * 100),
                "contribution": round(bd.strategic_relevance * WEIGHTS["strategic"], 1),
            },
        },
        "details": bd.details,
        "reason": bd.reason,
        "strengths": bd.strengths,
        "risks": bd.risks,
    }
