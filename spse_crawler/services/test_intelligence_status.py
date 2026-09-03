"""Tests for the Intelligence Status / Readiness service + API.

Covers:
  - readiness levels: READY / PARTIAL / NOT_READY / EMPTY
  - count accuracy and coverage calculation
  - company isolation / no cross-company leakage
  - no mutation (read-only)
  - no LLM call
  - API: auth 401, company_admin/submitter scoped to own company,
    superadmin company_id, company_admin cannot override company_id
"""
from unittest.mock import patch

from django.test import TestCase, Client

from spse_crawler.accounts.models import User
from spse_crawler.ai_match.models import AIMatchResult
from spse_crawler.companies.models import CompanyProfile, CompanyQualification
from spse_crawler.web.models import OpportunityScore, TenderResult
from spse_crawler.services.intelligence_status import compute_status


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _mk_company(name="PT Alpha", nib="1111111111"):
    return CompanyProfile.objects.create(
        name=name, nib=nib, modal_disetor=2_000_000_000,
        penghasilan_tahunan=5_000_000_000, is_active=True,
    )


def _mk_qual(company, kbli="62019"):
    return CompanyQualification.objects.create(
        company=company, category="isin_usaha", number="SBU-1",
        kbli_code=kbli, status="active",
    )


def _mk_user(company, email, role, username):
    return User.objects.create_user(
        username=username, email=email, password="pw12345", role=role, company=company,
    )


def _mk_tender(kode="000001", idl="T-001", name="Sistem Informasi", tahap="Penawaran"):
    return TenderResult.objects.create(
        kode_instansi=kode, id_lelang=idl, nama_paket=name,
        instansi="Kementerian", tahap_saat_ini=tahap,
        kbli_code="62019", jadwal_json=[{"tahap": "Penawaran", "sampai": "01-01-2030"}],
    )


def _mk_terminal_tender(kode="000099", idl="T-END", name="Selesai Project", tahap="Selesai"):
    return TenderResult.objects.create(
        kode_instansi=kode, id_lelang=idl, nama_paket=name,
        instansi="Kementerian", tahap_saat_ini=tahap,
        kbli_code="62019", jadwal_json=[{"tahap": "Selesai", "sampai": "01-01-2030"}],
    )


def _mk_ai(company, tender, fit=80):
    return AIMatchResult.objects.create(
        tender=tender, company=company, fit_score=fit,
        summary="Cocok", criteria_json=[], llm_provider="test",
        cache_key=f"k{company.id}t{tender.id}",
    )


def _mk_opp(company, tender, status="ready", final=80):
    return OpportunityScore.objects.create(
        tender=tender, company=company, status=status, final_score=final,
        classification="LAYAK_DIKEJAR", calculation_version="v0.1",
    )


def _setUp_two_companies():
    """Company A ready; Company B isolated. Returns objects."""
    a = _mk_company("PT Alpha", "1111111111")
    _mk_qual(a)
    ua = _mk_user(a, "ua@alpha.co.id", "company_admin", "ua")

    ta1 = _mk_tender("000001", "T-001")
    _mk_ai(a, ta1)
    _mk_opp(a, ta1, status="ready", final=85)

    b = _mk_company("PT Beta", "2222222222")
    _mk_qual(b)
    ub = _mk_user(b, "ub@beta.co.id", "submitter", "ub")
    tb = _mk_tender("100001", "B-001")  # B has tender but no AI
    return a, ua, ta1, b, ub, tb


# ---------------------------------------------------------------------------
# Service: readiness levels
# ---------------------------------------------------------------------------

