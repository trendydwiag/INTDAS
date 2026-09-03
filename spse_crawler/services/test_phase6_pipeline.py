"""Phase 6D — Integration & Data Quality Hardening tests.

Exercises the whole Phase 6 pipeline as one coherent system:

    TenderResult -> Participants -> Winner -> Entity Resolution -> CompanyProfile
              -> Company Intelligence -> Competitive Intelligence API

Covers (per the Phase 6D scope):
  1. Critical integration: crawl #1 then crawl #2 -> no duplicates, stable identity.
  2. Historical retention regression: auto-purge (flush_non_retained) retains
     awarded tender + participants + winner + company + links; active tenders
     retained; legitimate aborted/stale records still removable.
  3. Winner integration: missing/malformed winner doesn't break the crawl or
     destroy existing winner; idempotent re-crawl; changed values update.
  4. Entity resolution integration: cases A (participant NPWP==company NPWP),
     B (winner NPWP==company NPWP), C (same name, diff/no NPWP -> no link),
     D (duplicate company NPWP -> conflict), E (participant NPWP != winner NPWP
     -> not inferred same company).
  5. Source immutability: linking never rewrites source-derived fields.
  6. Intelligence API + coverage metadata honesty (win-rate denominator).
  7. Legacy data: rows created before Phase 6 (company=NULL) are safe to read;
     no wrong-company assignment; no destructive backfill executed.
  8. Partial failure: participant saved + winner unavailable does not destroy
     participant history.
"""

from django.test import TestCase

from spse_crawler.companies.models import CompanyProfile
from spse_crawler.services.competitive_intelligence import (
    get_company_intelligence,
    get_tender_winner,
)
from spse_crawler.services.entity_resolution import mask_identifier, normalize_npwp
from spse_crawler.services.purger import flush_non_retained
from spse_crawler.services.tender_participant_store import sync_tender_participants
from spse_crawler.services.tender_winner_store import sync_tender_winner
from spse_crawler.web.models import TenderParticipant, TenderResult, TenderWinner


def _mk_company(name="PT Alpha", nib="912000001", npwp=""):
    return CompanyProfile.objects.create(
        name=name,
        nib=nib,
        npwp=npwp,
        modal_disetor=2_000_000_000,
        penghasilan_tahunan=5_000_000_000,
        is_active=True,
    )


def _mk_tender(tahap="pengumuman pemenang", idl="T-001", **kwargs):
    defaults = {
        "nama_paket": "Sistem Informasi",
        "instansi": "Kementerian",
        "hps": 1_000_000_000,
        "jenis_pengadaan": "Pengadaan Barang",
        "tahap_saat_ini": tahap,
        "is_prakualifikasi": "prakualifikasi" in tahap.lower(),
        "kbli_code": "62019",
        "peserta_count": 3,
    }
    defaults.update(kwargs)
    # Model the real crawler, which idempotently updates by (kode, id_lelang).
    obj, _ = TenderResult.objects.update_or_create(
        kode_instansi="dki",
        id_lelang=idl,
        defaults=defaults,
    )
    return obj


def _participants():
    return [
        {"name": "PT Alpha", "npwp": "1111111111"},
        {"name": "PT Beta", "npwp": "2222222222"},
    ]


def _winner_dict(name="PT Alpha", npwp="1111111111", value=980_000):
    return {
        "company_name": name,
        "npwp": npwp,
        "alamat": "Jl. Utama 1",
        "harga_penawaran": 1_000_000,
        "harga_terkoreksi": 990_000,
        "harga_negosiasi": value,
        "winning_value": value,
    }


