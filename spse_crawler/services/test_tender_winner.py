"""Tests for Phase 6B — Winner Source Verification & Winner Intelligence.

Covers the VERIFIED winner source (the SPSE ``/evaluasi/{id}/pemenang`` page,
captured live during Phase 6B reconnaissance):
- Winner parser extraction from real captured HTML structure (identity + the
  three distinct price values: Harga Penawaran / Terkoreksi / Negosiasi).
- Missing / malformed / boundary cases (never fabricated).
- TenderDetail pydantic winner field defaults.
- TenderWinner model fields (npwp, alamat, harga_*).
- Idempotent persistence (create / update / no-data no-op) via
  ``sync_tender_winner``.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from spse_crawler.companies.models import CompanyProfile
from spse_crawler.models.tender import TenderDetail
from spse_crawler.parsers.detail import DetailParser
from spse_crawler.services.tender_winner_store import sync_tender_winner
from spse_crawler.web.models import TenderResult, TenderWinner


def _mk_tender(**kwargs) -> TenderResult:
    defaults = {
        "kode_instansi": "jabarprov",
        "id_lelang": "10158980000",
        "nama_paket": "Pemeliharaan Berkala Jalan Cimerak - Cibuntu",
        "instansi": "DINAS PU DAN TATA RUANG",
        "hps": 856_638_000,
        "jenis_pengadaan": "Pekerjaan Konstruksi",
        "tahap_saat_ini": "pengumuman pemenang",
    }
    defaults.update(kwargs)
    return TenderResult.objects.create(**defaults)


def _winner_html(
    *,
    company: str = "CV. Athalla Putra Kusma",
    alamat: str = "Lingkungan Jelat RT 02 RW 04 - Banjar (Kota) - Jawa Barat",
    npwp: str = "00*5**7****42**0",
    penawaran: str = "Rp. 855.800.811,31",
    terkoreksi: str = "Rp. 855.800.811,31",
    negosiasi: str = "Rp. 855.301.311,32",
) -> str:
    """Build the verified /evaluasi/{id}/pemenang page structure.

    Mirrors the real captured HTML: a meta table (Pagu/HPS) plus a nested
    winner table whose data row appears immediately after the
    ``Nama Pemenang`` header row.
    """
    winner_header = (
        "<tr><th>Nama Pemenang</th><th>Alamat</th><th>NPWP</th>"
        "<th>Harga Penawaran</th><th>Harga Terkoreksi</th><th>Harga Negosiasi</th></tr>"
    )
    winner_row = (
        f"<tr><td>{company}</td><td>{alamat}</td><td>{npwp}</td>"
        f"<td>{penawaran}</td><td>{terkoreksi}</td><td>{negosiasi}</td></tr>"
    )
    nested = f"<table>{winner_header}{winner_row}</table>"
    return (
        "<table>"
        "<tr><th>Nama Tender</th><td>Pemeliharaan Berkala Jalan</td></tr>"
        "<tr><th>Pagu</th><td>Rp. 859.712.000,00</td></tr>"
        "<tr><th>HPS</th><td>Rp. 856.638.000,00</td></tr>"
        f"<tr><td colspan='2'>{nested}</td></tr>"
        "</table>"
    )


def _parser_instance() -> DetailParser:
    """Build a DetailParser without network/instance state (static helpers only)."""
    return object.__new__(DetailParser)


# ---------------------------------------------------------------------------
# Winner parser
# ---------------------------------------------------------------------------
class WinnerParserTests(TestCase):
    def test_parse_full_winner(self):
        html = _winner_html()
        result = _parser_instance()._parse_winner(html)
        self.assertIsNotNone(result)
        self.assertEqual(result["company_name"], "CV. Athalla Putra Kusma")
        self.assertEqual(result["npwp"], "00*5**7****42**0")
        self.assertEqual(result["alamat"], "Lingkungan Jelat RT 02 RW 04 - Banjar (Kota) - Jawa Barat")
        self.assertEqual(result["harga_penawaran"], 855800811)
        self.assertEqual(result["harga_terkoreksi"], 855800811)
        self.assertEqual(result["harga_negosiasi"], 855301311)
        # winning_value = final negotiated price
        self.assertEqual(result["winning_value"], 855301311)

    def test_parse_missing_identity_returns_none(self):
        html = _winner_html(company="", npwp="", alamat="")
        self.assertIsNone(_parser_instance()._parse_winner(html))

    def test_parse_missing_value_keeps_identity(self):
        html = _winner_html(penawaran="", terkoreksi="", negosiasi="")
        result = _parser_instance()._parse_winner(html)
        self.assertIsNotNone(result)
        self.assertEqual(result["company_name"], "CV. Athalla Putra Kusma")
        self.assertIsNone(result["harga_penawaran"])
        self.assertIsNone(result["harga_terkoreksi"])
        self.assertIsNone(result["harga_negosiasi"])
        self.assertIsNone(result["winning_value"])

    def test_parse_missing_table_returns_none(self):
        self.assertIsNone(_parser_instance()._parse_winner("<table><tr><td>x</td></tr></table>"))

    def test_parse_malformed_returns_none(self):
        self.assertIsNone(_parser_instance()._parse_winner("not html at all <<<"))

    def test_parse_empty_string_returns_none(self):
        self.assertIsNone(_parser_instance()._parse_winner(""))

    def test_parse_takes_first_data_row_only(self):
        header = (
            "<tr><th>Nama Pemenang</th><th>Alamat</th><th>NPWP</th>"
            "<th>Harga Penawaran</th><th>Harga Terkoreksi</th><th>Harga Negosiasi</th></tr>"
        )
        row1 = "<tr><td>PT Pertama</td><td>A</td><td>N1</td><td>Rp. 100,00</td><td>Rp. 100,00</td><td>Rp. 90,00</td></tr>"
        row2 = "<tr><td>PT Kedua</td><td>B</td><td>N2</td><td>Rp. 200,00</td><td>Rp. 200,00</td><td>Rp. 190,00</td></tr>"
        html = f"<table>{header}{row1}{row2}</table>"
        result = _parser_instance()._parse_winner(html)
        self.assertEqual(result["company_name"], "PT Pertama")

    def test_money_parser(self):
        parse = DetailParser._parse_money
        self.assertEqual(parse("Rp. 855.800.811,31"), 855800811)
        self.assertEqual(parse("1.234.567"), 1234567)
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("  "))
        self.assertIsNone(parse("Rp. abc"))


# ---------------------------------------------------------------------------
# TenderDetail pydantic
# ---------------------------------------------------------------------------
class WinnerPydanticTests(TestCase):
    def test_winner_default_none(self):
        d = TenderDetail(kode_instansi="jabarprov", id_lelang="10158980000", tahap_saat_ini="pengumuman pemenang")
        self.assertIsNone(d.winner)
        self.assertEqual(d.winner_source_url, "")

    def test_winner_assignment(self):
        d = TenderDetail(kode_instansi="jabarprov", id_lelang="10158980000", tahap_saat_ini="pengumuman pemenang")
        d.winner = {"company_name": "CV. Athalla Putra Kusma", "winning_value": 855301311}
        self.assertEqual(d.winner["company_name"], "CV. Athalla Putra Kusma")


# ---------------------------------------------------------------------------
# TenderWinner model
# ---------------------------------------------------------------------------
class WinnerModelTests(TestCase):
    def test_create_winner_with_verified_fields(self):
        tender = _mk_tender()
        w = TenderWinner.objects.create(
            tender=tender,
            company_name="CV. Athalla Putra Kusma",
            npwp="00*5**7****42**0",
            alamat="Lingkungan Jelat",
            harga_penawaran=855800811,
            harga_terkoreksi=855800811,
            harga_negosiasi=855301311,
            winning_value=855301311,
            source_url="https://spse.inaproc.id/jabarprov/evaluasi/10158980000/pemenang",
            source_type="pemenang_page",
            source_fetched_at=timezone.now(),
        )
        self.assertEqual(w.company_name, "CV. Athalla Putra Kusma")
        self.assertEqual(w.npwp, "00*5**7****42**0")
        self.assertEqual(w.harga_negosiasi, 855301311)
        self.assertEqual(w.source_type, "pemenang_page")
        self.assertIn("evaluasi", w.source_url)

    def test_one_to_one_tender(self):
        tender = _mk_tender()
        TenderWinner.objects.create(tender=tender, company_name="A")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TenderWinner.objects.create(tender=tender, company_name="B")

    def test_price_fields_nullable(self):
        tender = _mk_tender()
        w = TenderWinner.objects.create(tender=tender, company_name="A")
        self.assertIsNone(w.winning_value)
        self.assertIsNone(w.harga_penawaran)
        self.assertIsNone(w.harga_negosiasi)

    def test_cascade_delete_with_tender(self):
        tender = _mk_tender()
        TenderWinner.objects.create(tender=tender, company_name="A")
        tender_id = tender.id
        tender.delete()
        self.assertEqual(TenderWinner.objects.filter(tender_id=tender_id).count(), 0)


# ---------------------------------------------------------------------------
# Idempotent persistence
# ---------------------------------------------------------------------------
class WinnerPersistenceTests(TestCase):
    def _winner_dict(self, **overrides):
        data = {
            "company_name": "CV. Athalla Putra Kusma",
            "npwp": "00*5**7****42**0",
            "alamat": "Lingkungan Jelat",
            "harga_penawaran": 855800811,
            "harga_terkoreksi": 855800811,
            "harga_negosiasi": 855301311,
            "winning_value": 855301311,
        }
        data.update(overrides)
        return data

    def test_sync_creates_winner(self):
        tender = _mk_tender()
        created = sync_tender_winner(tender, self._winner_dict(), source_url="u")
        self.assertTrue(created)
        w = TenderWinner.objects.get(tender=tender)
        self.assertEqual(w.company_name, "CV. Athalla Putra Kusma")
        self.assertEqual(w.source_url, "u")

    def test_sync_is_idempotent(self):
        tender = _mk_tender()
        sync_tender_winner(tender, self._winner_dict())
        sync_tender_winner(tender, self._winner_dict())
        self.assertEqual(TenderWinner.objects.filter(tender=tender).count(), 1)

    def test_sync_updates_existing(self):
        tender = _mk_tender()
        sync_tender_winner(tender, self._winner_dict())
        sync_tender_winner(
            tender,
            self._winner_dict(winning_value=900000000, harga_negosiasi=900000000),
            source_url="new-url",
            source_fetched_at=timezone.now(),
        )
        self.assertEqual(TenderWinner.objects.filter(tender=tender).count(), 1)
        w = TenderWinner.objects.get(tender=tender)
        self.assertEqual(w.winning_value, 900000000)
        self.assertEqual(w.source_url, "new-url")

    def test_no_data_is_no_op(self):
        tender = _mk_tender()
        self.assertFalse(sync_tender_winner(tender, None))
        self.assertFalse(sync_tender_winner(tender, {}))
        self.assertEqual(TenderWinner.objects.filter(tender=tender).count(), 0)

    def test_empty_company_is_no_op(self):
        tender = _mk_tender()
        self.assertFalse(sync_tender_winner(tender, self._winner_dict(company_name="  ")))
        self.assertEqual(TenderWinner.objects.filter(tender=tender).count(), 0)

    def test_unavailable_data_does_not_destroy_existing(self):
        tender = _mk_tender()
        sync_tender_winner(tender, self._winner_dict())
        # Re-crawl returns no winner — existing record must remain.
        self.assertFalse(sync_tender_winner(tender, None))
        self.assertEqual(TenderWinner.objects.filter(tender=tender).count(), 1)
        self.assertEqual(
            TenderWinner.objects.get(tender=tender).company_name,
            "CV. Athalla Putra Kusma",
        )

    def test_no_auto_link_to_company(self):
        tender = _mk_tender()
        sync_tender_winner(tender, self._winner_dict())
        w = TenderWinner.objects.get(tender=tender)
        self.assertIsNone(w.company)
        self.assertEqual(w.resolution_status, "UNRESOLVED")
        self.assertEqual(CompanyProfile.objects.count(), 0)
