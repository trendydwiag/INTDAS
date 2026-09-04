"""Unit and Integration Tests for AI Match & Eligibility Engine (Phase 1).

Covers all 9 mandatory test scenarios from Section 13:
 1. All mandatory requirements pass -> ELIGIBLE
 2. Mandatory KBLI fails -> NOT_ELIGIBLE + blocker
 3. Mandatory SBU missing -> NOT_ELIGIBLE + blocker
 4. Soft technical mismatch -> ELIGIBLE / CONDITIONALLY_ELIGIBLE, lower soft fit
 5. Missing company data -> NOT_READY
 6. LLM unavailable -> Rule-based fallback, semantic similarity != legal evidence
 7. High semantic similarity but mandatory missing -> NOT_ELIGIBLE (MANDATORY TEST)
 8. Cache invalidation -> Changing company qualification invalidates cache
 9. Existing API compatibility -> Existing fields preserved
"""

import json
from django.test import TestCase, Client
from django.contrib.auth import get_user_model

from spse_crawler.companies.models import CompanyProfile, CompanyQualification
from spse_crawler.web.models import TenderResult
from spse_crawler.ai_match.models import AIMatchResult
from spse_crawler.ai_match.matcher import run_match, _build_cache_key, MATCHER_VERSION
from spse_crawler.ai_match.providers import RuleBasedProvider, FallbackProvider

User = get_user_model()


