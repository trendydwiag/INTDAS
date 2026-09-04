"""Tests for Phase 6A — Discovery-level historical retention.

Covers the Stage 1 discovery eligibility gate:
- Active tenders are discovered.
- Awarded / decided / completed / pemenang tenders are discovered (retained).
- Aborted (cancelled / failed) and invalid records are still filtered.
- Empty / unknown statuses are carried to Stage 2 (let Stage 2 decide).
"""

from __future__ import annotations

from django.test import TestCase

from spse_crawler.parsers.discovery import DiscoveryParser


class DiscoveryEligibilityRetentionTests(TestCase):
    """Stage 1 retention semantics for the discovery status gate."""

    def test_active_tender_is_discovered(self):
        for status in [
            "pengumuman prakualifikasi",
            "pengumuman pascakualifikasi",
            "evaluasi administrasi",
            "pembuktian kualifikasi",
            "pengumuman",
        ]:
            self.assertTrue(
                DiscoveryParser._is_eligible_status(status),
                f"active status should be discovered: {status!r}",
            )

    def test_awarded_tender_is_discovered(self):
        for status in [
            "Pengumuman Pemenang",
            "pengumuman pemenang",
            "penetapan pemenang",
            "rekomendasi pemenang",
        ]:
            self.assertTrue(
                DiscoveryParser._is_eligible_status(status),
                f"awarded status should be discovered: {status!r}",
            )

    def test_decided_completed_tender_is_discovered(self):
        for status in [
            "selesai",
            "tender selesai",
            "kontrak",
            "dikontrak",
            "penandatanganan kontrak",
            "penandatanganan",
        ]:
            self.assertTrue(
                DiscoveryParser._is_eligible_status(status),
                f"decided/completed status should be discovered: {status!r}",
            )

    def test_aborted_tender_is_still_filtered(self):
        for status in [
            "batal",
            "pembatalan",
            "gagal",
            "tender gagal",
            "dibatalkan",
        ]:
            self.assertFalse(
                DiscoveryParser._is_eligible_status(status),
                f"aborted status should be filtered: {status!r}",
            )

    def test_empty_status_carried_to_stage2(self):
        self.assertTrue(DiscoveryParser._is_eligible_status(""))
        self.assertTrue(DiscoveryParser._is_eligible_status(None))

    def test_unknown_status_carried_to_stage2(self):
        # Unknown status is not dropped at discovery; Stage 2 decides retention.
        self.assertTrue(DiscoveryParser._is_eligible_status("tidak diketahui"))

    def test_skip_keywords_exclude_awarded_completed(self):
        """Regression guard: awarded/completed keywords must NOT be in the
        discovery skip set (they must flow through to Stage 2 retention)."""
        for kw in ("selesai", "kontrak", "dikontrak", "penandatanganan"):
            self.assertNotIn(kw, DiscoveryParser._SKIP_STATUS_KEYWORDS, kw)
