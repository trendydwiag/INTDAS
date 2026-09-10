"""Stage 2 — Detail Scraper & Prakualifikasi Evaluator.

Visits the pengumuman detail page for each tender and extracts the
"Tahap Tender Saat Ini" status using ``selectolax`` for fast HTML parsing.

Session management:
  Each instansi gets a cached Playwright BrowserContext that persists cookies
  (JSESSIONID, Cloudflare WAF tokens) across requests. The session is warmed
  by visiting the instansi landing page before any detail page requests.

Stealth & Anti-Bot Evasion:
  - Human-like random delays (jitter) between navigations
  - Cursor movement and scroll simulation on Cloudflare challenge pages
  - Smart retry with session invalidation on access denied / Cloudflare
  - Pre-navigation landing page visit for cookie acquisition
"""

from __future__ import annotations

import asyncio
import random
import re
import time

from loguru import logger
from selectolax.parser import HTMLParser

from spse_crawler.config.settings import InstansiConfig, Settings, get_settings
from spse_crawler.core.browser import PlaywrightEngine, SpseHttpClient
from spse_crawler.core.exceptions import (
    CloudflareBlockError,
    CloudflareChallengeError,
    PackageSkippedError,
    ParsingError,
)
from spse_crawler.models.tender import TenderDetail, TenderPackage