class ServiceReadinessTests(TestCase):
    def test_not_ready_no_tenders(self):
        a = _mk_company()
        res = compute_status(a.id)
        self.assertEqual(res["readiness"]["level"], "NOT_READY")
        self.assertFalse(res["readiness"]["ready"])
        self.assertEqual(res["counts"]["tenders"], 0)

    def test_not_ready_has_tender_no_ai(self):
        a = _mk_company()
        _mk_tender()
        res = compute_status(a.id)
        self.assertEqual(res["readiness"]["level"], "NOT_READY")
        self.assertEqual(res["counts"]["tenders"], 1)
        self.assertEqual(res["counts"]["ai_matches"], 0)

    def test_not_ready_ai_but_no_ready_opportunity(self):
        a = _mk_company()
        t = _mk_tender()
        _mk_ai(a, t)
        # OpportunityScore exists but not ready (not_ready status)
        _mk_opp(a, t, status="not_ready", final=0)
        res = compute_status(a.id)
        self.assertEqual(res["readiness"]["level"], "NOT_READY")
        self.assertEqual(res["counts"]["ai_matches"], 1)
        self.assertEqual(res["counts"]["ready_opportunities"], 0)

    def test_ready(self):
        a = _mk_company()
        t = _mk_tender()
        _mk_ai(a, t)
        _mk_opp(a, t, status="ready", final=85)
        res = compute_status(a.id)
        self.assertEqual(res["readiness"]["level"], "READY")
        self.assertTrue(res["readiness"]["ready"])

    def test_partial_when_some_tenders_unscored(self):
        a = _mk_company()
        _mk_qual(a)
        t1 = _mk_tender("000001", "T-001")
        _mk_tender("000002", "T-002")  # no AI, no score
        _mk_ai(a, t1)
        _mk_opp(a, t1, status="ready", final=85)
        res = compute_status(a.id)
        # Data present but coverage incomplete -> PARTIAL.
        self.assertEqual(res["readiness"]["level"], "PARTIAL")
        self.assertLess(res["coverage"]["ai_match_percent"], 100)


class ServiceCountsTests(TestCase):
    def test_count_accuracy(self):
        a = _mk_company()
        t1 = _mk_tender("000001", "T-001")
        t2 = _mk_tender("000002", "T-002")
        _mk_ai(a, t1)
        _mk_opp(a, t1, status="ready", final=90)
        _mk_ai(a, t2)
        _mk_opp(a, t2, status="ready", final=70)
        res = compute_status(a.id)
        self.assertEqual(res["counts"]["tenders"], 2)
        self.assertEqual(res["counts"]["ai_matches"], 2)
        self.assertEqual(res["counts"]["opportunity_scores"], 2)
        self.assertEqual(res["counts"]["ready_opportunities"], 2)
        self.assertEqual(res["coverage"]["ai_match_percent"], 100)
        self.assertEqual(res["coverage"]["opportunity_score_percent"], 100)

    def test_coverage_pct(self):
        a = _mk_company()
        for i in range(4):
            _mk_tender(f"{i:06d}", f"T-{i}")
        t0 = TenderResult.objects.first()
        _mk_ai(a, t0)
        res = compute_status(a.id)
        self.assertEqual(res["counts"]["tenders"], 4)
        self.assertEqual(res["coverage"]["ai_match_percent"], 25)
        self.assertEqual(res["coverage"]["opportunity_score_percent"], 0)

    def test_terminal_tenders_excluded_from_counts(self):
        a = _mk_company()
        _mk_tender("000001", "T-001")
        _mk_terminal_tender()
        res = compute_status(a.id)
        self.assertEqual(res["counts"]["tenders"], 1)

    def test_zero_coverage_when_no_tenders(self):
        a = _mk_company()
        res = compute_status(a.id)
        self.assertEqual(res["coverage"]["ai_match_percent"], 0)
        self.assertEqual(res["coverage"]["opportunity_score_percent"], 0)


class ServiceIsolationTests(TestCase):
    def test_cross_company_isolation(self):
        a, ua, ta1, b, ub, tb = _setUp_two_companies()
        res_a = compute_status(a.id)
        res_b = compute_status(b.id)
        # Tenders are global (shared) — company isolation governs AI/score data.
        self.assertEqual(res_a["counts"]["ai_matches"], 1)
        # A covers 1 of 2 shared active tenders -> PARTIAL.
        self.assertEqual(res_a["readiness"]["level"], "PARTIAL")
        # B shares the same tender pool but has NO AI match / NO score.
        self.assertEqual(res_b["counts"]["ai_matches"], 0)
        self.assertEqual(res_b["counts"]["opportunity_scores"], 0)
        self.assertEqual(res_b["readiness"]["level"], "NOT_READY")


