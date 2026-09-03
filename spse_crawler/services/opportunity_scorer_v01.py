"""Opportunity Score v0.1 — Deterministic intelligence layer.

Computes a 0-100 score representing how worthwhile a tender is for a company
to pursue, based on five weighted dimensions:

  Qualification / Fit   35%   (from AIMatchResult.fit_score)
  Financial Fit         20%   (HPS / annual revenue ratio)
  Experience Fit        20%   (CompanyQualification pengalaman_kerja)
  Deadline Feasibility  15%   (days until submission deadline)
  Strategic Fit         10%   (fit_score thresholds + KBLI match)

Requires AIMatchResult to exist for (tender, company).
If missing, returns NOT_READY state — no fallback scoring.

No LLM calls. Purely rule-based. Deterministic.
"""
from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone as dj_timezone

from spse_crawler.web.models import OpportunityScore, TenderResult

VERSION = "v0.1"

WEIGHTS = {
    "qualification": 0.35,
    "financial": 0.20,
    "experience": 0.20,
    "deadline": 0.15,
    "strategic": 0.10,
}

CLASSIFICATION_THRESHOLDS = [
    (90, "PRIORITAS_TINGGI"),
    (75, "LAYAK_DIKEJAR"),
    (60, "REVIEW"),
    (40, "RISIKO_TINGGI"),
    (0, "TIDAK_DIREKOMENDASIKAN"),
]

FINANCIAL_THRESHOLDS = [
    (0.10, 100),
    (0.25, 90),
    (0.50, 75),
    (0.75, 60),
    (1.00, 40),
    (1.50, 20),
    (float("inf"), 0),
]

DEADLINE_THRESHOLDS = [
    (14, 100),
    (7, 90),
    (4, 80),
    (2, 60),
    (1, 35),
    (0, 15),
    (-1, 0),
]


def _clamp(v: int, lo: int = 0, hi: int = 100) -> int:
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

        if any(kw in tahap for kw in ["penawaran", "submission", "pengajuan", "ajuan"]):
            if p1_date is None or d > p1_date:
                p1_date = d
                p1_name = item.get("tahap", "")
        elif any(kw in tahap for kw in ["kualifikasi", "pra-kualifikasi", "prakualifikasi"]):
            if p2_date is None or d > p2_date:
                p2_date = d
                p2_name = item.get("tahap", "")

        if fallback_date is None or d > fallback_date:
            fallback_date = d
            fallback_name = item.get("tahap", "")

    if p1_date:
        return p1_date, p1_name
    if p2_date:
        return p2_date, p2_name
    return fallback_date, fallback_name


