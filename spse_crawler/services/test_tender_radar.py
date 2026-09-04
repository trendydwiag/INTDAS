"""Tests for Tender Radar — personalized opportunity discovery service.

Covers:
  - Service ranking (opp score, classification, deadline, READY>NOT_READY)
  - AI prerequisite / NOT_READY
  - Company isolation (no cross-company leakage)
  - API authentication (anon 401, roles success, superadmin inspect)
  - Filters (classification, min_score, search, kbli, location, priority, deadline)
  - Empty states (no company, no opportunities, no AI matches)
  - No LLM / no auto-calculation on radar (radar is read-only over persisted data)
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.utils import timezone

from spse_crawler.accounts.models import User
from spse_crawler.ai_match.models import AIMatchResult
from spse_crawler.companies.models import CompanyProfile
from spse_crawler.web.models import OpportunityScore, TenderResult
from spse_crawler.services.tender_radar import TenderRadarService


def _mk_jadwal(days_from_now):
    d = (timezone.now().date() + timedelta(days=days_from_now)).strftime("%d-%m-%Y")
    return [{"tahap": "Penawaran", "sampai": d}]


def _mk_company(name="PT Alpha", nib="1111111111"):
    return CompanyProfile.objects.create(
        name=name, nib=nib, modal_disetor=2_000_000_000,
        penghasilan_tahunan=5_000_000_000,
    )


def _mk_user(company, email="user@alpha.co.id", role="company_admin", username="user_alpha"):
    return User.objects.create_user(
        username=username, email=email, password="testpass123", role=role, company=company,
    )


def _mk_tender(kode="000001", idl="T-001", name="Sistem Informasi", hps=1_000_000_000,
               tahap="pengumuman prakualifikasi [...]", kbli="62019", it=False, days=15, location="Jakarta"):
    return TenderResult.objects.create(
        kode_instansi=kode, id_lelang=idl, nama_paket=name, instansi="Kementerian",
        hps=hps, tahap_saat_ini=tahap, kbli_code=kbli, is_it_priority=it,
        lokasi_pekerjaan=location, jadwal_json=_mk_jadwal(days),
    )


def _mk_ai(company, tender, fit=80, summary="Cocok"):
    return AIMatchResult.objects.create(
        tender=tender, company=company, fit_score=fit,
        summary=summary, criteria_json=[], llm_provider="test",
        cache_key=f"k{company.id}t{tender.id}",
    )


def _mk_opp(company, tender, **kw):
    defaults = dict(
        status="ready", qualification_score=80, financial_score=100,
        experience_score=75, deadline_score=100, strategic_score=85,
        final_score=85, classification="LAYAK_DIKEJAR",
        calculation_version="v0.1", breakdown_json={"explanation": ["ok"]},
    )
    defaults.update(kw)
    return OpportunityScore.objects.create(
        tender=tender, company=company, **defaults,
    )


def _base_fixtures():
    """Company A with 3 ready tenders + 1 NOT_READY tender, and Company B isolated."""
    a = _mk_company("PT Alpha", "1111111111")
    ua = _mk_user(a, email="ua@alpha.co.id", username="ua")

    # READY, high score, LAYAK_DIKEJAR, deadline 15d
    t1 = _mk_tender("000001", "T-001", "Sistem Informasi", hps=1_000_000_000, days=15)
    _mk_ai(a, t1, fit=90)
    _mk_opp(a, t1, final_score=85, classification="LAYAK_DIKEJAR",
            qualification_score=90, strategic_score=85)

    # READY, higher score, PRIORITAS_TINGGI, deadline 20d
    t2 = _mk_tender("000002", "T-002", "Cloud Service", hps=800_000_000, days=20,
                    it=True, kbli="62090")
    _mk_ai(a, t2, fit=95)
    _mk_opp(a, t2, final_score=92, classification="PRIORITAS_TINGGI",
            qualification_score=95, strategic_score=100)

    # READY, low score, REVIEW, deadline 3d
    t3 = _mk_tender("000003", "T-003", "Furniture", hps=200_000_000, days=3, kbli="31012")
    _mk_ai(a, t3, fit=55)
    _mk_opp(a, t3, final_score=62, classification="REVIEW",
            qualification_score=55, deadline_score=80)

    # NOT_READY: has AI match but no OpportunityScore created
    t4 = _mk_tender("000004", "T-004", "Consulting", hps=500_000_000, days=10, kbli="62090")
    _mk_ai(a, t4, fit=70)

    # A terminal/closed tender (should be excluded from eligibility)
    t5 = _mk_tender("000005", "T-005", "Selesai Project", hps=900_000_000, days=5,
                    tahap="Selesai")
    _mk_ai(a, t5, fit=88)
    _mk_opp(a, t5, final_score=88, classification="LAYAK_DIKEJAR")

    # Company B — isolated data
    b = _mk_company("PT Beta", "2222222222")
    ub = _mk_user(b, email="ub@beta.co.id", username="ub")
    tb = _mk_tender("100001", "B-001", "Beta Tender", hps=999_000_000, days=30, kbli="99999")
    _mk_ai(b, tb, fit=99)
    _mk_opp(b, tb, final_score=99, classification="PRIORITAS_TINGGI")

    return a, ua, [t1, t2, t3, t4, t5], b, ub, tb


# ---------------------------------------------------------------------------
# SERVICE RANKING
# ---------------------------------------------------------------------------

class ServiceRankingTests(TestCase):
    def setUp(self):
        a, ua, tenders, b, ub, tb = _base_fixtures()
        self.a, self.ua, self.tenders = a, ua, tenders
        self.b, self.ub, self.tb = b, ub, tb
        self.t1, self.t2, self.t3, self.t4, self.t5 = tenders

    def test_only_ready_by_default(self):
        """Default radar excludes NOT_READY tenders and terminal tenders."""
        data = TenderRadarService.get_radar(self.a.id)
        ids = [r["tender_id"] for r in data["results"]]
        self.assertIn(self.t1.id, ids)
        self.assertIn(self.t2.id, ids)
        self.assertIn(self.t3.id, ids)
        # NOT_READY (t4) and terminal (t5) excluded by default
        self.assertNotIn(self.t4.id, ids)
        self.assertNotIn(self.t5.id, ids)

    def test_higher_score_first(self):
        """Higher Opportunity Score appears first."""
        data = TenderRadarService.get_radar(self.a.id)
        scores = [r["opportunity"]["score"] for r in data["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(data["results"][0]["tender_id"], self.t2.id)  # 92

    def test_classification_priority(self):
        """PRIORITAS_TINGGI ranks above LAYAK_DIKEJAR above REVIEW."""
        data = TenderRadarService.get_radar(self.a.id)
        classifications = [r["opportunity"]["classification"] for r in data["results"]]
        self.assertEqual(classifications[0], "PRIORITAS_TINGGI")

    def test_ready_above_not_ready(self):
        """When include_not_ready, READY items rank above NOT_READY."""
        data = TenderRadarService.get_radar(self.a.id, filters={"include_not_ready": True})
        statuses = [r["radar_status"] for r in data["results"]]
        # All READY items come before any NOT_READY
        first_not_ready = statuses.index("NOT_READY")
        self.assertTrue(all(s == "READY" for s in statuses[:first_not_ready]))

    def test_not_ready_included_when_requested(self):
        data = TenderRadarService.get_radar(self.a.id, filters={"include_not_ready": True})
        ids = [r["tender_id"] for r in data["results"]]
        self.assertIn(self.t4.id, ids)

    def test_pagination(self):
        data = TenderRadarService.get_radar(self.a.id, limit=2, offset=0)
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["total"], 3)


# ---------------------------------------------------------------------------
# AI PREREQUISITE / NOT_READY
# ---------------------------------------------------------------------------

class NotReadyTests(TestCase):
    def test_no_ai_match_excluded_by_default(self):
        """Tender with no AIMatchResult is not in the default list."""
        a = _mk_company("PT Gamma", "3333333333")
        # Tender with NO AI match at all
        tn = _mk_tender("200001", "N-001", "No AI", days=20)
        data = TenderRadarService.get_radar(a.id)
        ids = [r["tender_id"] for r in data["results"]]
        self.assertNotIn(tn.id, ids)

    def test_ai_match_without_opp_score_is_not_ready(self):
        """AI match exists but no OpportunityScore → NOT_READY."""
        a = _mk_company("PT Delta", "4444444444")
        t = _mk_tender("200002", "N-002", "Has AI", days=20)
        _mk_ai(a, t, fit=80)
        data = TenderRadarService.get_radar(a.id, filters={"include_not_ready": True})
        row = next(r for r in data["results"] if r["tender_id"] == t.id)
        self.assertEqual(row["radar_status"], "NOT_READY")

    def test_no_llm_invoked(self):
        """Radar must not fabricate scores — NOT_READY has score 0, no calc.""" 
        a = _mk_company("PT Epsilon", "5555555555")
        t = _mk_tender("200003", "N-003", "No Calc", days=20)
        _mk_ai(a, t, fit=80)
        data = TenderRadarService.get_radar(a.id, filters={"include_not_ready": True})
        row = next(r for r in data["results"] if r["tender_id"] == t.id)
        self.assertEqual(row["opportunity"]["score"], 0)
        self.assertEqual(row["opportunity"]["classification"], "NOT_READY")
        # No OpportunityScore row should have been persisted by the radar
        self.assertFalse(OpportunityScore.objects.filter(tender=t, company=a).exists())


# ---------------------------------------------------------------------------
# COMPANY ISOLATION
# ---------------------------------------------------------------------------

class CompanyIsolationTests(TestCase):
    def setUp(self):
        a, ua, tenders, b, ub, tb = _base_fixtures()
        self.a, self.ua, self.tenders = a, ua, tenders
        self.b, self.ub, self.tb = b, ub, tb

    def test_company_a_does_not_see_company_b(self):
        data_a = TenderRadarService.get_radar(self.a.id)
        ids_a = [r["tender_id"] for r in data_a["results"]]
        self.assertNotIn(self.tb.id, ids_a)

    def test_company_b_only_sees_own(self):
        data_b = TenderRadarService.get_radar(self.b.id)
        ids_b = [r["tender_id"] for r in data_b["results"]]
        self.assertIn(self.tb.id, ids_b)
        a_ids = {self.tenders[i].id for i in range(3)}  # t1, t2, t3 (company A ready)
        for tid in ids_b:
            self.assertNotIn(tid, a_ids)


# ---------------------------------------------------------------------------
# FILTERS
# ---------------------------------------------------------------------------

class FilterTests(TestCase):
    def setUp(self):
        a, ua, tenders, b, ub, tb = _base_fixtures()
        self.a, self.ua, self.tenders = a, ua, tenders
        self.t1, self.t2, self.t3, self.t4, self.t5 = tenders

    def test_classification_filter(self):
        data = TenderRadarService.get_radar(self.a.id, filters={"classification": "PRIORITAS_TINGGI"})
        for r in data["results"]:
            self.assertEqual(r["opportunity"]["classification"], "PRIORITAS_TINGGI")
        self.assertEqual(len(data["results"]), 1)

    def test_min_score_filter(self):
        data = TenderRadarService.get_radar(self.a.id, filters={"min_score": 85})
        for r in data["results"]:
            self.assertGreaterEqual(r["opportunity"]["score"], 85)

    def test_search_filter(self):
        data = TenderRadarService.get_radar(self.a.id, filters={"search": "Cloud"})
        for r in data["results"]:
            self.assertIn("Cloud", r["title"])
        # only t2 matches
        self.assertEqual(len(data["results"]), 1)

    def test_kbli_filter(self):
        data = TenderRadarService.get_radar(self.a.id, filters={"kbli_code": "62019"})
        for r in data["results"]:
            self.assertEqual(r["kbli_code"], "62019")
        self.assertEqual(len(data["results"]), 1)

    def test_priority_filter(self):
        data = TenderRadarService.get_radar(self.a.id, filters={"priority": "it"})
        for r in data["results"]:
            self.assertTrue(r["is_it_priority"])
        self.assertEqual(len(data["results"]), 1)

    def test_location_filter(self):
        data = TenderRadarService.get_radar(self.a.id, filters={"location": "Jakarta"})
        for r in data["results"]:
            self.assertEqual(r["location"], "Jakarta")

    def test_deadline_after_filter(self):
        future = (timezone.now().date() + timedelta(days=25)).isoformat()
        data = TenderRadarService.get_radar(self.a.id, filters={"deadline_after": future})
        for r in data["results"]:
            self.assertIsNotNone(r["deadline"])


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class RadarAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a, cls.ua, tenders, cls.b, cls.ub, cls.tb = _base_fixtures()
        (cls.t1, cls.t2, cls.t3, cls.t4, cls.t5) = tenders

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_anonymous_returns_401(self):
        resp = Client().get("/api/radar/")
        self.assertEqual(resp.status_code, 401)

    def test_company_admin_success(self):
        resp = self._client(self.ua).get("/api/radar/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertGreater(data["count"], 0)

    def test_superadmin_success(self):
        sa = _mk_user(self.b, email="sa@x.co.id", role="superadmin", username="sa")
        resp = self._client(sa).get(f"/api/radar/?company_id={self.a.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertGreater(data["count"], 0)

    def test_company_admin_cannot_use_company_id(self):
        """company_admin must NOT be able to pass company_id (isolation)."""
        resp = self._client(self.ua).get(f"/api/radar/?company_id={self.b.id}")
        data = resp.json()
        # Should reflect company A (ua's company), not B
        ids = [r["tender_id"] for r in data["results"]]
        self.assertNotIn(self.tb.id, ids)

    def test_no_company_returns_empty(self):
        no_co = User.objects.create_user(
            username="noco", email="noco@x.co.id", password="x", role="submitter", company=None,
        )
        resp = self._client(no_co).get("/api/radar/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "empty")
        self.assertEqual(data["count"], 0)

    def test_response_contract(self):
        resp = self._client(self.ua).get("/api/radar/")
        data = resp.json()
        self.assertIn("results", data)
        self.assertIn("count", data)
        r0 = data["results"][0]
        for key in ["tender_id", "title", "hps", "deadline", "location",
                    "ai_match", "opportunity", "radar_status"]:
            self.assertIn(key, r0)
        self.assertIn("fit_score", r0["ai_match"])
        self.assertIn("score", r0["opportunity"])
        self.assertIn("classification", r0["opportunity"])
        self.assertEqual(r0["radar_status"], "READY")


class EmptyStateTests(TestCase):
    def test_no_ai_matches_empty(self):
        a = _mk_company("PT Zeta", "6666666666")
        u = _mk_user(a, email="uz@zeta.co.id", username="uz")
        _mk_tender("300001", "Z-001", "No match", days=20)
        c = Client(); c.force_login(u)
        resp = c.get("/api/radar/")
        data = resp.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["results"], [])
