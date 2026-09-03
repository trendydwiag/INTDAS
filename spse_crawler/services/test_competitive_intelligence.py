"""Tests for Phase 4 — Competitive & Winner Intelligence foundation.

Covers:
  - service: get_company_intelligence / get_tender_winner / get_tender_competition
  - no fabrication: unavailable aggregates return null / not_available; no
    participation, wins, or participant names are invented
  - no mutation and no LLM call (read-only foundation)
  - API auth (401 anonymous), company isolation (self vs other, with 403),
    superadmin cross-company access
  - data integrity: participant_count reflects real peserta_count only
"""
from unittest.mock import patch

from django.test import TestCase, Client

from spse_crawler.accounts.models import User
from spse_crawler.companies.models import CompanyProfile
from spse_crawler.web.models import TenderParticipant, TenderResult
from spse_crawler.services.competitive_intelligence import (
    get_company_intelligence,
    get_tender_winner,
    get_tender_competition,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _mk_company(name="PT Alpha", nib="1111111111"):
    return CompanyProfile.objects.create(
        name=name, nib=nib, modal_disetor=2_000_000_000,
        penghasilan_tahunan=5_000_000_000, is_active=True,
    )


def _mk_user(company=None, email="u@x.co.id", role="submitter", username="u"):
    return User.objects.create_user(
        username=username, email=email, password="pw12345",
        role=role, company=company,
    )


def _mk_tender(kode="000001", idl="T-001", peserta=5, name="Sistem Informasi"):
    return TenderResult.objects.create(
        kode_instansi=kode, id_lelang=idl, nama_paket=name,
        instansi="Kementerian", tahap_saat_ini="Penawaran",
        kbli_code="62019", peserta_count=peserta,
        jadwal_json=[{"tahap": "Penawaran", "sampai": "01-01-2030"}],
    )


# ---------------------------------------------------------------------------
# Service: company intelligence
# ---------------------------------------------------------------------------

class ServiceCompanyIntelligenceTests(TestCase):
    def test_not_found_company(self):
        res = get_company_intelligence(999999)
        self.assertEqual(res["status"], "not_found")
        self.assertIsNone(res["company"])
        self.assertIsNone(res["statistics"])

    def test_company_identity_returned(self):
        c = _mk_company()
        res = get_company_intelligence(c.id)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["company"]["id"], c.id)
        self.assertEqual(res["company"]["name"], c.name)

    def test_no_fabricated_statistics(self):
        """Without persisted participation/wins records, aggregates must be null,
        not zero — a fabricated zero would imply known-no-participation."""
        c = _mk_company()
        res = get_company_intelligence(c.id)
        self.assertIsNone(res["statistics"]["total_participation"])
        self.assertIsNone(res["statistics"]["total_wins"])
        self.assertIsNone(res["statistics"]["total_winning_value"])
        self.assertFalse(res["data_available"])

    def test_recent_tenders_and_wins_empty_not_fabricated(self):
        c = _mk_company()
        res = get_company_intelligence(c.id)
        self.assertEqual(res["recent_tenders"], [])
        self.assertEqual(res["recent_wins"], [])


# ---------------------------------------------------------------------------
# Service: winner intelligence
# ---------------------------------------------------------------------------

class ServiceWinnerTests(TestCase):
    def test_winner_not_available_for_existing_tender(self):
        """No winner column exists and decided tenders are excluded at crawl
        time — so a real tender with no winner row must be not_available, with
        winner null (never a fabricated default)."""
        t = _mk_tender()
        res = get_tender_winner(t.id)
        self.assertEqual(res["status"], "not_available")
        self.assertEqual(res["tender_id"], t.id)
        self.assertIsNone(res["winner"])

    def test_invalid_tender_not_found(self):
        res = get_tender_winner(999999)
        self.assertEqual(res["status"], "not_found")
        self.assertIsNone(res["winner"])


