"""Tests for Canonical Tender Status Normalization and Active Gate.

Covers all 14 mandatory test cases from Addendum Section 14:
 1. Exact canonical status 'pengumuman prakualifikasi' -> True
 2. Actual DB representation 'pengumuman prakualifikasi [...]' -> True
 3. Case variation 'Pengumuman Prakualifikasi [...]' -> True
 4. Whitespace variation '  pengumuman prakualifikasi [...]  ' -> True
 5. Evaluasi Administrasi -> False
 6. Evaluasi Teknis -> False
 7. Pengumuman Pemenang -> False
 8. Kontrak -> False
 9. Selesai -> False
 10. Batal -> False
 11. None -> False
 12. Empty string -> False
 13. Historical AI Match preserved after status transition -> True
 14. Current Radar excludes transitioned tender -> True
"""

from django.test import TestCase

from spse_crawler.companies.models import CompanyProfile, CompanyQualification
from spse_crawler.services.tender_status import (
    CANONICAL_ACTIVE_STATUS,
    normalize_tahap,
    is_submittable_tender,
)
from spse_crawler.web.models import TenderResult, OpportunityScore
from spse_crawler.ai_match.models import AIMatchResult
from spse_crawler.services.tender_radar import TenderRadarService


class TenderStatusNormalizationTests(TestCase):
    """Unit tests for normalize_tahap and is_submittable_tender."""

    def test_01_exact_canonical_status(self):
        self.assertEqual(normalize_tahap("pengumuman prakualifikasi"), CANONICAL_ACTIVE_STATUS)
        self.assertTrue(is_submittable_tender("pengumuman prakualifikasi"))

    def test_02_actual_db_representation(self):
        raw = "pengumuman prakualifikasi [...]"
        self.assertEqual(normalize_tahap(raw), CANONICAL_ACTIVE_STATUS)
        self.assertTrue(is_submittable_tender(raw))

    def test_03_case_variation(self):
        raw = "Pengumuman Prakualifikasi [...]"
        self.assertEqual(normalize_tahap(raw), CANONICAL_ACTIVE_STATUS)
        self.assertTrue(is_submittable_tender(raw))

    def test_04_whitespace_variation(self):
        raw = "  pengumuman prakualifikasi [...]  "
        self.assertEqual(normalize_tahap(raw), CANONICAL_ACTIVE_STATUS)
        self.assertTrue(is_submittable_tender(raw))

    def test_05_evaluasi_administrasi_excluded(self):
        self.assertFalse(is_submittable_tender("evaluasi administrasi"))
        self.assertFalse(is_submittable_tender("Evaluasi Administrasi [...]"))

    def test_06_evaluasi_teknis_excluded(self):
        self.assertFalse(is_submittable_tender("evaluasi teknis"))
        self.assertFalse(is_submittable_tender("Evaluasi Teknis [...]"))

    def test_07_pengumuman_pemenang_excluded(self):
        self.assertFalse(is_submittable_tender("pengumuman pemenang"))
        self.assertFalse(is_submittable_tender("Pengumuman Pemenang [...]"))

    def test_08_kontrak_excluded(self):
        self.assertFalse(is_submittable_tender("kontrak"))
        self.assertFalse(is_submittable_tender("penandatanganan kontrak"))

    def test_09_selesai_excluded(self):
        self.assertFalse(is_submittable_tender("selesai"))
        self.assertFalse(is_submittable_tender("tender selesai"))

    def test_10_batal_excluded(self):
        self.assertFalse(is_submittable_tender("batal"))
        self.assertFalse(is_submittable_tender("tender dibatalkan"))

    def test_11_none_excluded(self):
        self.assertEqual(normalize_tahap(None), "")
        self.assertFalse(is_submittable_tender(None))

    def test_12_empty_string_excluded(self):
        self.assertEqual(normalize_tahap(""), "")
        self.assertFalse(is_submittable_tender(""))
        self.assertFalse(is_submittable_tender("   "))

    def test_tender_object_dispatch(self):
        tender_active = TenderResult.objects.create(
            kode_instansi="test_inst",
            id_lelang="11111",
            nama_paket="Paket Aktif",
            tahap_saat_ini="pengumuman prakualifikasi [...]",
            hps=500_000_000,
        )
        tender_inactive = TenderResult.objects.create(
            kode_instansi="test_inst",
            id_lelang="22222",
            nama_paket="Paket Evaluasi",
            tahap_saat_ini="evaluasi administrasi",
            hps=500_000_000,
        )
        self.assertTrue(is_submittable_tender(tender_active))
        self.assertFalse(is_submittable_tender(tender_inactive))


