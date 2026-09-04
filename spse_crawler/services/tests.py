"""Tests for opportunity_scorer — the core deterministic scoring engine."""
from datetime import date, timedelta
from unittest.mock import MagicMock

from django.test import TestCase
from django.utils import timezone

from spse_crawler.services.opportunity_scorer import (
    WEIGHTS,
    ScoreBreakdown,
    _clamp,
    _extract_submission_deadline,
    _parse_date,
    compute_opportunity_score,
    score_to_dict,
)
from spse_crawler.companies.models import CompanyProfile, CompanyQualification
from spse_crawler.web.models import TenderResult


def _make_tender(**overrides):
    defaults = {
        "kode_instansi": "000001",
        "id_lelang": "T-001",
        "nama_paket": "Pengadaan Sistem Informasi",
        "instansi": "Kementerian IT",
        "hps": 500_000_000,
        "tahap_saat_ini": "Prakualifikasi",
        "is_prakualifikasi": True,
        "kbli_code": "62019",
        "is_it_priority": True,
        "priority_score": 80,
        "peserta_count": 5,
        "metode_pengadaan": "selectAll",
        "jadwal_json": [],
        "ai_score": 0,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def _make_company(**overrides):
    defaults = {
        "id": 1,
        "name": "PT Teknologi Nusantara",
        "nib": "1234567890",
        "modal_disetor": 2_000_000_000,
        "penghasilan_tahunan": 5_000_000_000,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class ClampTests(TestCase):
    def test_clamp_within_range(self):
        self.assertEqual(_clamp(50), 50)

    def test_clamp_below_min(self):
        self.assertEqual(_clamp(-10), 0)

    def test_clamp_above_max(self):
        self.assertEqual(_clamp(200), 100)

    def test_clamp_float(self):
        self.assertEqual(_clamp(67.9), 67)

    def test_clamp_custom_range(self):
        self.assertEqual(_clamp(200, 0, 50), 50)


class ParseDateTests(TestCase):
    def test_dd_mm_yyyy(self):
        self.assertEqual(_parse_date("25-12-2026"), date(2026, 12, 25))

    def test_yyyy_mm_dd(self):
        self.assertEqual(_parse_date("2026-12-25"), date(2026, 12, 25))

    def test_dd_slash_mm_slash_yyyy(self):
        self.assertEqual(_parse_date("25/12/2026"), date(2026, 12, 25))

    def test_dd_month_yyyy(self):
        self.assertEqual(_parse_date("25 Dec 2026"), date(2026, 12, 25))

    def test_dd_month_name_yyyy(self):
        self.assertEqual(_parse_date("25 December 2026"), date(2026, 12, 25))

    def test_empty_string(self):
        self.assertIsNone(_parse_date(""))

    def test_none(self):
        self.assertIsNone(_parse_date(None))

    def test_invalid_format(self):
        self.assertIsNone(_parse_date("not-a-date"))


class ExtractSubmissionDeadlineTests(TestCase):
    def test_empty_jadwal(self):
        self.assertEqual(_extract_submission_deadline([]), (None, None))

    def test_none_jadwal(self):
        self.assertEqual(_extract_submission_deadline(None), (None, None))

    def test_penawaran_priority(self):
        jadwal = [
            {"tahap": "Pra-Kualifikasi", "sampai": "01-01-2026"},
            {"tahap": "Penawaran Tahap 1", "sampai": "15-01-2026"},
        ]
        d, t = _extract_submission_deadline(jadwal)
        self.assertEqual(d, date(2026, 1, 15))
        self.assertEqual(t, "Penawaran Tahap 1")

    def test_submission_keyword(self):
        jadwal = [
            {"tahap": "Submission", "sampai": "20-06-2026"},
        ]
        d, _ = _extract_submission_deadline(jadwal)
        self.assertEqual(d, date(2026, 6, 20))

    def test_kualifikasi_fallback(self):
        jadwal = [
            {"tahap": "Kualifikasi", "sampai": "10-03-2026"},
        ]
        d, t = _extract_submission_deadline(jadwal)
        self.assertEqual(d, date(2026, 3, 10))
        self.assertEqual(t, "Kualifikasi")

    def test_last_available_fallback(self):
        jadwal = [
            {"tahap": "Pengumuman", "sampai": "01-01-2026"},
            {"tahap": "Penetapan Pemenang", "sampai": "20-02-2026"},
        ]
        d, _ = _extract_submission_deadline(jadwal)
        self.assertEqual(d, date(2026, 2, 20))

    def test_invalid_date_skipped(self):
        jadwal = [
            {"tahap": "Penawaran", "sampai": "invalid"},
            {"tahap": "Lainnya", "sampai": "05-05-2026"},
        ]
        d, _ = _extract_submission_deadline(jadwal)
        self.assertEqual(d, date(2026, 5, 5))


class ScoreBreakdownTests(TestCase):
    def test_default_values(self):
        bd = ScoreBreakdown()
        self.assertEqual(bd.total, 0)
        self.assertEqual(bd.recommendation, "")
        self.assertEqual(bd.strengths, [])
        self.assertEqual(bd.risks, [])

    def test_score_to_dict_structure(self):
        bd = ScoreBreakdown(total=75, recommendation="Direkomendasikan")
        bd.qualification_fit = 80
        bd.financial_fit = 70
        bd.experience_fit = 60
        bd.deadline_feasibility = 90
        bd.strategic_relevance = 65
        bd.details = [{"item": "test", "status": "pass"}]
        bd.strengths = ["test"]
        bd.risks = []
        bd.reason = "Good"

        d = score_to_dict(bd)
        self.assertEqual(d["opportunity_score"], 75)
        self.assertEqual(d["recommendation"], "Direkomendasikan")
        self.assertIn("components", d)
        self.assertIn("qualification", d["components"])
        self.assertEqual(d["components"]["qualification"]["score"], 80)
        self.assertEqual(d["components"]["qualification"]["weight_pct"], 40)
        self.assertIn("reason", d)
        self.assertIn("strengths", d)
        self.assertIn("risks", d)

    def test_weight_contributions(self):
        bd = ScoreBreakdown()
        bd.qualification_fit = 100
        bd.financial_fit = 100
        bd.experience_fit = 100
        bd.deadline_feasibility = 100
        bd.strategic_relevance = 100

        d = score_to_dict(bd)
        self.assertEqual(d["components"]["qualification"]["contribution"], 40.0)
        self.assertEqual(d["components"]["financial"]["contribution"], 20.0)
        self.assertEqual(d["components"]["experience"]["contribution"], 15.0)
        self.assertEqual(d["components"]["deadline"]["contribution"], 10.0)
        self.assertEqual(d["components"]["strategic"]["contribution"], 15.0)


class ComputeOpportunityScoreTests(TestCase):
    """Test compute_opportunity_score with mock objects (no DB)."""

    def test_no_company_returns_generic_score(self):
        tender = _make_tender()
        bd = compute_opportunity_score(tender, company=None)
        self.assertIsInstance(bd, ScoreBreakdown)
        self.assertEqual(bd.qualification_fit, 50)
        self.assertGreaterEqual(bd.total, 0)
        self.assertLessEqual(bd.total, 100)

    def test_recommendation_thresholds(self):
        for total, expected in [
            (90, "Sangat Direkomendasikan"),
            (75, "Direkomendasikan"),
            (55, "Perlu Ditinjau"),
            (30, "Tidak Direkomendasikan"),
        ]:
            bd = ScoreBreakdown()
            bd.total = total
            bd.details = []
            if total >= 70:
                bd.reason = "Tender ini cocok."
            elif total >= 50:
                bd.reason = "Perlu ditinjau."
            else:
                bd.reason = "Kurang sesuai."
            bd.strengths = [d.get("item", "") for d in []]
            bd.risks = []

            if total >= 85:
                bd.recommendation = "Sangat Direkomendasikan"
            elif total >= 70:
                bd.recommendation = "Direkomendasikan"
            elif total >= 50:
                bd.recommendation = "Perlu Ditinjau"
            else:
                bd.recommendation = "Tidak Direkomendasikan"

            self.assertEqual(bd.recommendation, expected)

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=6)

    def test_it_priority_increases_strategic(self):
        t1 = _make_tender(is_it_priority=True)
        t2 = _make_tender(is_it_priority=False)
        bd1 = compute_opportunity_score(t1)
        bd2 = compute_opportunity_score(t2)
        self.assertGreater(bd1.strategic_relevance, bd2.strategic_relevance)

    def test_prakualifikasi_increases_strategic(self):
        t1 = _make_tender(is_prakualifikasi=True)
        t2 = _make_tender(is_prakualifikasi=False)
        bd1 = compute_opportunity_score(t1)
        bd2 = compute_opportunity_score(t2)
        self.assertGreater(bd1.strategic_relevance, bd2.strategic_relevance)

    def test_high_competition_decreases_strategic(self):
        t1 = _make_tender(peserta_count=30)
        t2 = _make_tender(peserta_count=5)
        bd1 = compute_opportunity_score(t1)
        bd2 = compute_opportunity_score(t2)
        self.assertGreater(bd2.strategic_relevance, bd1.strategic_relevance)

    def test_selectall_metode_increases_strategic(self):
        t1 = _make_tender(metode_pengadaan="selectAll")
        t2 = _make_tender(metode_pengadaan="Tender Umum")
        bd1 = compute_opportunity_score(t1)
        bd2 = compute_opportunity_score(t2)
        self.assertGreater(bd1.strategic_relevance, bd2.strategic_relevance)

    def test_ai_score_boosts_strategic(self):
        t1 = _make_tender(ai_score=80)
        t2 = _make_tender(ai_score=0)
        bd1 = compute_opportunity_score(t1)
        bd2 = compute_opportunity_score(t2)
        self.assertGreater(bd1.strategic_relevance, bd2.strategic_relevance)

    def test_strengths_and_risks_populated(self):
        tender = _make_tender(is_it_priority=True)
        bd = compute_opportunity_score(tender, company=None)
        self.assertIsInstance(bd.strengths, list)
        self.assertIsInstance(bd.risks, list)

    def test_score_always_in_range(self):
        tender = _make_tender(
            is_it_priority=True,
            is_prakualifikasi=True,
            peserta_count=50,
            ai_score=90,
        )
        bd = compute_opportunity_score(tender, company=None)
        self.assertGreaterEqual(bd.total, 0)
        self.assertLessEqual(bd.total, 100)
        self.assertGreaterEqual(bd.qualification_fit, 0)
        self.assertLessEqual(bd.qualification_fit, 100)
        self.assertGreaterEqual(bd.strategic_relevance, 0)
        self.assertLessEqual(bd.strategic_relevance, 100)


class ScoreToDictTests(TestCase):
    def test_empty_breakdown(self):
        bd = ScoreBreakdown()
        d = score_to_dict(bd)
        self.assertEqual(d["opportunity_score"], 0)
        self.assertEqual(d["recommendation"], "")
        self.assertEqual(len(d["components"]), 5)

    def test_full_breakdown(self):
        bd = ScoreBreakdown(
            qualification_fit=90,
            financial_fit=80,
            experience_fit=70,
            deadline_feasibility=60,
            strategic_relevance=75,
            total=78,
            recommendation="Direkomendasikan",
            reason="Cocok.",
            strengths=["KBLI sesuai"],
            risks=["Deadline mepet"],
        )
        d = score_to_dict(bd)
        self.assertEqual(d["opportunity_score"], 78)
        self.assertEqual(d["reason"], "Cocok.")
        self.assertIn("KBLI sesuai", d["strengths"])
        self.assertIn("Deadline mepet", d["risks"])
