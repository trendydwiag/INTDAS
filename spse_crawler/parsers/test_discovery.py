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

    def test_decided_in_progress_tender_is_discovered(self):
        for status in [
            "kontrak",
            "dikontrak",
        ]:
            self.assertTrue(
                DiscoveryParser._is_eligible_status(status),
                f"decided/in-progress status should be discovered: {status!r}",
            )

    def test_completed_tender_is_filtered_at_discovery(self):
        """Completed & non-actionable tenders cannot be submitted and must be filtered at discovery."""
        for status in [
            "selesai",
            "tender selesai",
            "Tender Sudah Selesai",
            "pascakualifikasi",
            "pengumuman pascakualifikasi",
            "penandatanganan",
            "penandatanganan kontrak",
        ]:
            self.assertFalse(
                DiscoveryParser._is_eligible_status(status),
                f"completed/non-actionable status should be filtered at discovery: {status!r}",
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

    def test_skip_keywords_exclude_in_progress_awarded(self):
        """Regression guard: in-progress awarded keywords must NOT be in the
        discovery skip set, but completed/non-actionable keywords MUST be in the skip set."""
        for kw in ("kontrak", "dikontrak"):
            self.assertNotIn(kw, DiscoveryParser._SKIP_STATUS_KEYWORDS, kw)
        for kw in ("selesai", "tender selesai", "pascakualifikasi", "penandatanganan", "penandatanganan kontrak"):
            self.assertIn(kw, DiscoveryParser._SKIP_STATUS_KEYWORDS, kw)