class DetailParser:
    """Extracts detail information from the pengumuman page.

    Uses ``httpx`` as the primary method and falls back to Playwright if
    the response appears to be a Cloudflare challenge page.

    Playwright sessions are cached per instansi to maintain cookies and
    session state across requests.
    """

    # 5-digit KBLI codes we care about
    KBLI_TARGETS: dict[str, str] = {
        "62019": "Aktivitas Pemrograman Komputer Lainnya",
        "62090": "Aktivitas Teknologi Informasi Dan Jasa Komputer Lainnya",
        "62029": "Aktivitas Konsultasi Komputer dan Manajemen Fasilitas Komputer Lainnya",
    }

    # Regex: match any 5-digit KBLI code in HTML (with or without dot separators like 62.01.9)
    _KBLI_PATTERN: re.Pattern[str] = re.compile(
        r"(?:KBLI|K\.?B\.?L\.?I\.?|SBU|sub\s*bidang)"
        r"[^0-9]{0,30}?"
        r"(\d{2}[\.\s]?\d{2}[\.\s]?\d{1,2})",
        re.IGNORECASE,
    )

    # Broader fallback: find 5-digit codes in the IT-related 62xxx range near KBLI keywords
    _KBLI_BROAD_PATTERN: re.Pattern[str] = re.compile(
        r"(62\d{3})",
        re.IGNORECASE,
    )

    # Patterns to match "Tahap Tender Saat Ini" in HTML
    _TAHAP_PATTERNS: list[re.Pattern[str]] = [
        re.compile(
            r"Tahap\s+(?:Tender\s+)?Saat\s+Ini\s*[:\"]?\s*"
            r"<[^>]*>\s*([^<]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"Tahap\s+Saat\s+Ini\s*[:\"]?\s*"
            r"<[^>]*>\s*([^<]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"data-tahap[^>]*>\s*([^<]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"class=[\"']tahap[^\"']*[\"'][^>]*>\s*([^<]+)",
            re.IGNORECASE,
        ),
    ]

    # Session cache TTL — re-warm sessions after this many seconds
    _SESSION_TTL: float = 300.0  # 5 minutes

    # Max retries for Playwright fetch on Cloudflare / access denied
    _PW_MAX_RETRIES: int = 3

    def __init__(
        self,
        http_client: SpseHttpClient,
        playwright_engine: PlaywrightEngine | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._http = http_client
        self._pw = playwright_engine
        self._pw_owned = False  # True if we created our own PlaywrightEngine
        self._settings = settings or get_settings()
        # Per-instansi Playwright context cache: kode -> (context, warmed_at)
        self._pw_sessions: dict[str, tuple[object, float]] = {}

    async def _ensure_playwright(self) -> PlaywrightEngine | None:
        """Lazily create and start a PlaywrightEngine if none was provided."""
        if self._pw is not None:
            return self._pw
        try:
            self._pw = PlaywrightEngine(self._settings)
            await self._pw.start()
            self._pw_owned = True
            logger.debug("[pw] Lazily started PlaywrightEngine for DetailParser")
            return self._pw
        except Exception as exc:
            logger.warning("[pw] Failed to start PlaywrightEngine: {}", exc)
            self._pw = None
            return None

    async def close(self) -> None:
        """Clean up cached Playwright sessions and stop owned engine."""
        for kode, (ctx, _) in list(self._pw_sessions.items()):
            try:
                await ctx.close()
            except Exception:
                pass
        self._pw_sessions.clear()
        if self._pw_owned and self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._pw_owned = False

    # ------------------------------------------------------------------
    # Human-like delay helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _human_delay(low: float = 1.5, high: float = 3.5) -> None:
        """Sleep for a random duration between *low* and *high* seconds."""
        delay = random.uniform(low, high)
        await asyncio.sleep(delay)

    @staticmethod
    async def _simulate_human_behavior(page) -> None:
        """Perform random mouse movement and small scroll to mimic a real user."""
        try:
            # Random cursor move to a plausible position
            x = random.randint(150, 900)
            y = random.randint(100, 600)
            await page.mouse.move(x, y, steps=random.randint(8, 25))
            await asyncio.sleep(random.uniform(0.2, 0.6))

            # Small scroll down or up
            delta = random.randint(-80, 200)
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(random.uniform(0.3, 0.7))
        except Exception:
            pass  # Best-effort; never block on simulation

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_or_create_pw_session(self, kode_instansi: str):
        """Get a cached Playwright session for an instansi, or create + warm a new one."""
        if self._pw is None:
            return None

        now = time.monotonic()

        # Check cache — reuse if still warm
        if kode_instansi in self._pw_sessions:
            ctx, warmed_at = self._pw_sessions[kode_instansi]
            if now - warmed_at < self._SESSION_TTL:
                logger.debug("[pw] Reusing cached session for {}", kode_instansi)
                return ctx
            else:
                # Session expired — close old context, create new
                logger.debug("[pw] Session expired for {}, recreating", kode_instansi)
                try:
                    await ctx.close()
                except Exception:
                    pass
                del self._pw_sessions[kode_instansi]

        # Create new context
        context = await self._pw.new_context()
        landing_url = f"https://spse.inaproc.id/{kode_instansi}/"

        # Warm session: visit landing page to acquire cookies
        html = await self._pw.warm_session(context, landing_url)
        if html and not PlaywrightEngine.is_access_denied(html):
            logger.info("[pw] Session warmed for {} — cookies acquired", kode_instansi)
            self._pw_sessions[kode_instansi] = (context, now)
            return context
        else:
            # Landing page itself was access denied or failed — still cache the context
            # (cookies may still be useful for subsequent requests)
            logger.warning("[pw] Session warm returned access-denied for {}, caching anyway", kode_instansi)
            self._pw_sessions[kode_instansi] = (context, now)
            return context

    async def _invalidate_pw_session(self, kode_instansi: str) -> None:
        """Close and remove a cached session for an instansi."""
        if kode_instansi in self._pw_sessions:
            ctx, _ = self._pw_sessions.pop(kode_instansi)
            try:
                await ctx.close()
            except Exception:
                pass
            logger.debug("[pw] Invalidated session for {}", kode_instansi)

    # ------------------------------------------------------------------
    # KBLI Extraction
    # ------------------------------------------------------------------

    def _normalize_kblI_code(self, raw: str) -> str:
        """Strip dots/spaces from a KBLI code like '62.01.9' → '62019'."""
        return re.sub(r"[\.\s]", "", raw)

    def _extract_kbli_from_html(self, html: str) -> tuple[str, str]:
        """Return (kbli_code, kbli_description) from the detail page HTML.

        Strategy:
        1. Try structured pattern: KBLI/SBU keyword followed by a 5-digit code.
        2. Fall back to scanning for any 5-digit code inside KBLI-related context.
        3. If no target KBLI found, return ("", "").
        """
        # --- Pass 1: structured keyword + code ---
        for match in self._KBLI_PATTERN.finditer(html):
            raw_code = match.group(1)
            code = self._normalize_kblI_code(raw_code)
            if code in self.KBLI_TARGETS:
                return code, self.KBLI_TARGETS[code]

        # --- Pass 2: broader context search (500 chars around KBLI mentions) ---
        for ctx_match in re.finditer(
            r"(?:KBLI|SBU|sub\s*bidang)", html, re.IGNORECASE
        ):
            start = max(0, ctx_match.start() - 100)
            end = min(len(html), ctx_match.end() + 500)
            context = html[start:end]
            for code_match in self._KBLI_BROAD_PATTERN.finditer(context):
                code = code_match.group(1)
                if code in self.KBLI_TARGETS:
                    return code, self.KBLI_TARGETS[code]

        # --- Pass 3: scan the whole page for known 5-digit codes ---
        for code, desc in self.KBLI_TARGETS.items():
            if code in html:
                return code, desc

        return "", ""

    # ------------------------------------------------------------------
    # Tahap Eligibility Filter
    # ------------------------------------------------------------------

    # Keywords that mark a package as ABORTED (never stored — cancelled/failed/completed/non-actionable).
    _TAHAP_ABORTED_KEYWORDS: tuple[str, ...] = (
        "pembatalan",
        "batal",
        "gagal",
        "selesai",
        "tender selesai",
        "pascakualifikasi",
        "penandatanganan",
        "penandatanganan kontrak",
    )

    # Keywords that mark a package as AWARDED / IN-PROGRESS (retained, not
    # prakualifikasi). Phase 5: these are retained so participant + future
    # winner data can be captured, instead of being dropped by the crib gate.
    # Note: "selesai", "pascakualifikasi", and "penandatanganan" are moved to
    # ABORTED — non-actionable tenders that should not be stored unless
    # the user has an active submission.
    _TAHAP_AWARDED_KEYWORDS: tuple[str, ...] = (
        "kontrak",
        "penetapan pemenang",
        "pengumuman pemenang",
        "rekomendasi",
    )

    # Keywords that mark a package as ELIGIBLE (active prakualifikasi & pemilihan).
    _TAHAP_ACCEPT_KEYWORDS: tuple[str, ...] = (
        "prakualifikasi",
        "pengumuman prakualifikasi",
        "pembuktian kualifikasi",
        "kirim persyaratan",
        "evaluasi",
        "penawaran",
        "kualifikasi",
        "clarification",
        "jawaban",
        "negosiasi",
        "survey",
        "dokumen pemilihan",
        "pemilihan",
        "download dokumen",
        "pemberian penjelasan",
        "upload dokumen",
        "sanggah",
    )

    @classmethod
    def is_eligible_tahap(cls, tahap: str) -> bool:
        """Return True if the tahap string indicates the package is actively in
        a prakualifikasi / qualification stage.

        Returns False for packages that are finished, contracted, cancelled,
        or have not yet entered the qualification process.
        """
        if not tahap:
            return False
        lower = tahap.lower().strip()
        # Hard reject first
        if any(kw in lower for kw in cls._TAHAP_ABORTED_KEYWORDS):
            return False
        if any(kw in lower for kw in cls._TAHAP_AWARDED_KEYWORDS):
            return False
        # Must match an accepted tahap keyword
        return any(kw in lower for kw in cls._TAHAP_ACCEPT_KEYWORDS)

    @classmethod
    def is_retained_tahap(cls, tahap: str) -> bool:
        """Return True if the tahap indicates an AWARDED / COMPLETED package
        that should be retained for participant + winner capture.

        Truly aborted (cancelled/failed) and unknown tahap are NOT retained.
        """
        if not tahap:
            return False
        lower = tahap.lower().strip()
        if any(kw in lower for kw in cls._TAHAP_ABORTED_KEYWORDS):
            return False
        return any(kw in lower for kw in cls._TAHAP_AWARDED_KEYWORDS)

    def _extract_tahap_from_html(self, html: str) -> str | None:
        """Extract the current tender stage from raw HTML using regex."""
        for pattern in self._TAHAP_PATTERNS:
            match = pattern.search(html)
            if match:
                tahap = match.group(1).strip()
                # Clean HTML entities and extra whitespace
                tahap = re.sub(r"<[^>]+>", "", tahap)
                tahap = tahap.replace("&nbsp;", " ").strip()
                if tahap:
                    return tahap.lower()
        return None

    def _extract_tahap_selectolax(self, html: str) -> str | None:
        """Fallback: use selectolax to find tahap info by text content."""
        tree = HTMLParser(html)

        # Look for text nodes containing "Tahap" keywords
        tahap_keywords = [
            "tahap tender saat ini",
            "tahap saat ini",
            "tahapan saat ini",
        ]
        for node in tree.css("td, span, div, p, strong, b, label"):
            text = (node.text() or "").strip().lower()
            for kw in tahap_keywords:
                if kw in text:
                    # The tahap value is likely in the next sibling or parent's text
                    parent = node.parent
                    if parent:
                        full_text = (parent.text() or "").strip()
                        # Extract the part after the keyword
                        idx = full_text.lower().find(kw)
                        if idx >= 0:
                            after = full_text[idx + len(kw):].strip()
                            after = re.sub(r"^[:\s]+", "", after)
                            if after:
                                return after.lower().split("\n")[0].strip()

        # Also check for table rows with "Tahap" in first cell
        for tr in tree.css("tr"):
            cells = tr.css("td")
            if len(cells) >= 2:
                label = (cells[0].text() or "").strip().lower()
                if "tahap" in label:
                    value = (cells[1].text() or "").strip()
                    if value:
                        return value.lower()

        return None

    def _extract_requirement_text(self, html: str) -> str:
        """Extract qualification requirement text from the pengumuman page HTML."""
        tree = HTMLParser(html)
        sections = []

        # Look for requirement/qualification sections by heading keywords
        for node in tree.css("h2, h3, h4, strong, b, td, th"):
            text = (node.text() or "").strip().lower()
            if any(kw in text for kw in ["persyaratan", "kualifikasi", "syarat", "ketentuan"]):
                parent = node.parent
                if parent:
                    section_text = (parent.text() or "").strip()
                    if len(section_text) > 20:
                        sections.append(section_text)

        if sections:
            return "\n\n".join(sections[:5])[:4000]

        # Fallback: extract all table content (SPSE uses tables for requirements)
        table_texts = []
        for tr in tree.css("tr"):
            cells = tr.css("td")
            row_text = " | ".join((c.text() or "").strip() for c in cells if c.text())
            if row_text and len(row_text) > 10:
                table_texts.append(row_text)

        if table_texts:
            return "\n".join(table_texts[:30])[:4000]

        # Last resort: get all visible text
        body = tree.css_first("body")
        if body:
            return (body.text() or "")[:3000]
        return ""

    # ------------------------------------------------------------------
    # Detail enrichment extractors
    # ------------------------------------------------------------------

    def _extract_table_value(self, html: str, label_keywords: tuple[str, ...]) -> str:
        """Extract a value from a table row where the first cell matches label keywords."""
        tree = HTMLParser(html)
        for tr in tree.css("tr"):
            cells = tr.css("th, td")
            if len(cells) >= 2:
                label = (cells[0].text() or "").strip().lower()
                if any(kw in label for kw in label_keywords):
                    value = " ".join((c.text() or "").strip() for c in cells[1:])
                    value = re.sub(r"\s+", " ", value).strip()
                    if value:
                        return value
        return ""

    def _extract_lokasi_pekerjaan(self, html: str) -> str:
        """Extract work location from the detail page."""
        val = self._extract_table_value(html, ("lokasi pekerjaan", "lokasi"))
        return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", val)).strip()

    def _extract_metode_pengadaan(self, html: str) -> str:
        """Extract procurement method from the detail page."""
        return self._extract_table_value(html, ("metode pengadaan", "metode pemilihan"))

    def _extract_tahun_anggaran(self, html: str) -> str:
        """Extract budget year from the detail page."""
        val = self._extract_table_value(html, ("tahun anggaran",))
        # Clean: "LAINNYA 2026" → "2026"
        m = re.search(r"\d{4}", val)
        return m.group(0) if m else val

    def _extract_satuan_kerja(self, html: str) -> str:
        """Extract satuan kerja from the detail page."""
        return self._extract_table_value(html, ("satuan kerja",))

    async def _fetch_jadwal(self, url: str, referer: str, kode_instansi: str) -> list[dict]:
        """Fetch the jadwal tahapan page and extract the timeline table."""
        jadwal_url = url.replace("/pengumumanlelang", "/jadwal")
        html = await self._fetch_html_httpx(jadwal_url, referer=referer, kode_instansi=kode_instansi)
        if html is None:
            # Try Playwright
            await self._ensure_playwright()
            if self._pw is not None:
                html = await self._fetch_html_playwright(jadwal_url, kode_instansi)
        if html is None:
            return []
        return self._parse_jadwal_table(html)

    def _parse_jadwal_table(self, html: str) -> list[dict]:
        """Parse the jadwal tahapan table into a list of dicts."""
        tree = HTMLParser(html)
        jadwal: list[dict] = []
        for i, tr in enumerate(tree.css("tr")):
            cells = tr.css("td")
            if len(cells) >= 4:
                no = (cells[0].text() or "").strip()
                tahap = (cells[1].text() or "").strip()
                mulai = (cells[2].text() or "").strip()
                sampai = (cells[3].text() or "").strip()
                perubahan = (cells[4].text() or "").strip() if len(cells) > 4 else ""
                if tahap and mulai:
                    jadwal.append({
                        "no": no,
                        "tahap": tahap,
                        "mulai": mulai,
                        "sampai": sampai,
                        "perubahan": perubahan,
                    })
        return jadwal

    async def _fetch_peserta_count(self, url: str, referer: str, kode_instansi: str) -> int:
        """Fetch the peserta page and count participants."""
        count, _ = await self._fetch_peserta_data(url, referer, kode_instansi)
        return count

    @staticmethod
    def _build_evaluasi_hasil_url(url: str) -> str:
        """Build the verified /evaluasi/{id}/hasil URL."""
        if not url:
            return ""
        m = re.match(r"(https?://[^/]+/[^/]+)/lelang/([^/]+)/pengumumanlelang", url)
        if not m:
            return ""
        return f"{m.group(1)}/evaluasi/{m.group(2)}/hasil"

    async def _fetch_peserta_data(
        self, url: str, referer: str, kode_instansi: str
    ) -> tuple[int, list[dict]]:
        """Fetch both the /peserta page and /evaluasi/{id}/hasil page once and return (count, participants).

        ``participants`` is a list of ``{"name": str, "npwp": str, "alasan": str, "nilai": str}`` dicts.
        Captures evaluation failure notes (e.g. 'Tidak lulus hasil evaluasi unsur pengalaman perusahaan')
        from the evaluation results tab.
        """
        peserta_url = url.replace("/pengumumanlelang", "/peserta")
        hasil_url = self._build_evaluasi_hasil_url(url)

        participants_dict: dict[str, dict] = {}
        count = 0

        # 1. Fetch /peserta
        html = await self._fetch_html_httpx(peserta_url, referer=referer, kode_instansi=kode_instansi)
        if html is None:
            await self._ensure_playwright()
            if self._pw is not None:
                html = await self._fetch_html_playwright(peserta_url, kode_instansi)
        if html:
            count = self._parse_peserta_count(html)
            for p in self._parse_participants(html):
                key = re.sub(r"[^\w\s]", "", p["name"].lower()).strip()
                participants_dict[key] = p

        # 2. Fetch /evaluasi/{id}/hasil (for evaluation reasons, scores, etc.)
        if hasil_url:
            html_hasil = await self._fetch_html_httpx(hasil_url, referer=referer, kode_instansi=kode_instansi)
            if html_hasil is None:
                await self._ensure_playwright()
                if self._pw is not None:
                    html_hasil = await self._fetch_html_playwright(hasil_url, kode_instansi)
            if html_hasil:
                for p in self._parse_participants_from_evaluasi(html_hasil):
                    key = re.sub(r"[^\w\s]", "", p["name"].lower()).strip()
                    if key in participants_dict:
                        if p.get("alasan"):
                            participants_dict[key]["alasan"] = p["alasan"]
                        if p.get("nilai"):
                            participants_dict[key]["nilai"] = p["nilai"]
                        if p.get("npwp") and not participants_dict[key].get("npwp"):
                            participants_dict[key]["npwp"] = p["npwp"]
                    else:
                        participants_dict[key] = p
                if count == 0:
                    count = len(participants_dict)

        return max(count, len(participants_dict)), list(participants_dict.values())

    def _parse_peserta_count(self, html: str) -> int:
        """Count participants from the peserta page table."""
        tree = HTMLParser(html)
        count = 0
        for tr in tree.css("tr"):
            cells = tr.css("td")
            # Rows with 3+ cells (No, Nama, NPWP, ...) are participant rows
            if len(cells) >= 3:
                first = (cells[0].text() or "").strip()
                if first.isdigit():
                    count += 1
        return count

    def _parse_participants(self, html: str) -> list[dict]:
        """Extract participant name, NPWP, and evaluation note from /peserta table.

        Row layout is ``[No, Nama, NPWP, ...]``. Extra columns (penawaran,
        nilai evaluasi, alasan gugur/tidak lulus) are captured when present.
        """
        tree = HTMLParser(html)
        participants: list[dict] = []
        for tr in tree.css("tr"):
            cells = tr.css("td")
            # Rows with 3+ cells (No, Nama, NPWP, ...) are participant rows
            if len(cells) < 3:
                continue
            first = (cells[0].text() or "").strip()
            if not first.isdigit():
                continue
            name = (cells[1].text() or "").strip()
            npwp = (cells[2].text() or "").strip()
            if not name:
                continue

            alasan = ""
            nilai = ""
            for i in range(3, len(cells)):
                txt = (cells[i].text() or "").strip()
                if not txt:
                    continue
                txt_lower = txt.lower()
                if any(
                    kw in txt_lower
                    for kw in [
                        "tidak lulus",
                        "gugur",
                        "ambang batas",
                        "memenuhi",
                        "tidak memenuhi",
                        "diskualifikasi",
                        "alasan",
                        "keterangan",
                        "tidak menghadiri",
                    ]
                ) or len(txt) > 25:
                    alasan = txt if not alasan else f"{alasan}; {txt}"
                elif re.search(r"^\d+([.,]\d+)?$", txt) and not nilai:
                    nilai = txt

            p_data: dict[str, str] = {"name": name, "npwp": npwp}
            if alasan:
                p_data["alasan"] = alasan
            if nilai:
                p_data["nilai"] = nilai
            participants.append(p_data)
        return participants

    def _parse_participants_from_evaluasi(self, html: str) -> list[dict]:
        """Extract participant name, NPWP, evaluation score and reasons from /evaluasi/{id}/hasil."""
        tree = HTMLParser(html)
        participants: list[dict] = []
        for tr in tree.css("tr"):
            cells = tr.css("td, th")
            if len(cells) < 3:
                continue
            first = (cells[0].text() or "").strip()
            if not first.isdigit():
                continue
            name = (cells[1].text() or "").strip()
            if not name or name.lower() in ["nama peserta", "nama"]:
                continue

            npwp = ""
            alasan = ""
            nilai = ""

            # Check if any cell has fa-close / fa-times (indicating qualification failure)
            has_failed_icon = False
            for c in cells:
                for icon in c.css("i"):
                    cls_attr = (icon.attributes.get("class") or "").lower()
                    if "fa-close" in cls_attr or "fa-times" in cls_attr:
                        has_failed_icon = True

            for i in range(2, len(cells)):
                txt = (cells[i].text() or "").strip()
                if not txt:
                    continue
                # NPWP pattern (masked or unmasked, e.g. 08*3**5****21**0)
                if not npwp and (re.search(r"^\d{2}[*.\d\-]{6,}", txt) or (len(txt) >= 10 and "*" in txt and any(c.isdigit() for c in txt))):
                    npwp = txt
                    continue
                # Evaluation notes / reasons
                txt_lower = txt.lower()
                if any(
                    kw in txt_lower
                    for kw in [
                        "tidak lulus",
                        "gugur",
                        "ambang batas",
                        "memenuhi",
                        "tidak memenuhi",
                        "diskualifikasi",
                        "alasan",
                        "keterangan",
                        "tidak menghadiri",
                        "evaluasi",
                    ]
                ) or len(txt) > 20:
                    alasan = txt if not alasan else f"{alasan}; {txt}"
                elif re.search(r"^\d+([.,]\d+)?$", txt) and not nilai:
                    nilai = txt

            if not alasan and has_failed_icon:
                alasan = "Tidak lulus evaluasi kualifikasi SPSE"

            p_data: dict[str, str] = {"name": name, "npwp": npwp}
            if alasan:
                p_data["alasan"] = alasan
            if nilai:
                p_data["nilai"] = nilai
            participants.append(p_data)
        return participants

    # ------------------------------------------------------------------
    # Winner parsing (source: /evaluasi/{id_lelang}/pemenang, Phase 6B)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_winner_url(url: str) -> str:
        """Build the verified ``/evaluasi/{id}/pemenang`` winner URL.

        The pengumuman detail URL is ``https://spse.inaproc.id/{kode}/lelang/{id}
        /pengumumanlelang``; the winner page (verified during Phase 6B) lives at
        ``https://spse.inaproc.id/{kode}/evaluasi/{id}/pemenang``. This parses
        ``kode``+``id`` from the pengumuman URL and rewrites the segment from
        ``/lelang/.../pengumumanlelang`` to ``/evaluasi/{id}/pemenang`` — it does
        NOT naively swap the trailing "pengumumanlelang" token, which would
        produce the wrong ``/lelang/{id}/pemenang`` path.
        """
        if not url:
            return ""
        m = re.match(r"(https?://[^/]+/[^/]+)/lelang/([^/]+)/pengumumanlelang", url)
        if not m:
            return ""
        return f"{m.group(1)}/evaluasi/{m.group(2)}/pemenang"

    @staticmethod
    def _parse_money(raw: str) -> int | None:
        """Parse an IDR amount like 'Rp. 855.800.811,31' into integer rupiah.

        Returns None when the value is empty/unparseable. Matches the semantics
        of the discovery HPS parser: '.' is a thousands separator and ',' a
        decimal sen fraction, truncated to an integer rupiah amount.
        """
        if not raw:
            return None
        raw = re.sub(r"(?i)^rp\.?\s*", "", raw).strip()
        if not raw:
            return None
        m = re.match(r"([\d.,]+)", raw)
        if not m:
            return None
        num_str = m.group(1).replace(".", "").replace(",", ".")
        try:
            return int(float(num_str))
        except (ValueError, TypeError):
            return None

    @classmethod
    def _parse_winner(cls, html: str) -> dict | None:
        """Parse the winner record from the /evaluasi/{id}/pemenang page.

        The page contains a meta table plus a nested winner table with header:
            Nama Pemenang | Alamat | NPWP | Harga Penawaran | Harga Terkoreksi
            | Harga Negosiasi
        and a single data row. We locate the ``<tr>`` whose child ``<th>`` is
        'Nama Pemenang' and read the following ``<tr>`` as the data row.

        Returns a dict or None (never fabricates; no participant/ordering
        inference). ``winning_value`` is the final negotiated price.
        """
        tree = HTMLParser(html) if html else None
        if tree is None:
            return None
        trs = tree.css("tr")
        for i, tr in enumerate(trs):
            # Only DIRECT child <th> elements, so nested tables never leak into
            # the match (selectolax iter() yields direct child elements only).
            heads = []
            for child in tr.iter():
                if child.tag == "th":
                    heads.append((child.text() or "").strip().lower())
            if not any(h == "nama pemenang" for h in heads):
                continue
            if i + 1 >= len(trs):
                return None
            data_tr = trs[i + 1]
            cells = []
            for child in data_tr.iter():
                if child.tag == "td":
                    cells.append((child.text() or "").strip())
            if len(cells) < 6 or not cells[0]:
                return None
            company = cells[0]
            penawaran = cls._parse_money(cells[3])
            terkoreksi = cls._parse_money(cells[4])
            negosiasi = cls._parse_money(cells[5])
            return {
                "company_name": company,
                "npwp": cells[2] if len(cells) > 2 else "",
                "alamat": cells[1] if len(cells) > 1 else "",
                "harga_penawaran": penawaran,
                "harga_terkoreksi": terkoreksi,
                "harga_negosiasi": negosiasi,
                "winning_value": negosiasi or terkoreksi or penawaran,
            }
        return None

    async def _fetch_winner(
        self,
        url: str,
        referer: str,
        kode_instansi: str,
    ) -> dict | None:
        """Fetch and parse the winner record from /evaluasi/{id}/pemenang.

        Returns None when the page cannot be fetched or contains no winner
        record (never fabricates). Uses the same httpx→Playwright fallback
        pattern as the other linked pages.
        """
        winner_url = self._build_winner_url(url)
        if not winner_url:
            return None
        html = await self._fetch_html_httpx(winner_url, referer=referer, kode_instansi=kode_instansi)
        if html is None:
            await self._ensure_playwright()
            if self._pw is not None:
                html = await self._fetch_html_playwright(winner_url, kode_instansi)
        if html is None:
            logger.debug("WINNER_FETCH_UNAVAILABLE url={}", winner_url)
            return None
        winner = self._parse_winner(html)
        if winner is None:
            logger.debug("WINNER_PARSE_EMPTY url={}", winner_url)
        return winner


    # ------------------------------------------------------------------
    # HTTP fetching
    # ------------------------------------------------------------------

    async def _fetch_html_httpx(self, url: str, referer: str = "", kode_instansi: str = "") -> str | None:
        """Fetch the detail page HTML via httpx with Referer header.

        First warms the instansi session (acquire SPSE_SESSION cookies) if needed.
        """
        # Warm cookies for this instansi before fetching detail pages
        if kode_instansi:
            await self._http.warm_instansi_session(kode_instansi)

        headers = {"Referer": referer} if referer else None
        try:
            resp = await self._http.get(url, headers=headers)
            html = resp.text

            # Check if we got a Cloudflare challenge page
            if PlaywrightEngine.is_cloudflare_challenge(html):
                logger.debug("[httpx] Cloudflare challenge detected for {}", url)
                return None

            # Check for access denied
            if PlaywrightEngine.is_access_denied(html):
                logger.debug("[httpx] Access denied for {}", url)
                return None

            return html
        except CloudflareBlockError:
            logger.debug("[httpx] Cloudflare block for {}", url)
            return None
        except Exception as exc:
            logger.warning("[httpx] Error fetching {}: {}", url, exc)
            return None

    async def _fetch_html_playwright(
        self,
        url: str,
        kode_instansi: str,
        retry_count: int = 0,
    ) -> str | None:
        """Fetch detail page HTML via Playwright with stealth + human simulation.

        Smart retry mechanism:
        1. Get or create a cached Playwright session for this instansi.
        2. Pre-navigate to instansi landing page (if first visit) for cookie transfer.
        3. Set Referer + extra headers for realism.
        4. Navigate to the detail URL with human-like delay.
        5. If access denied OR Cloudflare challenge → invalidate session, retry
           with fresh session + backoff delay (max 3 attempts).
        6. On each retry, re-warm the session from scratch.
        """
        if self._pw is None:
            return None

        context = await self._get_or_create_pw_session(kode_instansi)
        if context is None:
            return None

        instansi_url = f"https://spse.inaproc.id/{kode_instansi}/"
        page = None
        try:
            page = await context.new_page()

            # Inject Referer + realistic headers
            await page.set_extra_http_headers({
                "Referer": instansi_url,
                "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            })

            # Pre-navigation human delay
            await self._human_delay(1.5, 3.0)

            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

            # Wait for JS to settle (Cloudflare challenge resolution window)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

            # Extra settle time
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # Simulate human behavior on the page
            await self._simulate_human_behavior(page)

            html = await page.content()

            # --- Gate: Access Denied ---
            if PlaywrightEngine.is_access_denied(html):
                if retry_count < self._PW_MAX_RETRIES:
                    backoff = 3.0 + retry_count * 2.0  # 3s, 5s, 7s
                    logger.warning(
                        "[pw] Access denied for {} — retry {}/{} in {:.0f}s (fresh session)",
                        url, retry_count + 1, self._PW_MAX_RETRIES, backoff,
                    )
                    await page.close()
                    page = None
                    await self._invalidate_pw_session(kode_instansi)
                    await asyncio.sleep(backoff)
                    return await self._fetch_html_playwright(url, kode_instansi, retry_count + 1)
                else:
                    logger.warning("[pw] Access denied on all retries for {}", url)
                    return None

            # --- Gate: Cloudflare Challenge ---
            if PlaywrightEngine.is_cloudflare_challenge(html):
                if retry_count < self._PW_MAX_RETRIES:
                    backoff = 4.0 + retry_count * 2.0  # 4s, 6s, 8s
                    logger.warning(
                        "[pw] Cloudflare challenge for {} — retry {}/{} in {:.0f}s (fresh session)",
                        url, retry_count + 1, self._PW_MAX_RETRIES, backoff,
                    )
                    await page.close()
                    page = None
                    await self._invalidate_pw_session(kode_instansi)
                    await asyncio.sleep(backoff)
                    return await self._fetch_html_playwright(url, kode_instansi, retry_count + 1)
                else:
                    logger.warning("[pw] Cloudflare challenge persists after all retries for {}", url)
                    return None

            return html

        except Exception as exc:
            logger.warning("[pw] Error fetching {}: {}", url, exc)
            return None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scrape_detail(
        self,
        package: TenderPackage,
        bypass_tahap_gate: bool = False,
    ) -> TenderDetail:
        """Scrape the detail page for a single tender package.

        Tries httpx first (with Referer), then falls back to Playwright
        (with session caching, human simulation, and smart retry).
        """
        url = (
            f"https://spse.inaproc.id/{package.kode_instansi}"
            f"/lelang/{package.id_lelang}/pengumumanlelang"
        )
        referer = f"https://spse.inaproc.id/{package.kode_instansi}/"
        logger.debug(
            "[{}/{}] Fetching detail page…",
            package.kode_instansi, package.id_lelang,
        )

        # Try httpx first (with Referer header) — session warmed automatically
        html = await self._fetch_html_httpx(url, referer=referer, kode_instansi=package.kode_instansi)

        # Fallback to Playwright if httpx failed (auto-start engine if needed)
        if html is None:
            await self._ensure_playwright()
            if self._pw is not None:
                logger.debug(
                    "[{}/{}] Falling back to Playwright (with stealth session)…",
                    package.kode_instansi, package.id_lelang,
                )
                html = await self._fetch_html_playwright(url, package.kode_instansi)

        if html is None:
            raise ParsingError(
                url=url,
                detail="Could not fetch detail page via httpx or Playwright (Cloudflare / access denied)",
            )

        # Extract KBLI code from HTML
        kbli_code, kbli_description = self._extract_kbli_from_html(html)

        # Extract requirement text for AI matching
        requirement_text = self._extract_requirement_text(html)

        # Extract tahap from HTML
        tahap = self._extract_tahap_from_html(html)
        if tahap is None:
            tahap = self._extract_tahap_selectolax(html)
        if tahap is None:
            tahap = "tidak diketahui"
            logger.warning(
                "[{}/{}] Could not extract tahap — defaulting to '{}'",
                package.kode_instansi, package.id_lelang, tahap,
            )

        # Stage 2 eligibility gate: skip packages that are neither actively
        # eligible (prakualifikasi) nor retained awarded/completed tenders.
        # Phase 5: awarded tenders are retained (not dropped) so participant and
        # future winner data can be captured. Aborted (cancelled/failed) tenders
        # and unrecognised tahap are still skipped.
        if not bypass_tahap_gate and not self.is_eligible_tahap(tahap) and not self.is_retained_tahap(tahap):
            logger.info(
                "[{}/{}] [SKIP] Tahap '{}' — non-aktif / bukan prakualifikasi",
                package.kode_instansi, package.id_lelang, tahap,
            )
            raise PackageSkippedError(
                id_lelang=package.id_lelang,
                reason=f"Tahap '{tahap}' — non-aktif / bukan prakualifikasi",
            )

        detail = TenderDetail(
            kode_instansi=package.kode_instansi,
            id_lelang=package.id_lelang,
            nama_paket=package.nama_paket,
            instansi=package.instansi,
            hps=package.nilai_pagu,
            jenis_pengadaan=package.jenis_pengadaan,
            tahap_saat_ini=tahap,
            kbli_code=kbli_code,
            kbli_description=kbli_description,
            url_pengumuman=url,
            requirement_text=requirement_text,
            # New enrichment fields from pengumuman page
            metode_pengadaan=self._extract_metode_pengadaan(html),
            lokasi_pekerjaan=self._extract_lokasi_pekerjaan(html),
            tahun_anggaran=self._extract_tahun_anggaran(html),
            satuan_kerja_detail=self._extract_satuan_kerja(html),
            syarat_kualifikasi=requirement_text[:2000] if requirement_text else "",
        )

        # Fetch jadwal + peserta pages (separate URLs, non-fatal on failure)
        referer = f"https://spse.inaproc.id/{package.kode_instansi}/"
        try:
            detail.jadwal_json = await self._fetch_jadwal(url, referer, package.kode_instansi)
        except Exception as exc:
            logger.debug("[{}/{}] Jadwal fetch failed: {}", package.kode_instansi, package.id_lelang, exc)
        try:
            count, participants = await self._fetch_peserta_data(url, referer, package.kode_instansi)
            detail.peserta_count = count
            detail.participants = participants
        except Exception as exc:
            logger.debug("[{}/{}] Peserta fetch failed: {}", package.kode_instansi, package.id_lelang, exc)

        # Fetch winner page (Phase 6B) for retained awarded/completed tenders
        # only — never for tenders that have not reached a pemenang stage. Read
        # from the verified /evaluasi/{id}/pemenang page; non-fatal on failure.
        if self.is_retained_tahap(tahap):
            try:
                winner = await self._fetch_winner(url, referer, package.kode_instansi)
                if winner:
                    detail.winner = winner
                    detail.winner_source_url = self._build_winner_url(url)
            except Exception as exc:
                logger.debug("[{}/{}] Winner fetch failed: {}", package.kode_instansi, package.id_lelang, exc)

        logger.info(
            "[{}/{}] Tahap: '{}' | Prakualifikasi: {} | KBLI: {}",
            detail.kode_instansi,
            detail.id_lelang,
            detail.tahap_saat_ini,
            detail.is_prakualifikasi,
            detail.kbli_code or '-',
        )
        return detail

    async def scrape_details_batch(
        self,
        packages: list[TenderPackage],
    ) -> list[TenderDetail]:
        """Scrape details for multiple packages sequentially.

        For concurrent version, use the orchestrator in main.py.
        """
        results: list[TenderDetail] = []
        for pkg in packages:
            try:
                detail = await self.scrape_detail(pkg)
                results.append(detail)
            except PackageSkippedError as exc:
                logger.info("[{}/{}] {}", pkg.kode_instansi, pkg.id_lelang, exc)
            except ParsingError as exc:
                logger.warning(
                    "[{}/{}] Parse error: {}",
                    pkg.kode_instansi, pkg.id_lelang, exc,
                )
            except Exception as exc:
                logger.error(
                    "[{}/{}] Unexpected error: {}",
                    pkg.kode_instansi, pkg.id_lelang, exc,
                )
        return results
