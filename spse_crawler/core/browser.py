"""Core HTTP client and browser engine for SPSE crawling.

Provides ``SpseHttpClient`` (httpx-based, primary) and ``PlaywrightEngine``
(fallback for JS-rendered pages with advanced stealth & anti-bot evasion).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

from spse_crawler.config.settings import Settings, get_settings
from spse_crawler.core.exceptions import CloudflareBlockError, CloudflareChallengeError

# Phrases indicating SPSE access denial
_ACCESS_DENIED_MARKERS: tuple[str, ...] = (
    "Akses Ditolak",
    "tidak diizinkan",
    "Anda tidak diizinkan",
    "akses ditolak",
)

# Phrases indicating Cloudflare JS challenge / captcha
# NOTE: These must be specific enough to avoid false positives on legitimate
# SPSE pages that may contain 'cloudflare' in CSS/JS CDN references.
_CLOUDFLARE_CHALLENGE_MARKERS: tuple[str, ...] = (
    "Just a moment",
    "Checking your browser",
    "Enable JavaScript and cookies to continue",
    "challenges.cloudflare.com",
    "cf-challenge-running",
    "Attention Required! | Cloudflare",
)

# Realistic Chromium launch args for anti-automation evasion
_STEALTH_CHROMIUM_ARGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--start-maximized",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-first-run",
    "--password-store=basic",
    "--use-mock-keychain",
]

# Realistic extra HTTP headers sent on every page navigation
_STEALTH_EXTRA_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# JavaScript injected into every new page to mask automation signals
_STEALTH_JS_INITSCRIPT: str = """
// Override navigator.webdriver — most critical fingerprint
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Fake languages
Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id', 'en-US', 'en'] });

// Fake platform
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// Fake plugins (empty but present)
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2,, 3, 4, 5].map(() => ({ length: 1 })),
});

// Chrome runtime
window.chrome = { runtime: {} };

