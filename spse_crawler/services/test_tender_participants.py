"""Tests for Phase 5 — Competitive Intelligence data foundation.

Covers:
- TenderParticipant / TenderWinner model creation, uniqueness, relation,
  nullable fields and cascade.
- Parser participant + winner extraction (incl. missing / malformed HTML and
  different tender stages) and the awarded-stage retention gate.
- Idempotent persistence (first crawl N, second crawl still N, enrichment
  update, unavailable data does not destroy existing).
- Full-suite regression is enforced separately when the whole suite runs.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from spse_crawler.companies.models import CompanyProfile
from spse_crawler.models.tender import TenderDetail
from spse_crawler.parsers.detail import DetailParser
from spse_crawler.services.tender_participant_store import (
    sync_tender_participants,
)
from spse_crawler.web.models import TenderParticipant, TenderResult, TenderWinner


def _mk_tender(**kwargs) -> TenderResult:
    defaults = {
        "kode_instansi": "dki",
        "id_lelang": "12345",
        "nama_paket": "Pengadaan Server",
        "instansi": "Dinas Kominfo",
        "hps": 500_000_000,
        "jenis_pengadaan": "Pengadaan Barang",
        "tahap_saat_ini": "pengumuman prakualifikasi",
        "is_prakualifikasi": True,
    }
    defaults.update(kwargs)
    return TenderResult.objects.create(**defaults)


def _participants_html(*rows) -> str:
    """Build a minimal /peserta table HTML.

    Each row is ``(no, nama, npwp)``; ``None`` name means it should be skipped.
    """
    cells = []
    for no, nama, npwp in rows:
        name_td = f"<td>{nama}</td>" if nama is not None else "<td></td>"
        cells.append(f"<tr><td>{no}</td>{name_td}<td>{npwp}</td></tr>")
    body = "".join(cells)
    return f"<table><thead><tr><th>No</th><th>Nama</th><th>NPWP</th></tr></thead><tbody>{body}</tbody></table>"


def _parser_instance() -> DetailParser:
    """Build a DetailParser without connecting to the network.

    The static parse helpers do not touch instance state, so a bare instance is
    enough (avoids constructing httpx / Playwright engines in tests).
    """
    obj = object.__new__(DetailParser)
    return obj


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class TenderParticipantModelTests(TestCase):
    def test_create_participant(self):
        tender = _mk_tender()
        p = TenderParticipant.objects.create(
            tender=tender,
            name="PT Maju Jaya",
            npwp="012345678901234",
            source_url="https://spse.inaproc.id/dki/lelang/12345/peserta",
            source_type="peserta_page",
            source_fetched_at=timezone.now(),
        )
        self.assertEqual(p.name, "PT Maju Jaya")
        self.assertEqual(p.npwp, "012345678901234")
        self.assertEqual(p.tender, tender)
        self.assertIsNotNone(p.created_at)
        self.assertIsNotNone(p.updated_at)
        self.assertIn(p, tender.participants.all())

    def test_unique_tender_and_name(self):
        tender = _mk_tender()
        TenderParticipant.objects.create(tender=tender, name="PT Maju Jaya")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TenderParticipant.objects.create(tender=tender, name="PT Maju Jaya")
        # Same name on a different tender is allowed
        other = _mk_tender(id_lelang="99999")
        TenderParticipant.objects.create(tender=other, name="PT Maju Jaya")

    def test_cascade_delete_with_tender(self):
        tender = _mk_tender()
        TenderParticipant.objects.create(tender=tender, name="PT Maju Jaya")
        self.assertEqual(TenderParticipant.objects.filter(tender=tender).count(), 1)
        tender_id = tender.id
        tender.delete()
        self.assertEqual(TenderParticipant.objects.filter(tender_id=tender_id).count(), 0)

    def test_empty_npwp_is_default(self):
        tender = _mk_tender()
        p = TenderParticipant.objects.create(tender=tender, name="PT Tanpa NPWP")
        self.assertEqual(p.npwp, "")


class TenderWinnerModelTests(TestCase):
    def test_create_winner_foundation(self):
        tender = _mk_tender()
        w = TenderWinner.objects.create(
            tender=tender,
            company_name="PT Pemenang",
            winning_value=400_000_000,
            source_fetched_at=timezone.now(),
        )
        self.assertEqual(w.company_name, "PT Pemenang")
        self.assertEqual(w.winning_value, 400_000_000)
        self.assertEqual(w.source_type, "pemenang_page")

    def test_one_to_one_tender(self):
        tender = _mk_tender()
        TenderWinner.objects.create(tender=tender, company_name="A")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TenderWinner.objects.create(tender=tender, company_name="B")

    def test_winning_value_nullable(self):
        tender = _mk_tender()
        w = TenderWinner.objects.create(tender=tender, company_name="A")
        self.assertIsNone(w.winning_value)


# ---------------------------------------------------------------------------
# Parser extraction
# ---------------------------------------------------------------------------
class ParticipantParserTests(TestCase):
    def test_parse_participants(self):
        html = _participants_html(
            (1, "PT Maju Jaya", "012345678901234"),
            (2, "CV Sentosa", "987654321098765"),
            (3, "PT Karya Nusa", "111222333444555"),
        )
        result = _parser_instance()._parse_participants(html)
        self.assertEqual(
            result,
            [
                {"name": "PT Maju Jaya", "npwp": "012345678901234"},
                {"name": "CV Sentosa", "npwp": "987654321098765"},
                {"name": "PT Karya Nusa", "npwp": "111222333444555"},
            ],
        )

    def test_parse_participants_skips_empty_name(self):
        html = _participants_html(
            (1, "PT Maju Jaya", "012345678901234"),
            (2, None, ""),
            (3, "CV Sentosa", ""),
        )
        result = _parser_instance()._parse_participants(html)
        self.assertEqual(
            result,
            [
                {"name": "PT Maju Jaya", "npwp": "012345678901234"},
                {"name": "CV Sentosa", "npwp": ""},
            ],
        )

    def test_parse_participants_missing_header_only(self):
        html = "<table></table>"
        self.assertEqual(_parser_instance()._parse_participants(html), [])

    def test_parse_participants_malformed(self):
        html = "not html at all <<<"
        self.assertEqual(_parser_instance()._parse_participants(html), [])

    def test_parse_participants_empty_string(self):
        self.assertEqual(_parser_instance()._parse_participants(""), [])

    def test_count_matches_participant_rows(self):
        html = _participants_html(
            (1, "PT A", "123"),
            (2, "PT B", "456"),
            (3, "PT C", "789"),
        )
        parser = _parser_instance()
        self.assertEqual(parser._parse_peserta_count(html), 3)
        self.assertEqual(len(parser._parse_participants(html)), 3)


class TahapGateTests(TestCase):
    def test_active_eligible(self):
        self.assertTrue(DetailParser.is_eligible_tahap("pengumuman prakualifikasi"))

    def test_awarded_retained_not_eligible(self):
        for tahap in ["penetapan pemenang", "kontrak", "rekomendasi pemenang"]:
            self.assertFalse(DetailParser.is_eligible_tahap(tahap), tahap)
            self.assertTrue(DetailParser.is_retained_tahap(tahap), tahap)

    def test_aborted_not_retained(self):
        for tahap in [
            "pembatalan", "batal", "gagal", "tender gagal",
            "selesai", "tender selesai",
            "pascakualifikasi", "penandatanganan", "penandatanganan kontrak",
        ]:
            self.assertFalse(DetailParser.is_retained_tahap(tahap), tahap)

    def test_unknown_not_retained(self):
        self.assertFalse(DetailParser.is_retained_tahap("tidak diketahui"))

    def test_empty_not_retained(self):
        self.assertFalse(DetailParser.is_retained_tahap(""))
        self.assertFalse(DetailParser.is_retained_tahap(None))


class TenderDetailPydanticTests(TestCase):
    def test_scraped_at_is_timezone_aware(self):
        """Verify TenderDetail.scraped_at has non-None tzinfo and utcoffset."""
        d = TenderDetail(
            kode_instansi="dki",
            id_lelang="12345",
            tahap_saat_ini="pengumuman prakualifikasi",
        )
        self.assertIsNotNone(d.scraped_at.tzinfo)
        self.assertIsNotNone(d.scraped_at.utcoffset())

    def test_scraped_at_django_persistence_no_naive_warning(self):
        """Verify passing detail.scraped_at to Django DateTimeField produces zero naive warnings."""
        import warnings
        tender = _mk_tender()
        d = TenderDetail(
            kode_instansi="dki",
            id_lelang="12345",
            tahap_saat_ini="pengumuman prakualifikasi",
        )
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            tp, _ = TenderParticipant.objects.update_or_create(
                tender=tender,
                name="PT Timezone Test",
                defaults={"source_fetched_at": d.scraped_at},
            )
            naive_warnings = [
                w for w in captured
                if issubclass(w.category, RuntimeWarning) and "naive datetime" in str(w.message).lower()
            ]
            self.assertEqual(len(naive_warnings), 0)
            tp.delete()

    def test_participants_default_empty(self):
        d = TenderDetail(
            kode_instansi="dki",
            id_lelang="12345",
            tahap_saat_ini="pengumuman prakualifikasi",
        )
        self.assertEqual(d.participants, [])
        self.assertEqual(d.peserta_count, 0)

    def test_participants_assignment(self):
        d = TenderDetail(
            kode_instansi="dki",
            id_lelang="12345",
            tahap_saat_ini="pengumuman prakualifikasi",
        )
        d.participants = [{"name": "PT A", "npwp": "123"}]
        self.assertEqual(d.participants[0]["name"], "PT A")


# ---------------------------------------------------------------------------
# Idempotent persistence
# ---------------------------------------------------------------------------
class ParticipantPersistenceTests(TestCase):
    def test_first_sync_creates_n_records(self):
        tender = _mk_tender()
        parts = [{"name": "PT A", "npwp": "1"}, {"name": "PT B", "npwp": "2"}, {"name": "PT C", "npwp": "3"}]
        stats = sync_tender_participants(tender, parts, source_url="u")
        self.assertEqual(stats.created, 3)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(TenderParticipant.objects.filter(tender=tender).count(), 3)

    def test_re_sync_is_idempotent(self):
        tender = _mk_tender()
        parts = [{"name": "PT A", "npwp": "1"}, {"name": "PT B", "npwp": "2"}]
        sync_tender_participants(tender, parts)
        sync_tender_participants(tender, parts)
        self.assertEqual(TenderParticipant.objects.filter(tender=tender).count(), 2)

    def test_enrichment_updates_existing(self):
        tender = _mk_tender()
        sync_tender_participants(tender, [{"name": "PT A", "npwp": "111"}])
        stats = sync_tender_participants(
            tender,
            [{"name": "PT A", "npwp": "999"}],
            source_url="new-url",
            source_fetched_at=timezone.now(),
        )
        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(TenderParticipant.objects.filter(tender=tender).count(), 1)
        p = TenderParticipant.objects.get(tender=tender, name="PT A")
        self.assertEqual(p.npwp, "999")
        self.assertEqual(p.source_url, "new-url")

    def test_unavailable_data_does_not_destroy_existing(self):
        tender = _mk_tender()
        sync_tender_participants(tender, [{"name": "PT A", "npwp": "111"}])
        # Re-crawl returns no participant data — existing records must remain.
        stats = sync_tender_participants(tender, [])
        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(TenderParticipant.objects.filter(tender=tender).count(), 1)
        self.assertEqual(
            TenderParticipant.objects.get(tender=tender, name="PT A").npwp,
            "111",
        )

    def test_empty_name_ignored(self):
        tender = _mk_tender()
        stats = sync_tender_participants(
            tender,
            [{"name": "  ", "npwp": "1"}, {"name": "PT A", "npwp": "2"}],
        )
        self.assertEqual(stats.created, 1)
        self.assertEqual(TenderParticipant.objects.filter(tender=tender).count(), 1)

    def test_no_auto_link_to_company(self):
        """No CompanyProfile exists -> participant is UNRESOLVED, never linked."""
        tender = _mk_tender()
        sync_tender_participants(tender, [{"name": "PT Maju Jaya", "npwp": "123"}])
        p = TenderParticipant.objects.get(tender=tender, name="PT Maju Jaya")
        self.assertIsNone(p.company)
        self.assertEqual(p.resolution_status, "UNRESOLVED")
        self.assertEqual(CompanyProfile.objects.count(), 0)
