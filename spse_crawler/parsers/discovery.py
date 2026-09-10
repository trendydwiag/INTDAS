"""Stage 1 — Discovery List Fetcher.

Accesses the DataTables XHR endpoint of each SPSE instansi to extract
the listing of active tender packages.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from spse_crawler.config.settings import InstansiConfig, Settings, get_settings
from spse_crawler.core.browser import SpseHttpClient
from spse_crawler.core.exceptions import CloudflareBlockError, ParsingError
from spse_crawler.models.tender import TenderPackage

# Regex to extract authenticityToken from SPSE Phoenix HTML.
_CSRF_PATTERN = re.compile(r"authenticityToken\s*=\s*['\"]([^'\"]+)['\"]")


class DiscoveryParser:
    """Fetches tender package listings via the SPSE DataTables POST API.

    Workflow per instansi:
      1. GET main page → extract CSRF ``authenticityToken``
      2. POST /dt/lelang?tahun=YYYY → paginated JSON response
      3. Parse each row into a ``TenderPackage``
    """

    # Keywords in the Status column [3] that indicate the package is ABORTED,
    # COMPLETED, or NON-ACTIONABLE (cancelled/failed/finished/pascakualifikasi)
    # and should be skipped at discovery.
    #
    # Completed tenders ("selesai", "pascakualifikasi", "penandatanganan") cannot
    # be submitted to and are considered garbage data unless the user already has
    # a submission on record (that exemption is enforced at purge-time).
    _SKIP_STATUS_KEYWORDS: tuple[str, ...] = (
        "batal",
        "gagal",
        "pembatalan",
        "selesai",
        "tender selesai",
        "pascakualifikasi",
        "penandatanganan",
        "penandatanganan kontrak",
    )

    # DataTables columns for /dt/lelang (16 columns)
    DT_LELANG_COLUMNS: list[dict[str, str]] = [
        {"data": "0", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "1", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "2", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "3", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "4", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "5", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "6", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "7", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "8", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "9", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "10", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "11", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "12", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "13", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "14", "name": "", "searchable": "true", "orderable": "false"},
        {"data": "15", "name": "", "searchable": "true", "orderable": "false"},
    ]

    def __init__(
        self,
        http_client: SpseHttpClient,
        settings: Settings | None = None,
    ) -> None:
        self._http = http_client
        self._settings = settings or get_settings()

    @classmethod
    def _is_eligible_status(cls, status: str) -> bool:
        """Return True if the status string is a package worth carrying to Stage 2.

        Stage 1 only hard-drops cancelled / failed / aborted records. Active
        (prakualifikasi) packages AND awarded / decided / completed packages
        both flow through to Stage 2, where ``is_eligible_tahap`` /
        ``is_retained_tahap`` make the final retention decision.

        If the status is empty or unknown, we include it (let Stage 2 decide).
        """
        if not status:
            return True
        lower = status.lower()
        # Hard skip: cancelled / failed / aborted (never stored, even as history)
        return not any(kw in lower for kw in cls._SKIP_STATUS_KEYWORDS)

    async def _extract_csrf_token(self, instansi: InstansiConfig) -> str:
        """Fetch the main listing page and extract the authenticityToken.

        Tries httpx first, then falls back to Playwright with session warming
        if Cloudflare blocks the request.
        """
        url = instansi.main_page_url(
            section="lelang",
            tahun=self._settings.tahun_anggaran,
        )
        html = ""
        try:
            await self._http.warm_instansi_session(instansi.kode)
            resp = await self._http.get(url)
            html = resp.text
        except (CloudflareBlockError, Exception) as exc:
            logger.debug("[{}] httpx main page failed or Cloudflare blocked: {}", instansi.kode, exc)

        match = _CSRF_PATTERN.search(html)
        if match:
            token = match.group(1)
            logger.debug("[{}] CSRF token extracted via httpx: {}…", instansi.kode, token[:12])
            return token

        # Cloudflare blocked httpx — use Playwright with session warming
        logger.info("[{}] httpx blocked, trying Playwright with warm session…", instansi.kode)
        import asyncio, random
        from spse_crawler.core.browser import PlaywrightEngine
        pw = PlaywrightEngine(self._settings)
        await pw.start()
        try:
            context = await pw.new_context()
            # Warm session on landing page first (acquires Cloudflare cookies)
            base_url = f"https://spse.inaproc.id/{instansi.kode}/"
            page = await context.new_page()
            try:
                await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(2.0, 3.5))
                # Human-like mouse
                try:
                    await page.mouse.move(random.randint(200, 800), random.randint(200, 600), steps=random.randint(5, 15))
                except Exception:
                    pass
            finally:
                await page.close()

            # Now navigate to listing page (session has Cloudflare cookies)
            await asyncio.sleep(random.uniform(1.0, 2.0))
            page2 = await context.new_page()
            try:
                await page2.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page2.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(1.5, 3.0))
                content = await page2.content()
            finally:
                await page2.close()

            match = _CSRF_PATTERN.search(content)
            if match:
                token = match.group(1)
                # Inject Playwright Cloudflare cookies into httpx client
                try:
                    for c in await context.cookies():
                        self._http.client.cookies.set(
                            c["name"],
                            c["value"],
                            domain=c.get("domain") or ".inaproc.id",
                            path=c.get("path", "/"),
                        )
                except Exception as c_err:
                    logger.warning("[{}] Could not sync Playwright cookies to httpx: {}", instansi.kode, c_err)

                logger.info("[{}] CSRF token extracted via Playwright: {}…", instansi.kode, token[:12])
                await context.close()
                return token
            else:
                logger.warning("[{}] Playwright got page but no authenticityToken found", instansi.kode)
            await context.close()
        finally:
            await pw.stop()

        raise ParsingError(
            url=url,
            detail="Could not find authenticityToken in page HTML (httpx and Playwright both failed)",
        )

    def _build_dt_body(
        self,
        token: str,
        draw: int,
        start: int,
        length: int,
    ) -> dict[str, Any]:
        """Build the POST body for the DataTables request."""
        body: dict[str, Any] = {
            "authenticityToken": token,
            "draw": str(draw),
            "start": str(start),
            "length": str(length),
        }
        # Add column definitions
        for i, col in enumerate(self.DT_LELANG_COLUMNS):
            body[f"columns[{i}][data]"] = col["data"]
            body[f"columns[{i}][name]"] = col["name"]
            body[f"columns[{i}][searchable]"] = col["searchable"]
            body[f"columns[{i}][orderable]"] = col["orderable"]
            body[f"columns[{i}][search][value]"] = ""
            body[f"columns[{i}][search][regex]"] = "false"
        body["search[value]"] = ""
        body["search[regex]"] = "false"
        return body

    def _parse_row(
        self,
        row: list[Any],
        kode_instansi: str,
    ) -> TenderPackage | None:
        """Parse a single DataTables row into a TenderPackage.

        Actual SPSE /dt/lelang column mapping (16 cols):
          [0]=ID/Kode, [1]=Nama, [2]=Instansi, [3]=Status,
          [4]=HPS (e.g. '660,9 M'), [5]=Kualifikasi, [6]=Metode,
          [7]=Evaluasi, [8]=Jenis, [9]=Peserta, [10]=Kontrak
        """
        if not row or len(row) < 3:
            return None

        def _clean(val: Any) -> str:
            """Strip HTML tags and whitespace from a cell value."""
            return re.sub(r"<[^>]+>", "", str(val)).strip() if val else ""

        # [0] = ID / Kode lelang
        id_lelang = _clean(row[0])[:50]
        if not id_lelang or id_lelang == "None":
            return None

        # [1] = Nama Paket — capped to 500 chars
        nama_paket = _clean(row[1])[:500] if len(row) > 1 else ""

        # [2] = Instansi — capped to 200 chars
        instansi = _clean(row[2])[:200] if len(row) > 2 else ""

        # [3] = Status — capped to 200 chars
        status = _clean(row[3])[:200] if len(row) > 3 else ""

        # [4] = HPS — may be formatted like "660,9 M", "1,5 M", "Rp. 123.456.000"
        raw_hps = _clean(row[4]) if len(row) > 4 else "0"
        nilai_pagu = self._parse_hps_value(raw_hps)

        # [5] = Kualifikasi — capped to 100 chars
        kualifikasi = _clean(row[5])[:100] if len(row) > 5 else ""

        # [6] = Metode — capped to 200 chars
        metode = _clean(row[6])[:200] if len(row) > 6 else ""

        # [7] = Evaluasi — capped to 100 chars
        evaluasi = _clean(row[7])[:100] if len(row) > 7 else ""

        # [8] = Jenis Pengadaan — capped to 200 chars
        jenis = _clean(row[8])[:200] if len(row) > 8 else ""

        # [9] = Peserta
        raw_peserta = _clean(row[9]) if len(row) > 9 else "0"
        try:
            peserta = int(raw_peserta) if raw_peserta.isdigit() else 0
        except (ValueError, TypeError):
            peserta = 0

        # [10] = Nilai Kontrak — "Rp. 620.772.529.712,03"
        raw_kontrak = _clean(row[10]) if len(row) > 10 else "0"
        nilai_kontrak = self._parse_hps_value(raw_kontrak)

        return TenderPackage(
            kode_instansi=kode_instansi,
            id_lelang=id_lelang,
            nama_paket=nama_paket,
            instansi=instansi,
            status=status,
            nilai_pagu=nilai_pagu,
            kualifikasi=kualifikasi,
            metode_pemilihan=metode,
            evaluasi=evaluasi,
            jenis_pengadaan=jenis,
            peserta=peserta,
            nilai_kontrak=nilai_kontrak,
        )

    @staticmethod
    def _parse_hps_value(raw: str) -> int:
        """Parse HPS value that may be formatted as '660,9 M', '1,5 M', 'Rp. 123.456'."""
        raw = raw.strip()
        if not raw or raw == "None":
            return 0

        # Handle compact format: "660,9 M" or "1,5 Miliar"
        m = re.match(r"([\d.,]+)\s*(M|jt|Miliar|Juta|Rb|rb|Ribu)?", raw, re.IGNORECASE)
        if m:
            num_str = m.group(1).replace(".", "").replace(",", ".")
            suffix = (m.group(2) or "").lower()
            try:
                num = float(num_str)
            except ValueError:
                return 0
            if suffix in ("m", "miliar"):
                return int(num * 1_000_000_000)
            if suffix in ("jt", "juta"):
                return int(num * 1_000_000)
            if suffix in ("rb", "ribu"):
                return int(num * 1_000)
            return int(num)

        # Handle standard format: "Rp. 123.456.789,00"
        cleaned = raw.replace("Rp", "").replace(".", "").replace(",", "").replace(" ", "").strip()
        try:
            return int(cleaned) if cleaned.isdigit() else 0
        except (ValueError, TypeError):
            return 0

    async def _fetch_dt_playwright(
        self,
        instansi: InstansiConfig,
        dt_url: str,
        body: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Fetch DataTables JSON via Playwright stealth session when httpx is Cloudflare-blocked."""
        import json
        from spse_crawler.core.browser import PlaywrightEngine

        pw = PlaywrightEngine(self._settings)
        await pw.start()
        try:
            context = await pw.new_context()
            page = await context.new_page()
            listing_url = instansi.main_page_url(section="lelang", tahun=self._settings.tahun_anggaran)
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            # Sync cookies to httpx for subsequent requests
            try:
                for c in await context.cookies():
                    self._http.client.cookies.set(
                        c["name"],
                        c["value"],
                        domain=c.get("domain") or ".inaproc.id",
                        path=c.get("path", "/"),
                    )
            except Exception:
                pass

            # Execute POST request from within browser page context
            js_script = """
            async (args) => {
                const params = new URLSearchParams();
                for (const [key, val] of Object.entries(args.body)) {
                    params.append(key, val);
                }
                const res = await fetch(args.url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json, text/javascript, */*; q=0.01'
                    },
                    body: params.toString()
                });
                if (!res.ok) return null;
                return await res.json();
            }
            """
            result = await page.evaluate(js_script, {"url": dt_url, "body": body})
            await page.close()
            await context.close()
            return result
        except Exception as exc:
            logger.warning("[{}] Playwright fallback for dt/lelang failed: {}", instansi.kode, exc)
            return None
        finally:
            await pw.stop()

    async def fetch_packages(
        self,
        instansi: InstansiConfig,
        limit: int = 0,
    ) -> list[TenderPackage]:
        """Fetch all tender packages from an instansi via DataTables API.

        Args:
            instansi: Target instansi configuration.
            limit: Maximum packages to fetch (0 = unlimited / all pages).

        Returns:
            List of TenderPackage objects.
        """
        logger.info("[{}] Starting discovery fetch…", instansi.kode)

        # Step 1: Get CSRF token
        token = await self._extract_csrf_token(instansi)

        # Step 2: Paginate through DataTables
        dt_url = instansi.dt_endpoint(
            endpoint="lelang",
            tahun=self._settings.tahun_anggaran,
        )
        all_packages: list[TenderPackage] = []
        draw = 1
        start = 0
        page_size = self._settings.page_size
        skipped_count = 0

        headers = {
            "Referer": instansi.main_page_url(section="lelang", tahun=self._settings.tahun_anggaran),
            "Origin": "https://spse.inaproc.id",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

        while True:
            body = self._build_dt_body(
                token=token,
                draw=draw,
                start=start,
                length=page_size,
            )
            data = None
            try:
                resp = await self._http.post(dt_url, data=body, headers=headers)
                data = resp.json()
            except CloudflareBlockError:
                logger.warning("[{}] Cloudflare blocked POST /dt/lelang via httpx. Trying Playwright fallback...", instansi.kode)
                data = await self._fetch_dt_playwright(instansi, dt_url, body)
                if data is None:
                    logger.warning("[{}] Cloudflare blocked both httpx and Playwright for dt/lelang", instansi.kode)
                    break
            except Exception as exc:
                logger.warning("[{}] Error fetching dt/lelang at offset {}: {}. Trying Playwright...", instansi.kode, start, exc)
                data = await self._fetch_dt_playwright(instansi, dt_url, body)
                if data is None:
                    break

            if not data or not isinstance(data, dict):
                break

            rows: list[list[Any]] = data.get("data", [])
            if not rows:
                logger.debug("[{}] No more data at offset {}", instansi.kode, start)
                break

            for row in rows:
                pkg = self._parse_row(row, instansi.kode)
                if pkg is None:
                    continue
                # Stage 1 eligibility filter
                if not self._is_eligible_status(pkg.status):
                    skipped_count += 1
                    logger.debug(
                        "[{}/{}] [SKIP] Status '{}' — aborted (batal/gagal/pembatalan)",
                        instansi.kode, pkg.id_lelang, pkg.status,
                    )
                    continue
                all_packages.append(pkg)

            logger.info(
                "[{}] Fetched {} rows (offset {}) — {} eligible so far, {} skipped",
                instansi.kode, len(rows), start, len(all_packages), skipped_count,
            )

            # Check if we've reached the limit
            if limit > 0 and len(all_packages) >= limit:
                all_packages = all_packages[:limit]
                break

            # If fewer rows than page_size, we're done
            if len(rows) < page_size:
                break

            start += page_size
            draw += 1

        logger.info(
            "[{}] Discovery complete: {} packages to Stage 2 ({} skipped as aborted)",
            instansi.kode, len(all_packages), skipped_count,
        )
        return all_packages