// Permissions query override
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
"""


class SpseHttpClient:
    """Async HTTP client built on httpx with session management and rate limiting.

    Handles CSRF token extraction, cookie persistence, and retry logic.
    Warms cookies per-instansi by visiting the landing page before detail fetches.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0.0
        # Per-instansi cookie warming: kode -> warmed_at timestamp
        self._warmed_instansi: dict[str, float] = {}
        self._WARM_TTL: float = 600.0  # 10 minutes

    async def __aenter__(self) -> SpseHttpClient:
        self._client = httpx.AsyncClient(
            headers=self._settings.default_headers,
            timeout=httpx.Timeout(self._settings.request_timeout),
            follow_redirects=True,
            verify=False,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SpseHttpClient must be used as async context manager.")
        return self._client

    async def _rate_limit(self) -> None:
        """Enforce minimum delay between requests."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        wait = self._settings.rate_limit_delay - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_time = asyncio.get_event_loop().time()

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Perform a GET request with rate limiting and retry."""
        await self._rate_limit()
        for attempt in range(1, self._settings.retry_max_attempts + 1):
            try:
                resp = await self.client.get(url, headers=headers)
                if resp.status_code == 403:
                    raise CloudflareBlockError(url=url, status_code=403)
                resp.raise_for_status()
                return resp
            except CloudflareBlockError:
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 and attempt < self._settings.retry_max_attempts:
                    backoff = self._settings.retry_backoff_base ** attempt
                    logger.warning(
                        "HTTP {} for {} — retry {}/{} in {:.1f}s",
                        exc.response.status_code, url, attempt,
                        self._settings.retry_max_attempts, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise
        raise RuntimeError(f"Exhausted retries for GET {url}")

    async def post(
        self,
        url: str,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Perform a POST request with rate limiting and retry."""
        await self._rate_limit()
        for attempt in range(1, self._settings.retry_max_attempts + 1):
            try:
                resp = await self.client.post(url, data=data, headers=headers)
                if resp.status_code == 403:
                    raise CloudflareBlockError(url=url, status_code=403)
                resp.raise_for_status()
                return resp
            except CloudflareBlockError:
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 and attempt < self._settings.retry_max_attempts:
                    backoff = self._settings.retry_backoff_base ** attempt
                    logger.warning(
                        "HTTP {} for {} — retry {}/{} in {:.1f}s",
                        exc.response.status_code, url, attempt,
                        self._settings.retry_max_attempts, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise
        raise RuntimeError(f"Exhausted retries for POST {url}")

    async def warm_instansi_session(self, kode_instansi: str) -> bool:
        """Visit the instansi landing page to acquire SPSE_SESSION + Cloudflare cookies.

        This is critical: without these cookies, detail page fetches return 403.
        Cookies are cached per-instansi with a TTL to avoid redundant warming.
        Returns True if warming succeeded.
        """
        import time as _time

        now = _time.monotonic()
        if kode_instansi in self._warmed_instansi:
            if now - self._warmed_instansi[kode_instansi] < self._WARM_TTL:
                logger.debug("[httpx] Session already warmed for {}", kode_instansi)
                return True

        landing_url = f"https://spse.inaproc.id/{kode_instansi}/"
        try:
            resp = await self.get(landing_url)
            html = resp.text
            # Even if we got HTML, check it's not access-denied
            from spse_crawler.core.browser import PlaywrightEngine
            if PlaywrightEngine.is_access_denied(html):
                logger.warning("[httpx] Session warm got access-denied for {}", kode_instansi)
                return False
            self._warmed_instansi[kode_instansi] = now
            # Extract and log cookie count for debugging
            cookies = dict(self._client.cookies)
            logger.debug(
                "[httpx] Session warmed for {} — {} cookies acquired",
                kode_instansi, len(cookies),
            )
            return True
        except Exception as exc:
            logger.warning("[httpx] Session warm failed for {}: {}", kode_instansi, exc)
            return False


class PlaywrightEngine:
    """Manages the lifecycle of a Chromium browser with advanced stealth patches.

    Uses ``playwright-stealth`` + manual JS injection + realistic headers
    + human-like behavior simulation to bypass Cloudflare WAF challenges.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._stealth = Stealth()

    async def start(self) -> None:
        """Launch the browser with full stealth configuration."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._settings.headless,
            args=_STEALTH_CHROMIUM_ARGS,
        )
        logger.info("Playwright Chromium browser launched (headless={})", self._settings.headless)

    async def stop(self) -> None:
        """Gracefully close the browser."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Playwright browser closed")

    async def new_context(self) -> BrowserContext:
        """Create a new browser context with full stealth applied.

        Applies:
        - playwright-stealth patches
        - Realistic viewport, locale, timezone
        - Extra HTTP headers (Sec-Ch-Ua, Sec-Fetch-*, etc.)
        - JS init script to mask navigator.webdriver, languages, platform
        """
        if self._browser is None:
            raise RuntimeError("Browser not started. Call start() first.")

        context = await self._browser.new_context(
            user_agent=self._settings.user_agent,
            viewport={"width": 1920, "height": 1080},
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            extra_http_headers=_STEALTH_EXTRA_HEADERS,
            java_script_enabled=True,
            ignore_https_errors=True,
        )

        # Apply playwright-stealth patches
        await self._stealth.apply_stealth_async(context)

        # Inject JS to mask automation fingerprint on every new page
        await context.add_init_script(_STEALTH_JS_INITSCRIPT)

        return context

    async def warm_session(
        self,
        context: BrowserContext,
        instansi_url: str,
        timeout_ms: int = 20_000,
    ) -> str | None:
        """Visit the instansi landing page to acquire Cloudflare + session cookies.

        Performs human-like navigation:
        1. Navigate to the landing page
        2. Wait for networkidle (Cloudflare challenge resolution)
        3. Simulate random mouse movement + small scroll
        4. Return page HTML

        Returns None on failure.
        """
        page: Page | None = None
        try:
            page = await context.new_page()

            await page.goto(instansi_url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Wait for Cloudflare challenge to resolve (up to 8s)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass

            # Extra settle time for CF token injection
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # Human-like mouse movement
            try:
                await page.mouse.move(
                    random.randint(200, 800),
                    random.randint(200, 600),
                    steps=random.randint(5, 15),
                )
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await page.mouse.wheel(0, random.randint(-50, 150))
            except Exception:
                pass

            html = await page.content()

            if self.is_cloudflare_challenge(html):
                logger.debug("[pw] Cloudflare challenge still present after warm for {}", instansi_url)

            logger.debug("[pw] Warmed session for {}", instansi_url)
            return html

        except Exception as exc:
            logger.warning("[pw] Session warm failed for {}: {}", instansi_url, exc)
            return None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    @staticmethod
    def is_access_denied(html: str) -> bool:
        """Return True if the HTML contains SPSE access-denied markers."""
        html_lower = html.lower()
        return any(marker.lower() in html_lower for marker in _ACCESS_DENIED_MARKERS)

    @staticmethod
    def is_cloudflare_challenge(html: str) -> bool:
        """Return True if the HTML looks like a Cloudflare JS challenge page."""
        html_lower = html.lower()
        return any(marker.lower() in html_lower for marker in _CLOUDFLARE_CHALLENGE_MARKERS)

    @staticmethod
    def is_blocked_or_challenge(html: str) -> bool:
        """Return True if HTML is either access-denied or Cloudflare challenge."""
        return PlaywrightEngine.is_access_denied(html) or PlaywrightEngine.is_cloudflare_challenge(html)

    async def __aenter__(self) -> PlaywrightEngine:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()
