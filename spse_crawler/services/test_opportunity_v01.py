"""Tests for opportunity_scorer_v01 — the v0.1 deterministic scoring engine.

Covers:
  - Weight configuration
  - Qualification scoring (AIMatchResult prerequisite)
  - Financial scoring (HPS/revenue ratio thresholds)
  - Experience scoring (completeness levels)
  - Deadline scoring (days-left thresholds)
  - Strategic scoring (fit_score + KBLI match)
  - Weight redistribution for missing components
  - Classification thresholds
  - Determinism
  - Persistence
  - API backward compatibility
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

from django.test import TestCase, Client
from django.utils import timezone

from spse_crawler.services.opportunity_scorer_v01 import (
    VERSION,
    WEIGHTS,
    CLASSIFICATION_THRESHOLDS,
    FINANCIAL_THRESHOLDS,
    DEADLINE_THRESHOLDS,
    _clamp,
    _parse_date,
    _extract_submission_deadline,
    OpportunityScorerV01,
    calculate,
)
from spse_crawler.accounts.models import User
from spse_crawler.companies.models import CompanyProfile, CompanyQualification
from spse_crawler.web.models import TenderResult, OpportunityScore


def _create_fixtures(**overrides):
    """Create shared test fixtures: company, user, qualifications, tenders, AI match."""
    from spse_crawler.ai_match.models import AIMatchResult

    company = CompanyProfile.objects.create(
        name=overrides.get("company_name", "PT Contoh Teknologi"),
        nib=overrides.get("nib", "9876543210"),
        modal_disetor=overrides.get("modal_disetor", 2_000_000_000),
        penghasilan_tahunan=overrides.get("penghasilan_tahunan", 5_000_000_000),
    )
    user = User.objects.create_user(
        username=overrides.get("username", "admin_contoh"),
        email=overrides.get("email", "admin@contoh.co.id"),
        password=overrides.get("password", "testpass123"),
        role="company_admin",
        company=company,
    )

    tender = TenderResult.objects.create(
        kode_instansi=overrides.get("kode_instansi", "000001"),
        id_lelang=overrides.get("id_lelang", "T-001"),
        nama_paket=overrides.get("nama_paket", "Pengadaan Sistem Informasi"),
        instansi=overrides.get("instansi", "Kementerian IT"),
        hps=overrides.get("hps", 500_000_000),
        tahap_saat_ini=overrides.get("tahap_saat_ini", "pengumuman prakualifikasi [...]"),
        is_prakualifikasi=overrides.get("is_prakualifikasi", True),
        kbli_code=overrides.get("kbli_code", "62019"),
        is_it_priority=overrides.get("is_it_priority", True),
        peserta_count=overrides.get("peserta_count", 5),
        metode_pengadaan=overrides.get("metode_pengadaan", "selectAll"),
        jadwal_json=overrides.get("jadwal_json", []),
        ai_score=overrides.get("ai_score", 0),
    )

    fit_score = overrides.get("fit_score", 85)
    ai_match = AIMatchResult.objects.create(
        tender=tender,
        company=company,
        fit_score=fit_score,
        summary=overrides.get("ai_summary", "Tender sangat cocok."),
        criteria_json=[],
        llm_provider="rulebased",
    )

    return company, user, tender, ai_match


# ---------------------------------------------------------------------------
# WEIGHT CONFIGURATION
# ---------------------------------------------------------------------------

class WeightTests(TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=6)

    def test_qualification_weight_is_35(self):
        self.assertEqual(WEIGHTS["qualification"], 0.35)

    def test_financial_weight_is_20(self):
        self.assertEqual(WEIGHTS["financial"], 0.20)

    def test_experience_weight_is_20(self):
        self.assertEqual(WEIGHTS["experience"], 0.20)

    def test_deadline_weight_is_15(self):
        self.assertEqual(WEIGHTS["deadline"], 0.15)

    def test_strategic_weight_is_10(self):
        self.assertEqual(WEIGHTS["strategic"], 0.10)

    def test_version_is_v01(self):
        self.assertEqual(VERSION, "v0.1")


# ---------------------------------------------------------------------------
# CLAMP
# ---------------------------------------------------------------------------

class ClampTests(TestCase):
    def test_clamp_within_range(self):
        self.assertEqual(_clamp(50), 50)

    def test_clamp_below_min(self):
        self.assertEqual(_clamp(-10), 0)

    def test_clamp_above_max(self):
        self.assertEqual(_clamp(200), 100)

    def test_clamp_float(self):
        self.assertEqual(_clamp(67.9), 67)


# ---------------------------------------------------------------------------
# PARSE DATE
# ---------------------------------------------------------------------------

class ParseDateTests(TestCase):
    def test_dd_mm_yyyy(self):
        self.assertEqual(_parse_date("25-12-2026"), date(2026, 12, 25))

    def test_empty_string(self):
        self.assertIsNone(_parse_date(""))

    def test_none(self):
        self.assertIsNone(_parse_date(None))


# ---------------------------------------------------------------------------
# EXTRACT SUBMISSION DEADLINE
# ---------------------------------------------------------------------------

class ExtractDeadlineTests(TestCase):
    def test_penawaran_priority(self):
        jadwal = [
            {"tahap": "Pra-Kualifikasi", "sampai": "01-01-2026"},
            {"tahap": "Penawaran Tahap 1", "sampai": "15-01-2026"},
        ]
        d, t = _extract_submission_deadline(jadwal)
        self.assertEqual(d, date(2026, 1, 15))

    def test_empty_jadwal(self):
        self.assertEqual(_extract_submission_deadline([]), (None, None))


# ---------------------------------------------------------------------------
# QUALIFICATION SCORING
# ---------------------------------------------------------------------------

class QualificationScoringTests(TestCase):
    def test_not_ready_without_ai_match(self):
        """Without AIMatchResult, score should be NOT_READY."""
        company = CompanyProfile.objects.create(
            name="PT Test", nib="1111111111",
            penghasilan_tahunan=5_000_000_000,
        )
        tender = TenderResult.objects.create(
            kode_instansi="000001", id_lelang="T-NO-AI",
            nama_paket="Test", instansi="Test",
            hps=500_000_000, tahap_saat_ini="Prakualifikasi",
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.status, "not_ready")
        self.assertEqual(obj.final_score, 0)

    def test_ready_with_ai_match(self):
        """With AIMatchResult, score should be READY."""
        company, user, tender, ai_match = _create_fixtures(fit_score=85)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.status, "ready")
        self.assertGreater(obj.final_score, 0)

    def test_qualification_score_equals_fit_score(self):
        """Qualification component should equal AIMatchResult.fit_score."""
        company, user, tender, ai_match = _create_fixtures(fit_score=92)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.qualification_score, 92)

    def test_qualification_score_range(self):
        """Qualification score should be 0-100."""
        for i, score in enumerate([0, 30, 50, 75, 100]):
            company, user, tender, ai_match = _create_fixtures(
                fit_score=score,
                nib=f"111111111{i}",
                username=f"user_{i}",
                email=f"user{i}@test.co.id",
                id_lelang=f"T-RANGE-{i}",
                kode_instansi=f"00000{i}",
            )
            obj = calculate(tender_id=tender.id, company_id=company.id)
            self.assertEqual(obj.qualification_score, score)


# ---------------------------------------------------------------------------
# FINANCIAL SCORING
# ---------------------------------------------------------------------------

class FinancialScoringTests(TestCase):
    def test_hps_10pct_revenue(self):
        """HPS ≤ 10% of revenue → financial_score = 100."""
        company, user, tender, _ = _create_fixtures(
            hps=500_000_000,
            penghasilan_tahunan=5_000_000_000,  # 10%
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.financial_score, 100)

    def test_hps_25pct_revenue(self):
        """HPS 25% of revenue → financial_score = 90 (≤25% bucket)."""
        company, user, tender, _ = _create_fixtures(
            hps=1_250_000_000,
            penghasilan_tahunan=5_000_000_000,  # 25%
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.financial_score, 90)

    def test_hps_50pct_revenue(self):
        """HPS 50% of revenue → financial_score = 75."""
        company, user, tender, _ = _create_fixtures(
            hps=2_500_000_000,
            penghasilan_tahunan=5_000_000_000,  # 50%
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.financial_score, 75)

    def test_hps_75pct_revenue(self):
        """HPS 75% of revenue → financial_score = 60."""
        company, user, tender, _ = _create_fixtures(
            hps=3_750_000_000,
            penghasilan_tahunan=5_000_000_000,  # 75%
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.financial_score, 60)

    def test_hps_100pct_revenue(self):
        """HPS 100% of revenue → financial_score = 40."""
        company, user, tender, _ = _create_fixtures(
            hps=5_000_000_000,
            penghasilan_tahunan=5_000_000_000,  # 100%
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.financial_score, 40)

    def test_hps_150pct_revenue(self):
        """HPS 150% of revenue → financial_score = 20."""
        company, user, tender, _ = _create_fixtures(
            hps=7_500_000_000,
            penghasilan_tahunan=5_000_000_000,  # 150%
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.financial_score, 20)

    def test_hps_over_150pct_revenue(self):
        """HPS > 150% of revenue → financial_score = 0."""
        company, user, tender, _ = _create_fixtures(
            hps=10_000_000_000,
            penghasilan_tahunan=5_000_000_000,  # 200%
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.financial_score, 0)

    def test_missing_revenue(self):
        """Missing revenue → financial component excluded (weight redistribution)."""
        company, user, tender, _ = _create_fixtures(
            penghasilan_tahunan=0,
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        breakdown = obj.breakdown_json.get("components", {})
        self.assertFalse(breakdown.get("financial", {}).get("available", True))

    def test_missing_hps(self):
        """Missing HPS → financial component excluded (weight redistribution)."""
        company, user, tender, _ = _create_fixtures(hps=0)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        breakdown = obj.breakdown_json.get("components", {})
        self.assertFalse(breakdown.get("financial", {}).get("available", True))


# ---------------------------------------------------------------------------
# EXPERIENCE SCORING
# ---------------------------------------------------------------------------

class ExperienceScoringTests(TestCase):
    def test_no_experience(self):
        """No pengalaman_kerja → experience_score = 0."""
        company, user, tender, _ = _create_fixtures()
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.experience_score, 0)

    def test_active_experience_incomplete(self):
        """Active experience with incomplete data → experience_score = 60."""
        company, user, tender, _ = _create_fixtures()
        CompanyQualification.objects.create(
            company=company,
            category="pengalaman_kerja",
            name="Proyek A",
            status="active",
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.experience_score, 60)

    def test_active_experience_complete(self):
        """Active experience with complete data → experience_score = 75."""
        company, user, tender, _ = _create_fixtures()
        CompanyQualification.objects.create(
            company=company,
            category="pengalaman_kerja",
            name="Proyek A",
            project_name="Sistem Informasi",
            client_name="Kementerian",
            project_year=2024,
            value_amount=1_000_000_000,
            status="active",
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.experience_score, 75)

    def test_expired_experience_not_counted(self):
        """Expired experience should not count as active."""
        company, user, tender, _ = _create_fixtures()
        CompanyQualification.objects.create(
            company=company,
            category="pengalaman_kerja",
            name="Proyek Lama",
            status="expired",
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.experience_score, 0)


# ---------------------------------------------------------------------------
# DEADLINE SCORING
# ---------------------------------------------------------------------------

class DeadlineScoringTests(TestCase):
    def _make_tender_with_deadline(self, days_until_deadline):
        """Create a tender with a deadline N days from now."""
        deadline = timezone.now().date() + timedelta(days=days_until_deadline)
        jadwal = [{"tahap": "Penawaran", "sampai": deadline.strftime("%d-%m-%Y")}]
        company, user, tender, _ = _create_fixtures(jadwal_json=jadwal)
        return company, tender

    def test_deadline_14_days(self):
        company, tender = self._make_tender_with_deadline(14)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.deadline_score, 100)

    def test_deadline_7_days(self):
        company, tender = self._make_tender_with_deadline(7)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.deadline_score, 90)

    def test_deadline_4_days(self):
        company, tender = self._make_tender_with_deadline(4)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.deadline_score, 80)

    def test_deadline_2_days(self):
        company, tender = self._make_tender_with_deadline(2)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.deadline_score, 60)

    def test_deadline_1_day(self):
        company, tender = self._make_tender_with_deadline(1)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.deadline_score, 35)

    def test_deadline_passed(self):
        company, tender = self._make_tender_with_deadline(-1)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.deadline_score, 0)

    def test_no_deadline(self):
        """Missing deadline → deadline component excluded."""
        company, user, tender, _ = _create_fixtures(jadwal_json=[])
        obj = calculate(tender_id=tender.id, company_id=company.id)
        breakdown = obj.breakdown_json.get("components", {})
        self.assertFalse(breakdown.get("deadline", {}).get("available", True))


# ---------------------------------------------------------------------------
# STRATEGIC SCORING
# ---------------------------------------------------------------------------

class StrategicScoringTests(TestCase):
    def test_high_fit_with_kbli_match(self):
        """fit_score >= 90 + KBLI match → strategic = 100."""
        company, user, tender, _ = _create_fixtures(fit_score=95, kbli_code="62019")
        CompanyQualification.objects.create(
            company=company,
            category="izin_usaha",
            name="Izin IT",
            kbli_codes=["62019"],
            status="active",
        )
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.strategic_score, 100)

    def test_fit_80_no_kbli_match(self):
        """fit_score >= 80 without KBLI match → strategic = 85."""
        company, user, tender, _ = _create_fixtures(fit_score=85, kbli_code="99999")
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.strategic_score, 85)

    def test_fit_70(self):
        """fit_score >= 70 → strategic = 70."""
        company, user, tender, _ = _create_fixtures(fit_score=72)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.strategic_score, 70)

    def test_fit_50(self):
        """fit_score >= 50 → strategic = 50."""
        company, user, tender, _ = _create_fixtures(fit_score=55)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.strategic_score, 50)

    def test_fit_below_50(self):
        """fit_score < 50 → strategic = 25."""
        company, user, tender, _ = _create_fixtures(fit_score=30)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.strategic_score, 25)


# ---------------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------------

class ClassificationTests(TestCase):
    def test_classification_boundaries(self):
        """Test classification is derived from final_score, not fit_score directly."""
        company, user, tender, _ = _create_fixtures(fit_score=85)
        CompanyQualification.objects.create(
            company=company, category="izin_usaha", name="Izin",
            kbli_codes=["62019"], status="active",
        )
        CompanyQualification.objects.create(
            company=company, category="pengalaman_kerja", name="Proyek",
            project_name="SI", client_name="Kem", project_year=2024,
            value_amount=1_000_000_000, status="active",
        )

        # TIDAK_DIREKOMENDASIKAN: need final < 40
        # Use no experience, no deadline, low fit → weight redistribution drops total
        tender_td = TenderResult.objects.create(
            kode_instansi="CL-TD", id_lelang="T-CL-TD",
            nama_paket="Test", instansi="Test",
            hps=500_000_000, tahap_saat_ini="Prakualifikasi",
            jadwal_json=[],
        )
        from spse_crawler.ai_match.models import AIMatchResult
        AIMatchResult.objects.create(
            tender=tender_td, company=company, fit_score=0,
            summary="Test", criteria_json=[], llm_provider="test",
        )
        # Remove experience for this test
        CompanyQualification.objects.filter(
            company=company, category="pengalaman_kerja"
        ).delete()
        obj = calculate(tender_id=tender_td.id, company_id=company.id)
        self.assertEqual(obj.classification, "TIDAK_DIREKOMENDASIKAN",
                         f"final={obj.final_score}")

        # LAYAK_DIKEJAR: final in 75-89 range
        tender_ly = TenderResult.objects.create(
            kode_instansi="CL-LY", id_lelang="T-CL-LY",
            nama_paket="Test", instansi="Test",
            hps=500_000_000, tahap_saat_ini="pengumuman prakualifikasi [...]",
            jadwal_json=[{"tahap": "Penawaran", "sampai": (timezone.now().date() + timedelta(days=20)).strftime("%d-%m-%Y")}],
        )
        AIMatchResult.objects.create(
            tender=tender_ly, company=company, fit_score=80,
            summary="Test", criteria_json=[], llm_provider="test",
        )
        CompanyQualification.objects.create(
            company=company, category="pengalaman_kerja", name="Proyek",
            project_name="SI", client_name="Kem", project_year=2024,
            value_amount=1_000_000_000, status="active",
        )
        obj = calculate(tender_id=tender_ly.id, company_id=company.id)
        self.assertEqual(obj.classification, "LAYAK_DIKEJAR",
                         f"final={obj.final_score}")

        # PRIORITAS_TINGGI: need final >= 90
        tender_pt = TenderResult.objects.create(
            kode_instansi="CL-PT", id_lelang="T-CL-PT",
            nama_paket="Test", instansi="Test",
            hps=500_000_000, tahap_saat_ini="pengumuman prakualifikasi [...]",
            jadwal_json=[{"tahap": "Penawaran", "sampai": (timezone.now().date() + timedelta(days=30)).strftime("%d-%m-%Y")}],
        )
        AIMatchResult.objects.create(
            tender=tender_pt, company=company, fit_score=95,
            summary="Test", criteria_json=[], llm_provider="test",
        )
        obj = calculate(tender_id=tender_pt.id, company_id=company.id)
        self.assertEqual(obj.classification, "PRIORITAS_TINGGI",
                         f"final={obj.final_score}")


# ---------------------------------------------------------------------------
# WEIGHT REDISTRIBUTION
# ---------------------------------------------------------------------------

class WeightRedistributionTests(TestCase):
    def test_missing_financial_redistributes(self):
        """When financial is NOT_AVAILABLE, weight is redistributed."""
        company, user, tender, _ = _create_fixtures(penghasilan_tahunan=0, hps=0)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        # Score should still be calculable (not 0) even without financial
        self.assertEqual(obj.status, "ready")
        self.assertGreater(obj.final_score, 0)

    def test_missing_deadline_redistributes(self):
        """When deadline is NOT_AVAILABLE, weight is redistributed."""
        company, user, tender, _ = _create_fixtures(jadwal_json=[])
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.status, "ready")
        self.assertGreater(obj.final_score, 0)


# ---------------------------------------------------------------------------
# DETERMINISM
# ---------------------------------------------------------------------------

class DeterminismTests(TestCase):
    def test_same_inputs_same_output(self):
        """Same inputs must produce identical output."""
        company, user, tender, _ = _create_fixtures(fit_score=85)
        obj1 = calculate(tender_id=tender.id, company_id=company.id)
        obj2 = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj1.final_score, obj2.final_score)
        self.assertEqual(obj1.classification, obj2.classification)


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------

class PersistenceTests(TestCase):
    def test_persists_to_db(self):
        """Score should be persisted to OpportunityScore model."""
        company, user, tender, _ = _create_fixtures(fit_score=85)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertIsNotNone(obj.pk)
        self.assertTrue(
            OpportunityScore.objects.filter(
                tender=tender, company=company
            ).exists()
        )

    def test_unique_constraint(self):
        """Only one score per tender+company pair."""
        company, user, tender, _ = _create_fixtures(fit_score=85)
        obj1 = calculate(tender_id=tender.id, company_id=company.id)
        obj2 = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj1.pk, obj2.pk)
        self.assertEqual(
            OpportunityScore.objects.filter(
                tender=tender, company=company
            ).count(),
            1,
        )

    def test_calculation_version(self):
        """Persisted score should have calculation_version = v0.1."""
        company, user, tender, _ = _create_fixtures(fit_score=85)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertEqual(obj.calculation_version, "v0.1")


# ---------------------------------------------------------------------------
# API BACKWARD COMPATIBILITY
# ---------------------------------------------------------------------------

class APIBackwardCompatTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.tender, cls.ai_match = _create_fixtures(
            fit_score=85,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email="admin@contoh.co.id", password="testpass123")

    def test_opportunity_score_field(self):
        """Response should include opportunity_score for backward compat."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("opportunity_score", data)
        self.assertIsInstance(data["opportunity_score"], int)

    def test_score_field(self):
        """Response should include new score field."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        data = resp.json()
        self.assertIn("score", data)
        self.assertEqual(data["score"], data["opportunity_score"])

    def test_classification_field(self):
        """Response should include classification."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        data = resp.json()
        self.assertIn("classification", data)
        self.assertIn(data["classification"], [
            "PRIORITAS_TINGGI", "LAYAK_DIKEJAR", "REVIEW",
            "RISIKO_TINGGI", "TIDAK_DIREKOMENDASIKAN",
        ])

    def test_status_field(self):
        """Response should include status."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        data = resp.json()
        self.assertIn("status", data)
        self.assertEqual(data["status"], "ready")

    def test_components_field(self):
        """Response should include components breakdown."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        data = resp.json()
        self.assertIn("components", data)
        for key in ["qualification", "financial", "experience", "deadline", "strategic"]:
            self.assertIn(key, data["components"])

    def test_recommendation_field(self):
        """Response should include recommendation for backward compat."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        data = resp.json()
        self.assertIn("recommendation", data)

    def test_explanation_field(self):
        """Response should include explanation list."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        data = resp.json()
        self.assertIn("explanation", data)
        self.assertIsInstance(data["explanation"], list)

    def test_calculation_version_field(self):
        """Response should include calculation_version."""
        resp = self.client.get(f"/api/opportunity/{self.tender.id}/")
        data = resp.json()
        self.assertEqual(data["calculation_version"], "v0.1")

    def test_not_ready_without_ai_match(self):
        """Without AI match, status should be not_ready."""
        company2 = CompanyProfile.objects.create(
            name="PT No AI Match", nib="9999999999",
        )
        User.objects.create_user(
            username="noai", email="noai@test.co.id",
            password="testpass123", role="company_admin", company=company2,
        )
        tender2 = TenderResult.objects.create(
            kode_instansi="000002", id_lelang="T-NO-AI",
            nama_paket="Test", instansi="Test",
            hps=500_000_000, tahap_saat_ini="Prakualifikasi",
        )
        c = Client()
        c.login(email="noai@test.co.id", password="testpass123")
        resp = c.get(f"/api/opportunity/{tender2.id}/")
        data = resp.json()
        self.assertEqual(data["status"], "not_ready")
        self.assertEqual(data["score"], 0)


# ---------------------------------------------------------------------------
# EDGE CASES
# ---------------------------------------------------------------------------

class EdgeCaseTests(TestCase):
    def test_tender_not_found(self):
        """Non-existent tender should return 404."""
        company, user, tender, _ = _create_fixtures()
        c = Client()
        c.login(email="admin@contoh.co.id", password="testpass123")
        resp = c.get("/api/opportunity/99999/")
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated(self):
        """Unauthenticated request should return 401."""
        resp = Client().get("/api/opportunity/1/")
        self.assertEqual(resp.status_code, 401)

    def test_final_score_always_in_range(self):
        """Final score should always be 0-100."""
        company, user, tender, _ = _create_fixtures(fit_score=95)
        obj = calculate(tender_id=tender.id, company_id=company.id)
        self.assertGreaterEqual(obj.final_score, 0)
        self.assertLessEqual(obj.final_score, 100)
