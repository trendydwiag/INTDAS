"""Tests for Phase 6C — Company Identity, Entity Resolution & Company Intelligence.

Covers:
  - NPWP normalization (digits, masking, empty/null/malformed/alphabetics)
  - entity resolution (exact NPWP -> auto match; NIB exact -> auto match;
    name-only -> CANDIDATE_ONLY (never linked); same name + different NPWP ->
    not linked; duplicate CompanyProfile NPWP -> IDENTITY_CONFLICT; no identity
    -> UNRESOLVED)
  - participant linking (EXACT links; name-only doesn't; source name preserved)
  - winner linking (same)
  - company intelligence metrics (participation / wins / win-rate / history /
    institution / location / KBLI / coverage limitation)
  - upgraded get_tender_winner returns real data when a winner row exists
"""

from spse_crawler.companies.models import CompanyProfile
from spse_crawler.web.models import TenderParticipant, TenderResult, TenderWinner
from spse_crawler.services.competitive_intelligence import (
    get_company_intelligence,
    get_tender_winner,
)
from spse_crawler.services.entity_resolution import (
    AUTO_LINK_STATUSES,
    ResolutionResult,
    ResolutionStatus,
    apply_resolution_to,
    normalize_npwp,
    resolve_company_identity,
)
from spse_crawler.services.tender_participant_store import sync_tender_participants
from spse_crawler.services.tender_winner_store import sync_tender_winner

from django.test import TestCase


def _mk_company(name="PT Alpha", nib="9120001234567", npwp=""):
    return CompanyProfile.objects.create(
        name=name,
        nib=nib,
        npwp=npwp,
        modal_disetor=2_000_000_000,
        penghasilan_tahunan=5_000_000_000,
        is_active=True,
    )


def _mk_tender(kode="000001", idl="T-001", name="Sistem Informasi"):
    return TenderResult.objects.create(
        kode_instansi=kode,
        id_lelang=idl,
        nama_paket=name,
        instansi="Kementerian",
        tahap_saat_ini="Penawaran",
        kbli_code="62019",
        peserta_count=3,
        jadwal_json=[{"tahap": "Penawaran", "sampai": "01-01-2030"}],
    )


# ---------------------------------------------------------------------------
# NPWP normalization
# ---------------------------------------------------------------------------

class NormalizeNpwpTests(TestCase):
    def test_digits_extracted(self):
        self.assertEqual(normalize_npwp("01.234.567.8-901.000"), "012345678901000")
        self.assertEqual(normalize_npwp("012345678901000"), "012345678901000")

    def test_empty_and_none(self):
        self.assertIsNone(normalize_npwp(None))
        self.assertIsNone(normalize_npwp(""))
        self.assertIsNone(normalize_npwp("   "))

    def test_masked_unusable(self):
        # SPSE masks partly: '00*5**7****42**0'
        self.assertIsNone(normalize_npwp("00*5**7****42**0"))
        self.assertIsNone(normalize_npwp("1234x567"))
        self.assertIsNone(normalize_npwp("1234X567"))

    def test_alphabetic_rejected(self):
        self.assertIsNone(normalize_npwp("ABCDEF"))
        self.assertIsNone(normalize_npwp("12ABC"))

    def test_different_preserves_difference(self):
        self.assertNotEqual(normalize_npwp("1111111111"), normalize_npwp("2222222222"))


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

