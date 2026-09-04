"""Custom exception hierarchy for SPSE Inaproc Crawler."""

from __future__ import annotations


class SPSECrawlerError(Exception):
    """Base exception for all SPSE crawler errors."""


class CloudflareBlockError(SPSECrawlerError):
    """Raised when a request is blocked by Cloudflare WAF."""

    def __init__(self, url: str, status_code: int | None = None) -> None:
        self.url = url
        self.status_code = status_code
        msg = f"Cloudflare block detected for URL: {url}"
        if status_code is not None:
            msg += f" (HTTP {status_code})"
        super().__init__(msg)


class CloudflareChallengeError(SPSECrawlerError):
    """Raised when Cloudflare JS challenge / captcha is detected."""

    def __init__(self, url: str, detail: str = "") -> None:
        self.url = url
        self.detail = detail
        msg = f"Cloudflare challenge detected for: {url}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)


class ParsingError(SPSECrawlerError):
    """Raised when HTML/JSON parsing fails or produces invalid data."""

    def __init__(self, url: str, detail: str = "") -> None:
        self.url = url
        self.detail = detail
        msg = f"Parsing failed for URL: {url}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)


class InstansiNotFoundError(SPSECrawlerError):
    """Raised when a target instansi code is not found in the config."""

    def __init__(self, kode_instansi: str) -> None:
        self.kode_instansi = kode_instansi
        super().__init__(f"Instansi '{kode_instansi}' not found in configuration")


class RateLimitExceededError(SPSECrawlerError):
    """Raised when the per-host rate limit is exceeded."""

    def __init__(self, host: str, retry_after: float | None = None) -> None:
        self.host = host
        self.retry_after = retry_after
        msg = f"Rate limit exceeded for host: {host}"
        if retry_after is not None:
            msg += f" (retry after {retry_after:.1f}s)"
        super().__init__(msg)


class PackageSkippedError(SPSECrawlerError):
    """Raised when a package is intentionally skipped (non-eligible tahap/status)."""

    def __init__(self, id_lelang: str, reason: str) -> None:
        self.id_lelang = id_lelang
        self.reason = reason
        super().__init__(f"Package {id_lelang} skipped: {reason}")
