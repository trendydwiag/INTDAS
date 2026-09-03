"""Tests for Phase 6A — Historical purge safety (``flush_non_retained``).

Covers the history-safe auto-purge used after each scheduled crawl:
- Awarded / decided / completed / pemenang tenders are NOT deleted.
- Their participants survive (cascade must not fire for retained tenders).
- Active prakualifikasi tenders are NOT deleted.
- Truly aborted / unrecognised-status non-prakualifikasi records ARE deleted.
"""

from __future__ import annotations

from django.test import TestCase

from spse_crawler.services.purger import flush_non_retained
from spse_crawler.services.tender_participant_store import sync_tender_participants
from spse_crawler.web.models import TenderParticipant, TenderResult


_LELANG_SEQ = 0


def _mk_tender(tahap: str, is_prakualifikasi: bool, **kwargs) -> TenderResult:
    global _LELANG_SEQ
    _LELANG_SEQ += 1
    defaults = {
        "kode_instansi": "dki",
        "id_lelang": f"pk-lelang-{_LELANG_SEQ}",
        "nama_paket": "Pengadaan Server",
        "instansi": "Dinas Kominfo",
        "hps": 500_000_000,
        "jenis_pengadaan": "Pengadaan Barang",
        "tahap_saat_ini": tahap,
        "is_prakualifikasi": is_prakualifikasi,
    }
    defaults.update(kwargs)
    return TenderResult.objects.create(**defaults)


class FlushNonRetainedTests(TestCase):
    def test_awarded_tender_is_kept(self):
        for tahap in [
            "pengumuman pemenang",
            "Pengumuman Pemenang",
            "penetapan pemenang",
            "selesai",
            "kontrak",
            "penandatanganan kontrak",
        ]:
            _mk_tender(tahap=tahap, is_prakualifikasi=False)
        result = flush_non_retained()
        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(TenderResult.objects.count(), 6)

    def test_active_prakualifikasi_tender_is_kept(self):
        _mk_tender(tahap="pengumuman prakualifikasi", is_prakualifikasi=True)
        _mk_tender(tahap="evaluasi administrasi", is_prakualifikasi=False)
        result = flush_non_retained()
        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(TenderResult.objects.count(), 2)

    def test_aborted_non_retained_is_deleted(self):
        _mk_tender(tahap="pembatalan", is_prakualifikasi=False)
        _mk_tender(tahap="tender gagal", is_prakualifikasi=False)
        _mk_tender(tahap="tidak diketahui", is_prakualifikasi=False)
        result = flush_non_retained()
        self.assertEqual(result.deleted_count, 3)
        self.assertEqual(TenderResult.objects.count(), 0)

    def test_awarded_and_aborted_mixed(self):
        kept = _mk_tender(tahap="pengumuman pemenang", is_prakualifikasi=False)
        _mk_tender(tahap="tender gagal", is_prakualifikasi=False)
        result = flush_non_retained()
        self.assertEqual(result.deleted_count, 1)
        self.assertEqual(TenderResult.objects.count(), 1)
        self.assertTrue(TenderResult.objects.filter(pk=kept.pk).exists())

    def test_participant_survives_with_awarded_tender(self):
        """Cascade must not fire: participant of an awarded tender survives."""
        tender = _mk_tender(tahap="Pengumuman Pemenang", is_prakualifikasi=False)
        sync_tender_participants(
            tender,
            [{"name": "PT Maju Jaya", "npwp": "012345678901234"}],
        )
        flush_non_retained()
        self.assertTrue(TenderResult.objects.filter(pk=tender.pk).exists())
        self.assertEqual(
            TenderParticipant.objects.filter(tender=tender).count(),
            1,
        )
        self.assertEqual(
            TenderParticipant.objects.get(tender=tender, name="PT Maju Jaya").npwp,
            "012345678901234",
        )

    def test_aborted_participant_is_cascade_deleted(self):
        tender = _mk_tender(tahap="tender gagal", is_prakualifikasi=False)
        sync_tender_participants(tender, [{"name": "PT A", "npwp": "1"}])
        flush_non_retained()
        self.assertFalse(TenderResult.objects.filter(pk=tender.pk).exists())
        self.assertEqual(TenderParticipant.objects.filter(tender=tender).count(), 0)