# ---------------------------------------------------------------------------
# Service: tender competition
# ---------------------------------------------------------------------------

class ServiceCompetitionTests(TestCase):
    def test_participant_count_reflects_real_data(self):
        t = _mk_tender(peserta=7)
        res = get_tender_competition(t.id)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["participant_count"], 7)

    def test_no_fabricated_participant_names(self):
        t = _mk_tender(peserta=7)
        res = get_tender_competition(t.id)
        self.assertEqual(res["participants"], [])

    def test_participant_names_returned_when_persisted(self):
        t = _mk_tender(peserta=2)
        TenderParticipant.objects.create(tender=t, name="PT Alpha", npwp="111")
        TenderParticipant.objects.create(tender=t, name="PT Beta", npwp="222")
        res = get_tender_competition(t.id)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["participant_count"], 2)
        self.assertEqual([p["name"] for p in res["participants"]], ["PT Alpha", "PT Beta"])

    def test_participant_names_are_external_only(self):
        t = _mk_tender(peserta=1)
        TenderParticipant.objects.create(tender=t, name="PT Alpha", npwp="111")
        res = get_tender_competition(t.id)
        self.assertEqual(res["participants"][0]["name"], "PT Alpha")
        self.assertEqual(res["participants"][0]["npwp"], "111")
        # No profile id is invented / attached
        self.assertNotIn("company_id", res["participants"][0])

    def test_zero_participants_not_available(self):
        t = _mk_tender(peserta=0)
        res = get_tender_competition(t.id)
        self.assertEqual(res["status"], "not_available")
        self.assertIsNone(res["participant_count"])
        self.assertEqual(res["participants"], [])

    def test_invalid_tender_not_found(self):
        res = get_tender_competition(999999)
        self.assertEqual(res["status"], "not_found")
        self.assertIsNone(res["participant_count"])


# ---------------------------------------------------------------------------
# Read-only / no side-effects
# ---------------------------------------------------------------------------

class ServiceNoMutationTests(TestCase):
    def test_no_mutation(self):
        c = _mk_company()
        t = _mk_tender(peserta=4)
        before = TenderResult.objects.count()
        get_company_intelligence(c.id)
        get_tender_winner(t.id)
        get_tender_competition(t.id)
        self.assertEqual(TenderResult.objects.count(), before)

    def test_no_llm_call(self):
        c = _mk_company()
        t = _mk_tender()
        with patch("spse_crawler.ai_match.matcher.run_match") as mock_run:
            get_company_intelligence(c.id)
            get_tender_winner(t.id)
            get_tender_competition(t.id)
            mock_run.assert_not_called()

    def test_no_db_rows_created(self):
        c = _mk_company()
        t = _mk_tender()
        before = CompanyProfile.objects.count() + TenderResult.objects.count()
        get_company_intelligence(c.id)
        get_tender_winner(t.id)
        get_tender_competition(t.id)
        after = CompanyProfile.objects.count() + TenderResult.objects.count()
        self.assertEqual(after, before)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class ServiceQueryCountTests(TestCase):
    """Verify each lookup is a constant, small number of queries (no N+1)."""

    def test_company_intelligence_queries(self):
        c = _mk_company()
        # company.fetch + participations + wins = fixed 3 queries (no N+1).
        with self.assertNumQueries(3):
            get_company_intelligence(c.id)

    def test_winner_queries(self):
        t = _mk_tender()
        # tender.fetch + absence-check of winner = fixed 2 queries (no N+1).
        with self.assertNumQueries(2):
            get_tender_winner(t.id)

    def test_competition_queries(self):
        t = _mk_tender()
        # Phase 5: participant names are read from the normalized
        # TenderParticipant rows — fixed 2 queries (tender + participants), no N+1.
        with self.assertNumQueries(2):
            get_tender_competition(t.id)


class APIIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = _mk_company("PT Alpha", "1111111111")
        cls.b = _mk_company("PT Beta", "2222222222")
        cls.ua = _mk_user(cls.a, "ua@a.co.id", "company_admin", "ua")
        cls.us = _mk_user(cls.a, "us@a.co.id", "submitter", "us")
        cls.sa = _mk_user(None, "sa@x.co.id", "superadmin", "sa")

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_company_anonymous_401(self):
        resp = Client().get(f"/api/intelligence/company/{self.a.id}/")
        self.assertEqual(resp.status_code, 401)

    def test_winner_anonymous_401(self):
        t = _mk_tender()
        resp = Client().get(f"/api/intelligence/tender/{t.id}/winner/")
        self.assertEqual(resp.status_code, 401)

    def test_competition_anonymous_401(self):
        t = _mk_tender()
        resp = Client().get(f"/api/intelligence/tender/{t.id}/competition/")
        self.assertEqual(resp.status_code, 401)

    def test_company_admin_own_company_ok(self):
        resp = self._client(self.ua).get(f"/api/intelligence/company/{self.a.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_company_admin_other_company_denied(self):
        resp = self._client(self.ua).get(f"/api/intelligence/company/{self.b.id}/")
        self.assertEqual(resp.status_code, 403)

    def test_submitter_own_company_ok(self):
        resp = self._client(self.us).get(f"/api/intelligence/company/{self.a.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_submitter_other_company_denied(self):
        resp = self._client(self.us).get(f"/api/intelligence/company/{self.b.id}/")
        self.assertEqual(resp.status_code, 403)

    def test_superadmin_can_inspect_any_company(self):
        resp_a = self._client(self.sa).get(f"/api/intelligence/company/{self.a.id}/")
        resp_b = self._client(self.sa).get(f"/api/intelligence/company/{self.b.id}/")
        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_b.status_code, 200)

    def test_company_not_found_via_api(self):
        resp = self._client(self.sa).get("/api/intelligence/company/999999/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "not_found")

    def test_winner_endpoint_contract(self):
        t = _mk_tender()
        resp = self._client(self.us).get(f"/api/intelligence/tender/{t.id}/winner/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["tender_id"], t.id)
        self.assertEqual(data["status"], "not_available")
        self.assertIsNone(data["winner"])

    def test_competition_endpoint_contract(self):
        t = _mk_tender(peserta=3)
        resp = self._client(self.us).get(f"/api/intelligence/tender/{t.id}/competition/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["participant_count"], 3)
        self.assertEqual(data["participants"], [])

    def test_competition_endpoint_returns_participant_names(self):
        t = _mk_tender(peserta=2)
        TenderParticipant.objects.create(tender=t, name="PT Alpha", npwp="111")
        TenderParticipant.objects.create(tender=t, name="PT Beta", npwp="222")
        resp = self._client(self.us).get(f"/api/intelligence/tender/{t.id}/competition/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual([p["name"] for p in data["participants"]], ["PT Alpha", "PT Beta"])

    def test_winner_invalid_tender_via_api(self):
        resp = self._client(self.us).get("/api/intelligence/tender/999999/winner/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "not_found")


class APIGetOnlyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = _mk_company()
        cls.sa = _mk_user(None, "sa@x.co.id", "superadmin", "sa")

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_company_endpoint_get_only(self):
        resp = self._client(self.sa).post(f"/api/intelligence/company/{self.a.id}/", {})
        self.assertIn(resp.status_code, (401, 405))

    def test_winner_endpoint_get_only(self):
        t = _mk_tender()
        resp = self._client(self.sa).post(f"/api/intelligence/tender/{t.id}/winner/", {})
        self.assertIn(resp.status_code, (401, 405))

    def test_competition_endpoint_get_only(self):
        t = _mk_tender()
        resp = self._client(self.sa).post(f"/api/intelligence/tender/{t.id}/competition/", {})
        self.assertIn(resp.status_code, (401, 405))