class StatusTransitionAndPreservationTests(TestCase):
    """Tests 13 & 14: Historical preservation and Radar exclusion upon transition."""

    def setUp(self):
        self.company = CompanyProfile.objects.create(
            name="PT Solusi Digital",
            nib="1234567890123",
            npwp="01.234.567.8-901.000",
            penghasilan_tahunan=5_000_000_000,
            modal_disetor=1_000_000_000,
        )
        CompanyQualification.objects.create(
            company=self.company,
            category="izin_usaha",
            name="NIB Utama",
            kbli_code="62019",
            status="active",
        )
        self.tender = TenderResult.objects.create(
            kode_instansi="test_inst",
            id_lelang="999001",
            nama_paket="Pengadaan Sistem Informasi",
            tahap_saat_ini="pengumuman prakualifikasi [...]",
            hps=800_000_000,
            kbli_code="62019",
        )
        # Create historical AI Match and Opportunity records
        self.ai_match = AIMatchResult.objects.create(
            tender=self.tender,
            company=self.company,
            fit_score=85,
            eligibility_status="ELIGIBLE",
            mandatory_passed=True,
            summary="Cocok",
            cache_key="test_cache_key_trans",
        )
        self.opportunity = OpportunityScore.objects.create(
            tender=self.tender,
            company=self.company,
            final_score=88,
            classification="PRIORITAS_TINGGI",
            status="ready",
        )

    def test_13_historical_ai_match_preserved_after_status_transition(self):
        # 1. Initially submittable
        self.assertTrue(is_submittable_tender(self.tender))

        # 2. Status transitions to "evaluasi administrasi" (e.g. after crawler update)
        self.tender.tahap_saat_ini = "evaluasi administrasi"
        self.tender.save(update_fields=["tahap_saat_ini"])

        # 3. Tender is no longer submittable for NEW processing
        self.assertFalse(is_submittable_tender(self.tender))

        # 4. Historical AIMatchResult remains fully preserved in database
        persisted_match = AIMatchResult.objects.filter(
            tender=self.tender, company=self.company
        ).first()
        self.assertIsNotNone(persisted_match)
        self.assertEqual(persisted_match.fit_score, 85)
        self.assertEqual(persisted_match.eligibility_status, "ELIGIBLE")

        # 5. Historical OpportunityScore remains fully preserved
        persisted_opp = OpportunityScore.objects.filter(
            tender=self.tender, company=self.company
        ).first()
        self.assertIsNotNone(persisted_opp)
        self.assertEqual(persisted_opp.final_score, 88)

    def test_14_current_radar_excludes_transitioned_tender(self):
        # 1. When tender is active, it appears in Radar
        radar_before = TenderRadarService.get_radar(company_id=self.company.id)
        tender_ids_before = [r["id"] for r in radar_before["results"]]
        self.assertIn(self.tender.id, tender_ids_before)

        # 2. Tender transitions away from active status
        self.tender.tahap_saat_ini = "evaluasi administrasi"
        self.tender.save(update_fields=["tahap_saat_ini"])

        # 3. Tender is immediately excluded from current Radar
        radar_after = TenderRadarService.get_radar(company_id=self.company.id)
        tender_ids_after = [r["id"] for r in radar_after["results"]]
        self.assertNotIn(self.tender.id, tender_ids_after)
