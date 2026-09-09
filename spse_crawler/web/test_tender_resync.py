"""Tests for single tender resync endpoint and DetailParser bypass_tahap_gate."""
from unittest.mock import AsyncMock, patch
from django.test import TestCase, Client

from spse_crawler.accounts.models import User
from spse_crawler.companies.models import CompanyProfile
from spse_crawler.web.models import TenderResult
from spse_crawler.models.tender import TenderPackage


class TenderResyncTestCase(TestCase):
    def setUp(self):
        self.company = CompanyProfile.objects.create(name="PT Test Solusi")
        self.user = User.objects.create_user(
            username="resync_tester",
            email="resync@test.com",
            password="password123",
            company=self.company,
        )
        self.client = Client()
        self.tender = TenderResult.objects.create(
            kode_instansi="kemendagri",
            id_lelang="10161819000",
            nama_paket="Pengadaan Perangkat IT",
            instansi="Kementerian Dalam Negeri",
            hps=500_000_000,
            jenis_pengadaan="Pengadaan Barang",
            metode_pengadaan="Tender - Pasca Kualifikasi Satu File",
            tahap_saat_ini="Tender Sudah Selesai",
            is_prakualifikasi=False,
            url_pengumuman="https://spse.inaproc.id/kemendagri/lelang/10161819000/pengumumanlelang",
        )

    def test_resync_unauthenticated_returns_401(self):
        resp = self.client.post(f"/api/tenders/{self.tender.id}/resync/")
        self.assertEqual(resp.status_code, 401)

    def test_resync_get_not_allowed(self):
        self.client.force_login(self.user)
        resp = self.client.get(f"/api/tenders/{self.tender.id}/resync/")
        self.assertEqual(resp.status_code, 405)

    def test_resync_not_found(self):
        self.client.force_login(self.user)
        resp = self.client.post("/api/tenders/9999999/resync/")
        self.assertEqual(resp.status_code, 404)

    @patch("spse_crawler.web.views._execute_single_resync")
    def test_resync_success(self, mock_exec):
        self.client.force_login(self.user)

        self.tender.satuan_kerja_detail = "Pusat Data dan Sistem Informasi"
        self.tender.jadwal_json = [{"no": "1", "tahap": "Pengumuman", "mulai": "01 Jan", "sampai": "05 Jan", "perubahan": ""}]
        self.tender.syarat_kualifikasi = "Memiliki NIB dan SBU"
        self.tender.peserta_count = 12
        self.tender.save()

        async def _mock_coro(tender_id):
            return True, "Data tender berhasil disinkronkan dari LPSE", self.tender

        mock_exec.side_effect = _mock_coro

        resp = self.client.post(f"/api/tenders/{self.tender.id}/resync/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["tender"]["satuan_kerja_detail"], "Pusat Data dan Sistem Informasi")
        self.assertEqual(data["tender"]["peserta_count"], 12)
        self.assertEqual(len(data["tender"]["jadwal_json"]), 1)


class DetailParserBypassTahapGateTestCase(TestCase):
    async def test_bypass_tahap_gate_avoids_skip(self):
        from spse_crawler.parsers.detail import DetailParser
        from spse_crawler.config import get_settings

        mock_http = AsyncMock()
        mock_http.get.return_value.status_code = 200
        html_dummy = """
        <html>
          <body>
            <table>
              <tr><td>Tahap Saat Ini</td><td>Tender Sudah Selesai</td></tr>
              <tr><td>Satuan Kerja</td><td>Biro Umum</td></tr>
            </table>
          </body>
        </html>
        """
        parser = DetailParser(mock_http, settings=get_settings())
        parser._fetch_html_httpx = AsyncMock(return_value=html_dummy)

        pkg = TenderPackage(
            kode_instansi="kemendagri",
            id_lelang="10161819000",
            nama_paket="Paket Uji",
            instansi="Kemendagri",
            hps=1000000,
            jenis_pengadaan="Barang",
            tahap_saat_ini="Tender Sudah Selesai",
        )

        from spse_crawler.core.exceptions import PackageSkippedError
        with self.assertRaises(PackageSkippedError):
            await parser.scrape_detail(pkg, bypass_tahap_gate=False)

        detail = await parser.scrape_detail(pkg, bypass_tahap_gate=True)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.id_lelang, "10161819000")
