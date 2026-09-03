"""Tests for web API endpoints: opportunity score, recommended, pipeline, watchlist, company completion."""
import json
from datetime import date, timedelta

from django.test import TestCase, Client
from django.utils import timezone

from spse_crawler.accounts.models import User
from spse_crawler.companies.models import CompanyProfile, CompanyQualification
from spse_crawler.web.models import TenderResult
from spse_crawler.web.models_watchlist import TenderWatchlist
from spse_crawler.submissions.models import TenderSubmissionStatus


def _create_fixtures():
    """Create shared test fixtures: company, user, qualifications, tenders."""
    company = CompanyProfile.objects.create(
        name="PT Contoh Teknologi",
        nib="9876543210",
        npwp="12.345.678.9-012.000",
        address="Jl. Sudirman No. 1",
        phone="021-1234",
        email="info@contoh.co.id",
        contact_person="Budi",
        modal_disetor=2_000_000_000,
        penghasilan_tahunan=5_000_000_000,
    )
    user = User.objects.create_user(
        username="admin_contoh",
        email="admin@contoh.co.id",
        password="testpass123",
        role="company_admin",
        company=company,
    )

    # KBLI qualification
    CompanyQualification.objects.create(
        company=company,
        category="izin_usaha",
        name="Izin IT",
        kbli_code="62019",
        kbli_codes=["62019", "62090"],
        status="active",
    )
    CompanyQualification.objects.create(
        company=company,
        category="sbu",
        name="SBU IT",
        kbli_code="62019",
        status="active",
    )
    CompanyQualification.objects.create(
        company=company,
        category="pengalaman_kerja",
        name="Proyek E-Gov",
        project_name="E-Gov Portal",
        client_name="Kemenkominfo",
        project_year=date.today().year - 1,
        status="active",
    )
    CompanyQualification.objects.create(
        company=company,
        category="sdm",
        name="SDM Manager",
        status="active",
    )

    now = timezone.now()
    future = (date.today() + timedelta(days=15)).strftime("%d-%m-%Y")
    near = (date.today() + timedelta(days=2)).strftime("%d-%m-%Y")

    t1 = TenderResult.objects.create(
        kode_instansi="000001",
        id_lelang="T-001",
        nama_paket="Pengadaan Sistem Informasi",
        instansi="Kementerian IT",
        hps=500_000_000,
        tahap_saat_ini="Prakualifikasi",
        is_prakualifikasi=True,
        kbli_code="62019",
        is_it_priority=True,
        priority_score=80,
        peserta_count=5,
        metode_pengadaan="selectAll",
        jadwal_json=[
            {"tahap": "Pra-Kualifikasi", "sampai": future},
            {"tahap": "Penawaran Tahap 1", "sampai": future},
        ],
    )
    t2 = TenderResult.objects.create(
        kode_instansi="000002",
        id_lelang="T-002",
        nama_paket="Pengadaan Furniture Kantor",
        instansi="Kementerian A",
        hps=200_000_000,
        tahap_saat_ini="Kualifikasi",
        kbli_code="31012",
        is_it_priority=False,
        priority_score=30,
        peserta_count=15,
        jadwal_json=[],
    )
    t3 = TenderResult.objects.create(
        kode_instansi="000001",
        id_lelang="T-003",
        nama_paket="Managed Service Cloud",
        instansi="Kementerian IT",
        hps=1_000_000_000,
        tahap_saat_ini="Selesai",
        kbli_code="62090",
        is_it_priority=True,
        priority_score=70,
        jadwal_json=[],
    )

    # Create AIMatchResult for v0.1 scorer prerequisite
    from spse_crawler.ai_match.models import AIMatchResult
    AIMatchResult.objects.create(
        tender=t1, company=company,
        fit_score=85, summary="Sangat cocok",
        criteria_json=[], llm_provider="test",
    )
    AIMatchResult.objects.create(
        tender=t2, company=company,
        fit_score=35, summary="Kurang cocok",
        criteria_json=[], llm_provider="test",
    )

    return company, user, t1, t2, t3


class OpportunityScoreAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.t1, cls.t2, cls.t3 = _create_fixtures()

    def setUp(self):
        self.client = Client()
        self.client.login(email="admin@contoh.co.id", password="testpass123")

    def test_unauthenticated_returns_401(self):
        c = Client()
        resp = c.get(f"/api/opportunity/{self.t1.id}/")
        self.assertEqual(resp.status_code, 401)

    def test_returns_score_dict(self):
        resp = self.client.get(f"/api/opportunity/{self.t1.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("opportunity_score", data)
        self.assertIn("recommendation", data)
        self.assertIn("components", data)
        self.assertIn("reason", data)
        self.assertIn("strengths", data)
        self.assertIn("risks", data)

    def test_tender_not_found(self):
        resp = self.client.get("/api/opportunity/99999/")
        self.assertEqual(resp.status_code, 404)

    def test_score_in_range(self):
        resp = self.client.get(f"/api/opportunity/{self.t1.id}/")
        data = resp.json()
        self.assertGreaterEqual(data["opportunity_score"], 0)
        self.assertLessEqual(data["opportunity_score"], 100)

    def test_components_have_weights(self):
        resp = self.client.get(f"/api/opportunity/{self.t1.id}/")
        data = resp.json()
        for key in ["qualification", "financial", "experience", "deadline", "strategic"]:
            self.assertIn(key, data["components"])
            self.assertIn("score", data["components"][key])
            self.assertIn("weight_pct", data["components"][key])

    def test_it_tender_higher_score(self):
        r1 = self.client.get(f"/api/opportunity/{self.t1.id}/").json()
        r2 = self.client.get(f"/api/opportunity/{self.t2.id}/").json()
        self.assertGreater(r1["opportunity_score"], r2["opportunity_score"])


class RecommendedAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.t1, cls.t2, cls.t3 = _create_fixtures()

    def setUp(self):
        self.client = Client()
        self.client.login(email="admin@contoh.co.id", password="testpass123")

    def test_unauthenticated_returns_401(self):
        resp = Client().get("/api/recommended/")
        self.assertEqual(resp.status_code, 401)

    def test_returns_items_array(self):
        resp = self.client.get("/api/recommended/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items", data)
        self.assertIsInstance(data["items"], list)

    def test_items_have_required_fields(self):
        resp = self.client.get("/api/recommended/")
        data = resp.json()
        if data["items"]:
            item = data["items"][0]
            for key in ["id", "nama_paket", "opportunity_score", "recommendation", "reason", "strengths", "risks"]:
                self.assertIn(key, item)

    def test_excludes_terminal_tenders(self):
        resp = self.client.get("/api/recommended/")
        data = resp.json()
        for item in data["items"]:
            self.assertNotIn("selesai", item["tahap_saat_ini"].lower())
            self.assertNotIn("kontrak", item["tahap_saat_ini"].lower())


class PipelineAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.t1, cls.t2, cls.t3 = _create_fixtures()

    def setUp(self):
        self.client = Client()
        self.client.login(email="admin@contoh.co.id", password="testpass123")

    def test_unauthenticated_returns_401(self):
        resp = Client().get("/api/pipeline/")
        self.assertEqual(resp.status_code, 401)

    def test_returns_all_fields(self):
        resp = self.client.get("/api/pipeline/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ["belum_diproses", "sudah_submit", "menang", "gagal", "total", "urgency"]:
            self.assertIn(key, data)

    def test_urgency_structure(self):
        resp = self.client.get("/api/pipeline/")
        data = resp.json()
        self.assertIn("deadline_under_3_days", data["urgency"])
        self.assertIn("deadline_under_7_days", data["urgency"])

    def test_belum_diproses_count(self):
        TenderSubmissionStatus.objects.create(
            tender=self.t1, company=self.company, status="sudah_submit"
        )
        resp = self.client.get("/api/pipeline/")
        data = resp.json()
        self.assertGreaterEqual(data["sudah_submit"], 1)

    def test_counts_by_status(self):
        TenderSubmissionStatus.objects.create(
            tender=self.t1, company=self.company, status="menang"
        )
        TenderSubmissionStatus.objects.create(
            tender=self.t2, company=self.company, status="gagal"
        )
        resp = self.client.get("/api/pipeline/")
        data = resp.json()
        self.assertEqual(data["menang"], 1)
        self.assertEqual(data["gagal"], 1)


class WatchlistAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.t1, cls.t2, cls.t3 = _create_fixtures()

    def setUp(self):
        self.client = Client()
        self.client.login(email="admin@contoh.co.id", password="testpass123")

    def test_list_empty(self):
        resp = self.client.get("/api/watchlist/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"], [])

    def test_add_to_watchlist(self):
        resp = self.client.post(
            "/api/watchlist/add/",
            data=json.dumps({"tender_id": self.t1.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

    def test_add_then_list(self):
        self.client.post(
            "/api/watchlist/add/",
            data=json.dumps({"tender_id": self.t1.id}),
            content_type="application/json",
        )
        resp = self.client.get("/api/watchlist/")
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["tender_id"], self.t1.id)

    def test_add_duplicate(self):
        self.client.post(
            "/api/watchlist/add/",
            data=json.dumps({"tender_id": self.t1.id}),
            content_type="application/json",
        )
        resp = self.client.post(
            "/api/watchlist/add/",
            data=json.dumps({"tender_id": self.t1.id}),
            content_type="application/json",
        )
        # Should still succeed or return exists
        self.assertIn(resp.status_code, [200, 400])

    def test_remove_from_watchlist(self):
        add_resp = self.client.post(
            "/api/watchlist/add/",
            data=json.dumps({"tender_id": self.t1.id}),
            content_type="application/json",
        )
        wl_id = add_resp.json().get("id")
        self.assertIsNotNone(wl_id)

        resp = self.client.post(
            "/api/watchlist/remove/",
            data=json.dumps({"watchlist_id": wl_id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        list_resp = self.client.get("/api/watchlist/")
        self.assertEqual(list_resp.json()["items"], [])

    def test_watchlist_item_has_deadline(self):
        self.client.post(
            "/api/watchlist/add/",
            data=json.dumps({"tender_id": self.t1.id}),
            content_type="application/json",
        )
        resp = self.client.get("/api/watchlist/")
        item = resp.json()["items"][0]
        self.assertIn("deadline", item)
        self.assertIn("days_left", item)
        self.assertIn("urgency", item)

    def test_unauthenticated_returns_401(self):
        resp = Client().get("/api/watchlist/")
        self.assertEqual(resp.status_code, 401)


class CompanyCompletionAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.t1, cls.t2, cls.t3 = _create_fixtures()

    def setUp(self):
        self.client = Client()
        self.client.login(email="admin@contoh.co.id", password="testpass123")

    def test_unauthenticated_returns_401(self):
        resp = Client().get("/api/company/completion/")
        self.assertEqual(resp.status_code, 401)

    def test_returns_completion(self):
        resp = self.client.get("/api/company/completion/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("completion_pct", data)
        self.assertIn("categories", data)

    def test_completion_in_range(self):
        resp = self.client.get("/api/company/completion/")
        data = resp.json()
        self.assertGreaterEqual(data["completion_pct"], 0)
        self.assertLessEqual(data["completion_pct"], 100)

    def test_categories_structure(self):
        resp = self.client.get("/api/company/completion/")
        data = resp.json()
        for cat in ["identitas", "legal_kontak", "keuangan", "kualifikasi", "pengalaman", "sdm"]:
            self.assertIn(cat, data["categories"])

    def test_full_profile_high_score(self):
        """Company with all fields should have high completion."""
        resp = self.client.get("/api/company/completion/")
        data = resp.json()
        self.assertGreaterEqual(data["completion_pct"], 70)


class FilterAPITests(TestCase):
    """Test filter behaviors in the main data endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.t1, cls.t2, cls.t3 = _create_fixtures()

    def setUp(self):
        self.client = Client()
        self.client.login(email="admin@contoh.co.id", password="testpass123")

    def _get_results(self, **params):
        resp = self.client.get("/api/results/", params)
        if resp.status_code != 200:
            return resp, []
        data = resp.json()
        return resp, data.get("results", data.get("items", []))

    def test_tab_aktif_excludes_selesai(self):
        resp, results = self._get_results(tab="aktif")
        self.assertEqual(resp.status_code, 200)
        for item in results:
            self.assertNotIn("selesai", item["tahap_saat_ini"].lower())
            self.assertNotIn("kontrak", item["tahap_saat_ini"].lower())

    def test_tab_it_priority(self):
        resp, results = self._get_results(tab="it_priority")
        self.assertEqual(resp.status_code, 200)
        for item in results:
            self.assertTrue(item["is_it_priority"])

    def test_search_filter(self):
        resp, results = self._get_results(search="Furniture")
        self.assertEqual(resp.status_code, 200)
        names = [item["nama_paket"] for item in results]
        self.assertTrue(any("Furniture" in n for n in names))

    def test_hps_min_filter(self):
        resp, results = self._get_results(hps_min="600000000")
        self.assertEqual(resp.status_code, 200)
        for item in results:
            self.assertGreaterEqual(item["hps"], 600_000_000)

    def test_hps_max_filter(self):
        resp, results = self._get_results(hps_max="300000000")
        self.assertEqual(resp.status_code, 200)
        for item in results:
            self.assertLessEqual(item["hps"], 300_000_000)

    def test_hps_min_negative_ignored(self):
        resp, _ = self._get_results(hps_min="-100")
        self.assertEqual(resp.status_code, 200)

    def test_deadline_before_filter(self):
        resp, results = self._get_results(deadline_before="2020-01-01")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(results), 0)

    def test_invalid_date_filter_graceful(self):
        resp, _ = self._get_results(deadline_before="not-a-date")
        self.assertEqual(resp.status_code, 200)


class AuthEnforcementTests(TestCase):
    """Test that auth is required on protected endpoints."""

    @classmethod
    def setUpTestData(cls):
        cls.company, cls.user, cls.t1, _, _ = _create_fixtures()

    def _unauth_client(self):
        return Client()

    def test_opportunity_401(self):
        resp = self._unauth_client().get(f"/api/opportunity/{self.t1.id}/")
        self.assertEqual(resp.status_code, 401)

    def test_recommended_401(self):
        resp = self._unauth_client().get("/api/recommended/")
        self.assertEqual(resp.status_code, 401)

    def test_pipeline_401(self):
        resp = self._unauth_client().get("/api/pipeline/")
        self.assertEqual(resp.status_code, 401)

    def test_watchlist_401(self):
        resp = self._unauth_client().get("/api/watchlist/")
        self.assertEqual(resp.status_code, 401)

    def test_company_completion_401(self):
        resp = self._unauth_client().get("/api/company/completion/")
        self.assertEqual(resp.status_code, 401)

    def test_watchlist_add_401(self):
        resp = self._unauth_client().post(
            "/api/watchlist/add/",
            data=json.dumps({"tender_id": self.t1.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_watchlist_remove_401(self):
        resp = self._unauth_client().post(
            "/api/watchlist/remove/",
            data=json.dumps({"watchlist_id": 1}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