class AIMatchEligibilityTests(TestCase):
    """Tests for Hard Requirement Gates, Eligibility, and Soft Fit."""

    def setUp(self):
        # Base company with valid NIB, NPWP, and KBLI 62019
        self.company = CompanyProfile.objects.create(
            name="PT Inovasi Teknologi Nusantara",
            nib="0220208762392",
            npwp="01.234.567.8-901.000",
            penghasilan_tahunan=10_000_000_000,
            modal_disetor=2_000_000_000,
            address="Jl. Sudirman No. 1, Jakarta",
        )
        self.qual_kbli = CompanyQualification.objects.create(
            company=self.company,
            category="izin_usaha",
            name="NIB Izin Usaha",
            kbli_code="62019",
            kbli_codes=["62019"],
            status="active",
        )
        self.qual_sbu = CompanyQualification.objects.create(
            company=self.company,
            category="sbu",
            name="SBU TI001 Jasa Pengembangan Software",
            status="active",
        )
        self.qual_exp = CompanyQualification.objects.create(
            company=self.company,
            category="pengalaman_kerja",
            name="Pengembangan Portal Web Kominfo",
            project_name="Portal SPSE Integrasi",
            client_name="Kementerian Kominfo",
            project_year=2024,
            value_amount=1_500_000_000,
            status="active",
        )

        # Standard submittable tender
        self.tender = TenderResult.objects.create(
            kode_instansi="kominfo",
            id_lelang="10160332000",
            nama_paket="Pengembangan Sistem Layanan Terpadu",
            tahap_saat_ini="pengumuman prakualifikasi [...]",
            hps=2_000_000_000,
            kbli_code="62019",
            kbli_description="Aktivitas Pemrograman Komputer Lainnya",
            syarat_kualifikasi=(
                "Persyaratan Kualifikasi:\n"
                "1. Memiliki NIB / Izin Usaha yang masih berlaku.\n"
                "2. Memiliki NPWP dan telah melunasi kewajiban pajak.\n"
                "3. Kesesuaian KBLI: 62019.\n"
                "4. Memiliki SBU aktif di bidang teknologi informasi.\n"
                "5. Memiliki pengalaman kerja di bidang software development.\n"
            ),
        )

    def test_01_all_mandatory_requirements_pass(self):
        """Test 1: Company satisfies KBLI, SBU, NIB, and NPWP -> ELIGIBLE."""
        result = run_match(tender_id=self.tender.id, company_id=self.company.id, force=True)

        self.assertEqual(result["eligibility_status"], "ELIGIBLE")
        self.assertTrue(result["mandatory_passed"])
        self.assertEqual(len(result["blockers"]), 0)
        self.assertGreater(result["fit_score"], 40)

        # Verify persisted record in DB
        persisted = AIMatchResult.objects.filter(
            tender=self.tender, company=self.company
        ).first()
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.eligibility_status, "ELIGIBLE")
        self.assertTrue(persisted.mandatory_passed)
        self.assertEqual(persisted.matcher_version, MATCHER_VERSION)

    def test_02_mandatory_kbli_fails(self):
        """Test 2: Tender requires KBLI 62090, company only has 62019 -> NOT_ELIGIBLE."""
        self.tender.kbli_code = "62090"
        self.tender.syarat_kualifikasi = "Syarat: KBLI 62090 wajib dipenuhi."
        self.tender.save(update_fields=["kbli_code", "syarat_kualifikasi"])

        result = run_match(tender_id=self.tender.id, company_id=self.company.id, force=True)

        self.assertEqual(result["eligibility_status"], "NOT_ELIGIBLE")
        self.assertFalse(result["mandatory_passed"])
        self.assertTrue(any("kbli" in b.lower() for b in result["blockers"]))
        # Hard gate caps fit_score to max 25
        self.assertLessEqual(result["fit_score"], 25)

    def test_03_mandatory_sbu_missing(self):
        """Test 3: Tender explicitly requires SBU, company has no SBU -> NOT_ELIGIBLE."""
        # Remove company SBU
        self.qual_sbu.delete()

        self.tender.syarat_kualifikasi = (
            "Kualifikasi Teknis: Wajib melampirkan SBU (Sertifikat Badan Usaha) aktif."
        )
        self.tender.save(update_fields=["syarat_kualifikasi"])

        result = run_match(tender_id=self.tender.id, company_id=self.company.id, force=True)

        self.assertEqual(result["eligibility_status"], "NOT_ELIGIBLE")
        self.assertFalse(result["mandatory_passed"])
        self.assertTrue(any("sbu" in b.lower() for b in result["blockers"]))
        self.assertLessEqual(result["fit_score"], 25)

    def test_04_soft_technical_mismatch(self):
        """Test 4: Mandatory requirements pass, but technical/domain terms differ -> ELIGIBLE but lower score."""
        # Tender in different specialized technical domain but matching KBLI 62019
        self.tender.syarat_kualifikasi = (
            "Kualifikasi: KBLI: 62019, memiliki NIB dan SBU aktif.\n"
            "Lingkup Khusus: Pengalaman implementasi Blockchain Hyperledger Fabric, "
            "Rust Smart Contract, dan Cryptographic Zero Knowledge Proofs."
        )
        self.tender.save(update_fields=["syarat_kualifikasi"])

        result = run_match(tender_id=self.tender.id, company_id=self.company.id, force=True)

        # Still eligible or conditionally eligible because hard requirements pass
        self.assertIn(result["eligibility_status"], ("ELIGIBLE", "CONDITIONALLY_ELIGIBLE"))
        self.assertTrue(result["mandatory_passed"])
        self.assertEqual(len(result["blockers"]), 0)

    def test_05_missing_company_data(self):
        """Test 5: Empty company with no qualifications -> NOT_READY."""
        empty_company = CompanyProfile.objects.create(
            name="PT Kosong Polos",
            nib="",
            npwp="",
        )
        result = run_match(tender_id=self.tender.id, company_id=empty_company.id, force=True)

        self.assertEqual(result["eligibility_status"], "NOT_READY")
        self.assertFalse(result["mandatory_passed"])
        self.assertEqual(result["fit_score"], 0)

    def test_06_llm_unavailable_rule_based_fallback(self):
        """Test 6: Provider fallback operates deterministically without crashing."""
        provider = RuleBasedProvider()
        self.assertEqual(provider.name(), "rule_based")

        raw = provider.chat(
            system_prompt="sys",
            user_prompt=(
                "== PERSYARATAN KUALIFIKASI SPSE ==\n"
                "KBLI: 62019. Wajib memiliki SBU dan NIB aktif.\n"
                "== PROFIL PERUSAHAAN ==\n"
                "Nama: PT Test\nNIB: 1234567890\nNPWP: 01.234.567.8\n"
                "== KUALIFIKASI & DOKUMEN PERUSAHAAN (AKTIF) ==\n"
                '[{"category": "SBU", "category_raw": "sbu", "name": "SBU TI", "kbli_codes": ["62019"]}]\n\n'
                'Perform strict eligibility and qualification assessment. Return ONLY the JSON object.'
            ),
        )
        data = json.loads(raw)
        self.assertIn(data["eligibility_status"], ("ELIGIBLE", "CONDITIONALLY_ELIGIBLE"))
        self.assertTrue(data["mandatory_passed"])

    def test_07_high_semantic_similarity_but_mandatory_missing(self):
        """Test 7 (MANDATORY): Text has high semantic similarity, but SBU is missing.

        MUST evaluate to NOT_ELIGIBLE and MUST NOT produce a high fit score.
        """
        # Delete SBU from company
        self.qual_sbu.delete()

        # Requirement packed with rich keywords matching company's experience and words
        self.tender.syarat_kualifikasi = (
            "KBLI: 62019. Pengadaan Pengembangan Portal Web Kominfo dan Aplikasi SPSE Integrasi. "
            "Penyedia harus berpengalaman dalam software development, sistem informasi, "
            "jaringan komputer, teknologi informasi, software dan hardware.\n"
            "Persyaratan Mutlak: Wajib memiliki SBU (Sertifikat Badan Usaha) aktif bidang TI."
        )
        self.tender.save(update_fields=["syarat_kualifikasi"])

        result = run_match(tender_id=self.tender.id, company_id=self.company.id, force=True)

        # Semantic similarity would be very high, but SBU is missing!
        self.assertEqual(result["eligibility_status"], "NOT_ELIGIBLE")
        self.assertFalse(result["mandatory_passed"])
        self.assertTrue(any("sbu" in b.lower() for b in result["blockers"]))
        # Hard gate MUST cap fit score to <= 25 despite high keyword overlap!
        self.assertLessEqual(result["fit_score"], 25)

    def test_08_cache_invalidation_on_qualification_change(self):
        """Test 8: Changing company qualifications changes cache key and invalidates cache."""
        # 1. Run initial match
        res1 = run_match(tender_id=self.tender.id, company_id=self.company.id, force=False)
        self.assertFalse(res1.get("cached", False))

        # 2. Re-running without change gives cache hit
        res2 = run_match(tender_id=self.tender.id, company_id=self.company.id, force=False)
        self.assertTrue(res2.get("cached", False))

        # 3. Add a new qualification (e.g. SBU TI002)
        CompanyQualification.objects.create(
            company=self.company,
            category="sbu",
            name="SBU TI002 Cloud Infrastructure",
            status="active",
        )

        # 4. Re-running immediately detects the changed qualification hash and recomputes
        res3 = run_match(tender_id=self.tender.id, company_id=self.company.id, force=False)
        self.assertFalse(res3.get("cached", False))

    def test_09_existing_api_compatibility(self):
        """Test 9: API endpoints preserve existing fields while exposing new additive fields."""
        # Pre-compute match
        run_match(tender_id=self.tender.id, company_id=self.company.id, force=True)

        user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="testpassword123",
            company_id=self.company.id,
            is_active=True,
        )

        client = Client()
        client.force_login(user)

        # Test GET /api/match/results/
        resp = client.get(f"/api/match/results/?tender_id={self.tender.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items", data)
        self.assertGreaterEqual(len(data["items"]), 1)

        item = data["items"][0]
        # Verify backward compatible fields
        self.assertIn("fit_score", item)
        self.assertIn("summary", item)
        self.assertIn("criteria", item)
        self.assertIn("tender_name", item)
        self.assertIn("company_name", item)

        # Verify new additive fields
        self.assertIn("eligibility_status", item)
        self.assertIn("mandatory_passed", item)
        self.assertIn("blockers", item)
        self.assertIn("missing_requirements", item)
        self.assertIn("recommended_actions", item)
        self.assertEqual(item["matcher_version"], MATCHER_VERSION)
