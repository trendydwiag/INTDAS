"""Main application entry point.

Initialise logging, configuration, and orchestrate the crawl workflow.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from spse_crawler.config.settings import Settings, get_settings


def _setup_logging(settings: Settings) -> None:
    """Configure loguru with the level from settings."""
    logger.remove()
    logger.add(
        sink=__import__("sys").stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )


async def run_crawl(settings: Settings) -> None:
    """Run the crawl pipeline for configured instansi."""
    from spse_crawler.config.settings import INSTANSI_CODES, InstansiConfig
    from spse_crawler.core.browser import SpseHttpClient
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.parsers.discovery import DiscoveryParser

    logger.info("Starting SPSE Inaproc Crawler …")
    logger.info("Enabled instansi: {} codes", len(INSTANSI_CODES))

    async with SpseHttpClient(settings) as http:
        discovery = DiscoveryParser(http, settings)
        detail_parser = DetailParser(http, settings=settings)

        instansi_list = list(INSTANSI_CODES)[:5]
        for kode in instansi_list:
            instansi = InstansiConfig(kode=kode)
            try:
                packages = await discovery.fetch_packages(instansi)
                logger.info("[{}] Found {} eligible packages", kode, len(packages))
            except Exception as exc:
                logger.error("[{}] Discovery failed: {}", kode, exc)

    logger.info("Crawl finished.")


def main() -> None:
    """Sync entry point used by cli.py and direct execution."""
    settings = get_settings()
    _setup_logging(settings)
    asyncio.run(run_crawl(settings))


if __name__ == "__main__":
    main()