class ResolveCompanyIdentityTests(TestCase):
    def test_exact_npwp_auto_match(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="012345678901000")
        res = resolve_company_identity(name="PT Alpha", npwp="01.234.567.8-901.000")
        self.assertEqual(res.status, ResolutionStatus.EXACT_NPWP)
        self.assertEqual(res.company.id, c.id)
        self.assertIn(res.status, AUTO_LINK_STATUSES)

    def test_exact_nib_auto_match(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="")
        res = resolve_company_identity(name="PT Alpha", nib="9120 0000 1")
        self.assertEqual(res.status, ResolutionStatus.EXACT_OFFICIAL_IDENTIFIER)
        self.assertEqual(res.company.id, c.id)
        self.assertIn(res.status, AUTO_LINK_STATUSES)

    def test_name_only_candidate_not_auto_linked(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="012345678901000")
        res = resolve_company_identity(name="PT Alpha")
        self.assertEqual(res.status, ResolutionStatus.CANDIDATE_ONLY)
        self.assertIsNone(res.company)
        self.assertEqual(tuple(x.id for x in res.candidates), (c.id,))
        self.assertNotIn(res.status, AUTO_LINK_STATUSES)

    def test_name_only_with_no_company_unresolved(self):
        res = resolve_company_identity(name="PT Nonexistent")
        self.assertEqual(res.status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(res.company)

    def test_no_identity_unresolved(self):
        res = resolve_company_identity(name="", npwp="")
        self.assertEqual(res.status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(res.company)

    def test_duplicate_npwp_conflict_not_linked(self):
        # Distinct NIBs but same NPWP -> integrity conflict, never picked arbitrarily.
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        _mk_company(name="PT Beta", nib="912000002", npwp="1111111111")
        res = resolve_company_identity(name="PT Alpha", npwp="1111111111")
        self.assertEqual(res.status, ResolutionStatus.IDENTITY_CONFLICT)
        self.assertIsNone(res.company)
        self.assertEqual(len(res.candidates), 2)
        self.assertNotIn(res.status, AUTO_LINK_STATUSES)

    def test_same_name_different_npwp_not_linked(self):
        # A company shares the name but has a different NPWP -> never auto-merge.
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        res = resolve_company_identity(name="PT Alpha", npwp="9999999999")
        self.assertEqual(res.status, ResolutionStatus.CANDIDATE_ONLY)
        self.assertIsNone(res.company)
        self.assertNotIn(res.status, AUTO_LINK_STATUSES)

    def test_masked_npwp_never_matches(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        res = resolve_company_identity(name="PT Alpha", npwp="00*5**7****42**0")
        # masked source cannot be matched; name-only candidate at best.
        self.assertEqual(res.status, ResolutionStatus.CANDIDATE_ONLY)
        self.assertIsNone(res.company)
        self.assertEqual(tuple(x.id for x in res.candidates), (c.id,))


class ApplyResolutionToTests(TestCase):
    def setUp(self):
        self.c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")

    def test_exact_links_and_records_status(self):
        res = resolve_company_identity(name="PT Alpha", npwp="1111111111")
        p = TenderParticipant(name="PT Alpha", npwp="1111111111")
        self.assertTrue(apply_resolution_to(p, res))
        self.assertEqual(p.company_id, self.c.id)
        self.assertEqual(p.resolution_status, "EXACT_NPWP")

    def test_exact_link_not_clobbered_by_weaker_match(self):
        p = TenderParticipant(
            name="PT Alpha", npwp="1111111111",
            company=self.c, resolution_status="EXACT_NPWP",
        )
        weaker = ResolutionResult(
            status=ResolutionStatus.CANDIDATE_ONLY, company=None
        )
        self.assertFalse(apply_resolution_to(p, weaker))
        self.assertEqual(p.company_id, self.c.id)
        self.assertEqual(p.resolution_status, "EXACT_NPWP")


# ---------------------------------------------------------------------------
# Participant linking
# ---------------------------------------------------------------------------

class ParticipantLinkingTests(TestCase):
    def test_exact_npwp_links_participant(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender()
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        p = TenderParticipant.objects.get(tender=t, name="PT Alpha")
        self.assertEqual(p.company_id, c.id)
        self.assertEqual(p.resolution_status, "EXACT_NPWP")

    def test_name_only_does_not_link(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender()
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": ""}])
        p = TenderParticipant.objects.get(tender=t, name="PT Alpha")
        self.assertIsNone(p.company)
        self.assertEqual(p.resolution_status, "CANDIDATE_ONLY")

    def test_no_matching_company_unresolved(self):
        t = _mk_tender()
        sync_tender_participants(t, [{"name": "PT Unknown", "npwp": "9999999999"}])
        p = TenderParticipant.objects.get(tender=t, name="PT Unknown")
        self.assertIsNone(p.company)
        self.assertEqual(p.resolution_status, "UNRESOLVED")

    def test_source_name_preserved(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender()
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        p = TenderParticipant.objects.get(tender=t, name="PT Alpha")
        # source-derived name is never replaced by the CompanyProfile name.
        self.assertEqual(p.name, "PT Alpha")


# ---------------------------------------------------------------------------
# Winner linking
# ---------------------------------------------------------------------------

class WinnerLinkingTests(TestCase):
    def _winner_dict(self, name="PT Alpha", npwp="1111111111"):
        return {
            "company_name": name,
            "npwp": npwp,
            "alamat": "Jl. Utama 1",
            "harga_penawaran": 1_000_000,
            "harga_terkoreksi": 990_000,
            "harga_negosiasi": 980_000,
            "winning_value": 980_000,
        }

    def test_exact_npwp_links_winner(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender()
        sync_tender_winner(t, self._winner_dict())
        w = TenderWinner.objects.get(tender=t)
        self.assertEqual(w.company_id, c.id)
        self.assertEqual(w.resolution_status, "EXACT_NPWP")

    def test_name_only_does_not_link_winner(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender()
        sync_tender_winner(t, self._winner_dict(npwp=""))
        w = TenderWinner.objects.get(tender=t)
        self.assertIsNone(w.company)
        self.assertEqual(w.resolution_status, "CANDIDATE_ONLY")

    def test_winner_source_name_preserved(self):
        _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender()
        sync_tender_winner(t, self._winner_dict())
        w = TenderWinner.objects.get(tender=t)
        self.assertEqual(w.company_name, "PT Alpha")


# ---------------------------------------------------------------------------
# Company intelligence (metrics + coverage limitation)
# ---------------------------------------------------------------------------

class CompanyIntelligenceTests(TestCase):
    def test_no_data_not_available(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        res = get_company_intelligence(c.id)
        self.assertFalse(res["data_available"])
        self.assertIsNone(res["statistics"]["total_participation"])
        self.assertIsNone(res["statistics"]["win_rate"])

    def test_participation_and_wins_metrics(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        other = _mk_company(name="PT Beta", nib="912000002", npwp="2222222222")
        for i in range(4):
            t = _mk_tender(idl=f"T-{i}")
            sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
            if i == 0:
                sync_tender_winner(t, {"company_name": "PT Alpha", "npwp": "1111111111", "winning_value": 1_000_000})
        self.assertEqual(other.participations.count(), 0)
        res = get_company_intelligence(c.id)
        self.assertTrue(res["data_available"])
        self.assertEqual(res["statistics"]["total_participation"], 4)
        self.assertEqual(res["statistics"]["total_wins"], 1)
        self.assertEqual(res["statistics"]["total_winning_value"], 1_000_000)
        self.assertEqual(res["statistics"]["win_rate"], 0.25)
        self.assertEqual(len(res["recent_tenders"]), 4)
        self.assertEqual(len(res["recent_wins"]), 1)

    def test_frequency_and_coverage_metadata(self):
        c = _mk_company(name="PT Alpha", nib="912000001", npwp="1111111111")
        t = _mk_tender(idl="T-1", name="Sistem Informasi", kode="000001")
        sync_tender_participants(t, [{"name": "PT Alpha", "npwp": "1111111111"}])
        res = get_company_intelligence(c.id)
        self.assertEqual(res["institution_frequency"], [{"instansi": "Kementerian", "count": 1}])
        self.assertEqual(res["kbli_frequency"], [{"kbli_code": "62019", "count": 1}])
        # coverage limitation is exposed honestly, never a fake 100% universe.
        cov = res["coverage"]
        self.assertEqual(cov["participation_coverage"], 1)
        self.assertIn("captured", cov["winner_coverage_basis"])
        self.assertIn("subset", res["data_limitation"])


class TenderWinnerIntelligenceTests(TestCase):
    def test_winner_returns_real_data(self):
        t = _mk_tender()
        sync_tender_winner(t, {"company_name": "PT Alpha", "npwp": "1111111111", "winning_value": 1_000_000})
        res = get_tender_winner(t.id)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["winner"]["company_name"], "PT Alpha")
        self.assertEqual(res["winner"]["winning_value"], 1_000_000)

    def test_winner_not_available_when_missing(self):
        t = _mk_tender()
        res = get_tender_winner(t.id)
        self.assertEqual(res["status"], "not_available")
        self.assertIsNone(res["winner"])