class ServiceNoMutationTests(TestCase):
    def test_no_mutation(self):
        a = _mk_company()
        t = _mk_tender()
        _mk_ai(a, t)
        _mk_opp(a, t, status="ready", final=85)
        before_ai = AIMatchResult.objects.count()
        before_opp = OpportunityScore.objects.count()
        before_tender = TenderResult.objects.count()
        compute_status(a.id)
        self.assertEqual(AIMatchResult.objects.count(), before_ai)
        self.assertEqual(OpportunityScore.objects.count(), before_opp)
        self.assertEqual(TenderResult.objects.count(), before_tender)

    def test_no_llm_call(self):
        a = _mk_company()
        t = _mk_tender()
        _mk_ai(a, t)
        _mk_opp(a, t, status="ready", final=85)
        with patch(
            "spse_crawler.ai_match.matcher.run_match"
        ) as mock_run:
            compute_status(a.id)
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class APIStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        a, ua, ta1, b, ub, tb = _setUp_two_companies()
        cls.a, cls.ua, cls.ta1 = a, ua, ta1
        cls.b, cls.ub, cls.tb = b, ub, tb
        cls.sa = _mk_user(None, "sa@x.co.id", "superadmin", "sa")

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_anonymous_401(self):
        resp = Client().get("/api/intelligence/status/")
        self.assertEqual(resp.status_code, 401)

    def test_company_admin_success(self):
        resp = self._client(self.ua).get("/api/intelligence/status/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["company_id"], self.a.id)
        self.assertEqual(data["readiness"]["level"], "PARTIAL")

    def test_submitter_success(self):
        resp = self._client(self.ub).get("/api/intelligence/status/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["company_id"], self.b.id)
        self.assertEqual(data["readiness"]["level"], "NOT_READY")

    def test_superadmin_can_use_company_id(self):
        resp = self._client(self.sa).get(f"/api/intelligence/status/?company_id={self.b.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["company_id"], self.b.id)
        # B is NOT_READY (no AI), proving superadmin inspected B not their own.
        self.assertEqual(data["readiness"]["level"], "NOT_READY")

    def test_company_admin_cannot_override_company_id(self):
        """company_admin passing company_id of another company stays scoped."""
        resp = self._client(self.ua).get(
            f"/api/intelligence/status/?company_id={self.b.id}"
        )
        data = resp.json()
        self.assertEqual(data["company_id"], self.a.id)
        self.assertEqual(data["readiness"]["level"], "PARTIAL")

    def test_user_without_company_returns_empty(self):
        no_co = _mk_user(None, "noco@x.co.id", "submitter", "noco")
        resp = self._client(no_co).get("/api/intelligence/status/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "empty")
        self.assertEqual(data["readiness"]["level"], "NOT_READY")
        self.assertEqual(data["counts"]["tenders"], 0)

    def test_contract_fields(self):
        resp = self._client(self.ua).get("/api/intelligence/status/")
        data = resp.json()
        for key in ["status", "company_id", "readiness", "counts", "coverage", "message"]:
            self.assertIn(key, data)
        for key in ["ready", "level"]:
            self.assertIn(key, data["readiness"])
        for key in ["tenders", "ai_matches", "opportunity_scores", "ready_opportunities"]:
            self.assertIn(key, data["counts"])
        for key in ["ai_match_percent", "opportunity_score_percent"]:
            self.assertIn(key, data["coverage"])

    def test_get_only_no_post(self):
        resp = Client().post("/api/intelligence/status/", {})
        self.assertIn(resp.status_code, (401, 405))