class OpportunityScorerV01:
    """Deterministic opportunity scorer following the v0.1 specification.

    Usage:
        result = OpportunityScorerV01.calculate(tender_id=123, company_id=45)
        # result is an OpportunityScore instance (persisted)
    """

    @staticmethod
    @transaction.atomic
    def calculate(tender_id: int, company_id: int) -> OpportunityScore:
        """Calculate and persist opportunity score for a tender+company pair.

        Returns OpportunityScore instance. If AIMatchResult is missing,
        returns a NOT_READY record.

        Deterministic: same inputs always produce the same output.
        """
        from spse_crawler.companies.models import CompanyProfile, CompanyQualification
        from spse_crawler.ai_match.models import AIMatchResult

        try:
            tender = TenderResult.objects.get(pk=tender_id)
        except TenderResult.DoesNotExist:
            raise ValueError(f"Tender #{tender_id} not found")

        try:
            company = CompanyProfile.objects.get(pk=company_id)
        except CompanyProfile.DoesNotExist:
            raise ValueError(f"Company #{company_id} not found")

        # Check AIMatchResult — hard prerequisite
        try:
            ai_match = AIMatchResult.objects.get(tender=tender, company=company)
        except AIMatchResult.DoesNotExist:
            # NOT_READY: AI Match hasn't been run yet
            obj, _ = OpportunityScore.objects.update_or_create(
                tender=tender,
                company=company,
                defaults={
                    "status": "not_ready",
                    "qualification_score": 0,
                    "financial_score": 0,
                    "experience_score": 0,
                    "deadline_score": 0,
                    "strategic_score": 0,
                    "final_score": 0,
                    "classification": "TIDAK_DIREKOMENDASIKAN",
                    "breakdown_json": {
                        "message": "Analisis AI belum tersedia. Jalankan AI Match terlebih dahulu.",
                    },
                    "calculation_version": VERSION,
                },
            )
            return obj

        # Gather company data
        quals = CompanyQualification.objects.filter(
            company=company, status="active"
        )
        active_kbli_codes = set()
        for q in quals:
            for code in (q.kbli_codes or []):
                if code:
                    active_kbli_codes.add(str(code).strip())
            if q.kbli_code:
                active_kbli_codes.add(q.kbli_code.strip())

        has_izin = quals.filter(category="izin_usaha").exists()
        has_sbu = quals.filter(category="sbu").exists()
        has_sdm = quals.filter(category="sdm").exists()

        # Experience qualifications
        experience_quals = list(
            CompanyQualification.objects.filter(
                company=company,
                category="pengalaman_kerja",
                status="active",
            )
        )
        has_experience = len(experience_quals) > 0

        # =====================================================================
        # 1. QUALIFICATION / FIT SCORE (35%)
        # Source: AIMatchResult.fit_score
        # =====================================================================
        qualification_score = ai_match.fit_score

        # =====================================================================
        # 2. FINANCIAL FIT (20%)
        # Source: HPS / penghasilan_tahunan ratio
        # =====================================================================
        hps = tender.hps or 0
        annual_revenue = company.penghasilan_tahunan or 0

        financial_score = None
        financial_explanation = ""

        if hps > 0 and annual_revenue > 0:
            ratio = hps / annual_revenue
            for threshold, score in FINANCIAL_THRESHOLDS:
                if ratio <= threshold:
                    financial_score = score
                    financial_explanation = (
                        f"Nilai HPS berada pada kisaran {ratio:.0%} dari "
                        f"penghasilan tahunan perusahaan."
                    )
                    break
        else:
            financial_explanation = (
                "Data HPS atau penghasilan tahunan tidak tersedia."
            )

        # =====================================================================
        # 3. EXPERIENCE FIT (20%)
        # Source: CompanyQualification(category="pengalaman_kerja")
        # =====================================================================
        experience_score = 0
        experience_explanation = ""

        if not has_experience:
            experience_explanation = "Tidak ada pengalaman kerja tercatat."
        else:
            # Check completeness: project_name, client_name, project_year, value_amount
            complete_count = 0
            for eq in experience_quals:
                fields_present = all([
                    eq.project_name,
                    eq.client_name,
                    eq.project_year,
                    eq.value_amount,
                ])
                if fields_present:
                    complete_count += 1

            if complete_count > 0:
                experience_score = 75
                experience_explanation = (
                    f"Terdapat {complete_count} pengalaman kerja aktif "
                    f"dengan data proyek yang lengkap."
                )
            else:
                experience_score = 60
                experience_explanation = (
                    f"Terdapat {len(experience_quals)} pengalaman kerja aktif."
                )

        # =====================================================================
        # 4. DEADLINE FEASIBILITY (15%)
        # Source: TenderResult.jadwal_json
        # =====================================================================
        deadline_score = None
        deadline_explanation = ""

        deadline_date, deadline_tahap = _extract_submission_deadline(
            tender.jadwal_json or []
        )

        if deadline_date:
            days_left = (deadline_date - dj_timezone.now().date()).days

            for threshold_days, score in DEADLINE_THRESHOLDS:
                if days_left >= threshold_days:
                    deadline_score = score
                    break

            if deadline_score is None:
                deadline_score = 0

            if days_left >= 14:
                deadline_explanation = (
                    f"Tersisa {days_left} hari sebelum deadline."
                )
            elif days_left >= 7:
                deadline_explanation = (
                    f"Tersisa {days_left} hari sebelum deadline."
                )
            elif days_left >= 4:
                deadline_explanation = (
                    f"Tersisa {days_left} hari sebelum deadline — mulai siapkan."
                )
            elif days_left >= 2:
                deadline_explanation = (
                    f"Tersisa {days_left} hari sebelum deadline — mendesak."
                )
            elif days_left >= 1:
                deadline_explanation = (
                    f"Tersisa {days_left} hari sebelum deadline — sangat mendesak."
                )
            elif days_left == 0:
                deadline_explanation = "Deadline hari ini."
            else:
                deadline_explanation = "Deadline sudah lewat."
        else:
            deadline_explanation = "Info deadline tidak tersedia."

        # =====================================================================
        # 5. STRATEGIC FIT (10%)
        # Based on fit_score thresholds + KBLI match
        # =====================================================================
        strategic_score = 0
        strategic_explanation = ""

        tender_kbli = (tender.kbli_code or "").strip()
        kbli_match = tender_kbli and tender_kbli in active_kbli_codes

        if kbli_match and qualification_score >= 90:
            strategic_score = 100
            strategic_explanation = (
                "Tender memiliki alignment tinggi dengan qualification perusahaan."
            )
        elif qualification_score >= 80:
            strategic_score = 85
            strategic_explanation = (
                "Tender memiliki alignment baik dengan qualification perusahaan."
            )
        elif qualification_score >= 70:
            strategic_score = 70
            strategic_explanation = (
                "Tender memiliki alignment cukup dengan qualification perusahaan."
            )
        elif qualification_score >= 50:
            strategic_score = 50
            strategic_explanation = (
                "Tender memiliki alignment moderat dengan qualification perusahaan."
            )
        else:
            strategic_score = 25
            strategic_explanation = (
                "Tender memiliki alignment rendah dengan qualification perusahaan."
            )

        # =====================================================================
        # WEIGHT REDISTRIBUTION
        # Components with NOT_AVAILABLE (None) are excluded, weights redistributed
        # =====================================================================
        components = {
            "qualification": qualification_score,
            "financial": financial_score,
            "experience": experience_score,
            "deadline": deadline_score,
            "strategic": strategic_score,
        }

        available_weights = {}
        for key, score in components.items():
            if score is not None:
                available_weights[key] = WEIGHTS[key]

        total_weight = sum(available_weights.values())

        if total_weight > 0:
            final_score = sum(
                components[key] * (available_weights[key] / total_weight)
                for key in available_weights
            )
        else:
            final_score = 0

        final_score = _clamp(int(round(final_score)))

        # =====================================================================
        # CLASSIFICATION
        # =====================================================================
        classification = "TIDAK_DIREKOMENDASIKAN"
        for threshold, label in CLASSIFICATION_THRESHOLDS:
            if final_score >= threshold:
                classification = label
                break

        # =====================================================================
        # BUILD EXPLANATION
        # =====================================================================
        explanation_parts = []
        explanation_parts.append(f"Qualification: {qualification_score}")
        explanation_parts.append(ai_match.summary or "Berdasarkan analisis AI.")

        if financial_score is not None:
            explanation_parts.append(f"Financial: {financial_score}")
            explanation_parts.append(financial_explanation)
        else:
            explanation_parts.append("Financial: NOT AVAILABLE")
            explanation_parts.append(financial_explanation)

        explanation_parts.append(f"Experience: {experience_score}")
        explanation_parts.append(experience_explanation)

        if deadline_score is not None:
            explanation_parts.append(f"Deadline: {deadline_score}")
            explanation_parts.append(deadline_explanation)
        else:
            explanation_parts.append("Deadline: NOT AVAILABLE")
            explanation_parts.append(deadline_explanation)

        explanation_parts.append(f"Strategic: {strategic_score}")
        explanation_parts.append(strategic_explanation)

        breakdown = {
            "components": {
                "qualification": {
                    "score": qualification_score,
                    "weight_pct": 35,
                    "source": "AIMatchResult.fit_score",
                },
                "financial": {
                    "score": financial_score,
                    "weight_pct": 20,
                    "source": "HPS / penghasilan_tahunan",
                    "available": financial_score is not None,
                },
                "experience": {
                    "score": experience_score,
                    "weight_pct": 20,
                    "source": "CompanyQualification.pengalaman_kerja",
                },
                "deadline": {
                    "score": deadline_score,
                    "weight_pct": 15,
                    "source": "TenderResult.jadwal_json",
                    "available": deadline_score is not None,
                },
                "strategic": {
                    "score": strategic_score,
                    "weight_pct": 10,
                    "source": "fit_score + KBLI match",
                },
            },
            "explanation": explanation_parts,
            "ai_match_summary": ai_match.summary or "",
            "kbli_match": kbli_match,
            "financial_available": financial_score is not None,
            "deadline_available": deadline_score is not None,
        }

        # =====================================================================
        # PERSIST
        # =====================================================================
        obj, _ = OpportunityScore.objects.update_or_create(
            tender=tender,
            company=company,
            defaults={
                "status": "ready",
                "qualification_score": qualification_score,
                "financial_score": financial_score or 0,
                "experience_score": experience_score,
                "deadline_score": deadline_score or 0,
                "strategic_score": strategic_score,
                "final_score": final_score,
                "classification": classification,
                "breakdown_json": breakdown,
                "calculation_version": VERSION,
            },
        )

        return obj


def calculate(tender_id: int, company_id: int) -> OpportunityScore:
    """Public convenience function — delegates to OpportunityScorerV01."""
    return OpportunityScorerV01.calculate(tender_id, company_id)