class CriticalIntegrationTests(TestCase):
    """Crawl #1 then crawl #2 -> no duplicates, stable identity, stable history."""

    def test_two_crawls_produce_no_duplicates_and_stable_identity(self):
        c_alpha = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        _mk_company(name="PT Beta", nib="912000002", npwp="2222222222")

        # Crawl #1
        t1 = _mk_tender(idl="T-001")
        sync_tender_participants(t1, _participants())
        sync_tender_winner(t1, _winner_dict())

        self.assertEqual(TenderResult.objects.count(), 1)
        self.assertEqual(TenderParticipant.objects.filter(tender=t1).count(), 2)
        self.assertEqual(TenderWinner.objects.filter(tender=t1).count(), 1)
        self.assertEqual(CompanyProfile.objects.count(), 2)
        self.assertEqual(
            TenderParticipant.objects.get(tender=t1, name="PT Alpha").company_id,
            c_alpha.id,
        )
        self.assertEqual(
            TenderWinner.objects.get(tender=t1).company_id, c_alpha.id
        )

        winner = TenderWinner.objects.get(tender=t1)

        # Crawl #2 — same data re-synced (idempotent)
        t2 = _mk_tender(idl="T-001")
        sync_tender_participants(t2, _participants())
        sync_tender_winner(t2, _winner_dict())

        self.assertEqual(TenderResult.objects.count(), 1)
        self.assertEqual(TenderParticipant.objects.filter(tender=t1).count(), 2)
        self.assertEqual(TenderWinner.objects.filter(tender=t1).count(), 1)
        self.assertEqual(CompanyProfile.objects.count(), 2)

        # Identity remains stable (same UUID/PK architecture, links unchanged)
        self.assertEqual(
            TenderParticipant.objects.get(tender=t1, name="PT Alpha").company_id,
            c_alpha.id,
        )
        self.assertEqual(
            TenderWinner.objects.get(tender=t1).company_id, c_alpha.id
        )
        self.assertEqual(winner.pk, TenderWinner.objects.get(tender=t1).pk)

        # Identity (company links) and source fields remain stable across the
        # re-crawl. NOTE: `updated_at` is NOT asserted to be identical — Django's
        # update_or_create() re-touches auto_now fields on the update path, so
        # updated_at means "last re-synced" rather than "last data change". That
        # is intended/benign (duplicates are still prevented, links stable).
        self.assertEqual(
            TenderParticipant.objects.get(tender=t1, name="PT Alpha").name,
            "PT Alpha",
        )
        self.assertEqual(
            TenderParticipant.objects.get(tender=t1, name="PT Alpha").npwp,
            "1111111111",
        )
        self.assertEqual(
            TenderWinner.objects.get(tender=t1).company_name, "PT Alpha"
        )

    def test_intelligence_available_after_crawl(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        sync_tender_winner(t, _winner_dict())
        c = CompanyProfile.objects.get(nib="912000001")
        res = get_company_intelligence(c.id)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["data_available"])
        self.assertEqual(res["statistics"]["total_participation"], 1)
        self.assertEqual(res["statistics"]["total_wins"], 1)

    def test_no_duplicate_winner_on_rerun(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_winner(t, _winner_dict())
        sync_tender_winner(t, _winner_dict())
        self.assertEqual(TenderWinner.objects.filter(tender=t).count(), 1)


class HistoricalRetentionRegressionTests(TestCase):
    """Auto-purge (flush_non_retained) must retain awarded tender + all children."""

    def test_awarded_pipeline_survives_auto_purge(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(tahap="Pengumuman Pemenang", idl="T-A")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        sync_tender_winner(t, _winner_dict())

        flush_non_retained()

        self.assertTrue(TenderResult.objects.filter(pk=t.pk).exists())
        self.assertEqual(TenderParticipant.objects.filter(tender=t).count(), 1)
        self.assertEqual(TenderWinner.objects.filter(tender=t).count(), 1)
        self.assertTrue(CompanyProfile.objects.filter(pk=c.pk).exists())
        self.assertEqual(
            TenderParticipant.objects.get(tender=t).company_id, c.id
        )
        self.assertEqual(TenderWinner.objects.get(tender=t).company_id, c.id)
        # Intelligence still available after the purge.
        self.assertTrue(get_company_intelligence(c.id)["data_available"])

    def test_active_prakualifikasi_survives_auto_purge(self):
        _mk_tender(tahap="pengumuman prakualifikasi", idl="T-ACT", is_prakualifikasi=True)
        sync_tender_participants(
            TenderResult.objects.get(id_lelang="T-ACT"),
            [{"name": "PT Alpha", "npwp": "1111111111"}],
        )
        flush_non_retained()
        self.assertEqual(TenderResult.objects.filter(id_lelang="T-ACT").count(), 1)
        self.assertEqual(
            TenderParticipant.objects.filter(
                tender__id_lelang="T-ACT"
            ).count(),
            1,
        )

    def test_legitimate_aborted_still_removable(self):
        t = _mk_tender(tahap="tender gagal", idl="T-BAD", is_prakualifikasi=False)
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        flush_non_retained()
        self.assertEqual(TenderResult.objects.filter(pk=t.pk).count(), 0)
        self.assertEqual(TenderParticipant.objects.filter(tender=t).count(), 0)


class WinnerIntegrationTests(TestCase):
    def test_missing_winner_does_not_break_or_destroy(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_winner(t, _winner_dict())
        # Re-crawl with no winner -> no-op, existing winner preserved.
        self.assertFalse(sync_tender_winner(t, None))
        self.assertEqual(TenderWinner.objects.filter(tender=t).count(), 1)

    def test_malformed_winner_not_stored_and_does_not_destroy(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_winner(t, _winner_dict())
        # Empty company name -> not persisted, existing preserved.
        self.assertFalse(sync_tender_winner(t, {"company_name": "  ", "npwp": "1"}))
        self.assertEqual(TenderWinner.objects.filter(tender=t).count(), 1)
        self.assertEqual(
            TenderWinner.objects.get(tender=t).company_name, "PT Alpha"
        )

    def test_changed_source_values_update(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_winner(t, _winner_dict(value=980_000))
        sync_tender_winner(t, _winner_dict(value=900_000))
        w = TenderWinner.objects.get(tender=t)
        self.assertEqual(w.winning_value, 900_000)
        self.assertEqual(w.harga_negosiasi, 900_000)

    def test_winner_only_fetched_where_retained(self):
        """Parse-level guard: winner integration only for retained awarded tahap."""
        from spse_crawler.parsers.detail import DetailParser
        self.assertTrue(DetailParser.is_retained_tahap("pengumuman pemenang"))
        self.assertFalse(DetailParser.is_retained_tahap("penawaran"))
        self.assertFalse(DetailParser.is_retained_tahap("pembatalan"))

    def test_winner_url_builder_uses_verified_evaluasi_path(self):
        from spse_crawler.parsers.detail import DetailParser
        url = "https://spse.inaproc.id/dki/lelang/98765/pengumumanlelang"
        self.assertEqual(
            DetailParser._build_winner_url(url),
            "https://spse.inaproc.id/dki/evaluasi/98765/pemenang",
        )


class EntityResolutionIntegrationTests(TestCase):
    """Cases A–E from the Phase 6D scope."""

    def test_case_a_participant_npwp_equals_company_npwp(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        self.assertEqual(
            TenderParticipant.objects.get(tender=t).company_id, c.id
        )
        self.assertEqual(
            TenderParticipant.objects.get(tender=t).resolution_status,
            "EXACT_NPWP",
        )

    def test_case_b_winner_npwp_equals_company_npwp(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_winner(t, _winner_dict())
        self.assertEqual(TenderWinner.objects.get(tender=t).company_id, c.id)
        self.assertEqual(
            TenderWinner.objects.get(tender=t).resolution_status, "EXACT_NPWP"
        )

    def test_case_c_same_name_no_npwp_no_link(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": ""}])
        p = TenderParticipant.objects.get(tender=t)
        self.assertIsNone(p.company)
        self.assertEqual(p.resolution_status, "CANDIDATE_ONLY")

    def test_case_c_same_name_different_npwp_no_link(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "9999999999"}])
        p = TenderParticipant.objects.get(tender=t)
        self.assertIsNone(p.company)
        self.assertNotEqual(p.resolution_status, "EXACT_NPWP")

    def test_case_d_duplicate_company_npwp_conflict(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        _mk_company(name="PT Beta", nib="912000002", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        p = TenderParticipant.objects.get(tender=t)
        self.assertIsNone(p.company)
        self.assertEqual(p.resolution_status, "IDENTITY_CONFLICT")

    def test_case_e_participant_npwp_ne_winner_npwp_not_inferred_same(self):
        c_alpha = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        c_beta = _mk_company(name="PT Beta", nib="912000002", npwp="2222222222")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(
            t, [{"name": "PT Alpha", "npwp": "1111111111"}]
        )
        # Winner is a DIFFERENT company than the participant.
        sync_tender_winner(t, _winner_dict(name="PT Beta", npwp="2222222222"))
        p = TenderParticipant.objects.get(tender=t)
        w = TenderWinner.objects.get(tender=t)
        self.assertEqual(p.company_id, c_alpha.id)
        self.assertEqual(w.company_id, c_beta.id)
        self.assertNotEqual(p.company_id, w.company_id)


class SourceImmutabilityTests(TestCase):
    def test_participant_source_fields_preserved_after_link(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        p = TenderParticipant.objects.get(tender=t)
        # Name/NPWP remain source-derived, NOT replaced by CompanyProfile values.
        self.assertEqual(p.name, "PT Alpha")
        self.assertEqual(p.npwp, "1111111111")

    def test_winner_source_fields_preserved_after_link(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        sync_tender_winner(t, _winner_dict())
        w = TenderWinner.objects.get(tender=t)
        self.assertEqual(w.company_name, "PT Alpha")
        self.assertEqual(w.npwp, "1111111111")
        self.assertEqual(w.alamat, "Jl. Utama 1")
        self.assertEqual(w.harga_penawaran, 1_000_000)
        self.assertEqual(w.harga_terkoreksi, 990_000)
        self.assertEqual(w.harga_negosiasi, 980_000)


class CoverageMetadataTests(TestCase):
    def test_winner_coverage_denominator_is_honest(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        # 3 participations, only 1 has a captured winner.
        for i in range(3):
            t = _mk_tender(idl=f"T-{i}")
            sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
            if i == 0:
                sync_tender_winner(t, _winner_dict())
        res = get_company_intelligence(c.id)
        self.assertEqual(res["statistics"]["total_participation"], 3)
        self.assertEqual(res["statistics"]["total_wins"], 1)
        # win_rate = 1/3 with an explicit, honest basis (captured subset only).
        self.assertEqual(res["statistics"]["win_rate"], 0.3333)
        self.assertIn("captured", res["statistics"]["win_rate_basis"])
        cov = res["coverage"]
        self.assertEqual(cov["participation_coverage"], 3)
        self.assertEqual(cov["winner_coverage"], 0.3333)

    def test_win_rate_not_presented_as_universe_authoritative(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        for i in range(4):
            t = _mk_tender(idl=f"T-{i}")
            sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
            if i == 0:
                sync_tender_winner(t, _winner_dict())
        res = get_company_intelligence(c.id)
        # 1 win / 4 captured participations. The rate is explicitly framed as a
        # captured-subset figure with a disclaimer — never an authoritative
        # universe-wide win rate.
        self.assertEqual(res["statistics"]["win_rate"], 0.25)
        basis = res["statistics"]["win_rate_basis"].lower()
        self.assertIn("captured", basis)
        self.assertIn("universe", basis)  # explicit disclaimer present


class LegacyDataTests(TestCase):
    def test_legacy_null_company_links_are_safe_to_read(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        # Simulate rows created BEFORE Phase 6 (company=NULL).
        TenderParticipant.objects.create(
            tender=t, name="PT Alpha", npwp="1111111111", resolution_status=""
        )
        TenderWinner.objects.create(
            tender=t, company_name="PT Alpha", npwp="1111111111",
            resolution_status="", winning_value=1_000_000,
        )
        # Reading the API must not crash or silently overwrite.
        res = get_company_intelligence(c.id)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(get_tender_winner(t.id)["status"], "ok")
        # Legacy rows were NOT mutated by a read.
        self.assertIsNone(
            TenderParticipant.objects.get(tender=t, name="PT Alpha").company
        )

    def test_legacy_no_npwp_company_is_not_wrongly_linked(self):
        # CompanyProfile with missing NPWP (legacy) must not be auto-linked by
        # name alone when a participant provides a *different* identity.
        _mk_company(name="PT Alpha", nib="912000001", npwp="")
        t = _mk_tender(idl="T-001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        p = TenderParticipant.objects.get(tender=t)
        self.assertIsNone(p.company)
        self.assertEqual(p.resolution_status, "CANDIDATE_ONLY")


class PartialFailureTests(TestCase):
    def test_participant_saved_winner_unavailable_preserves_history(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-001")
        # Participant ingest succeeds.
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        # Winner source unavailable -> store returns False, no row, no crash.
        self.assertFalse(sync_tender_winner(t, None))
        self.assertEqual(TenderParticipant.objects.filter(tender=t).count(), 1)
        self.assertEqual(TenderWinner.objects.filter(tender=t).count(), 0)


class IntegritySanityTests(TestCase):
    """Non-destructive integrity assertions on the persisted graph."""

    def test_mask_identifier_never_exposes_full_value(self):
        full = "012345678901000"
        masked = mask_identifier(full)
        self.assertNotIn("012345678901000", masked)
        self.assertNotIn(full, masked)

    def test_masked_source_npwp_never_matches(self):
        # SPSE-masked NPWP must never be treated as a real identifier for matching.
        self.assertIsNone(normalize_npwp("00*5**7****42**0"))
