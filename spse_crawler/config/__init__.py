"""Configuration package for SPSE Inaproc Crawler."""

from spse_crawler.config.settings import (
    INSTANSI_CODES,
    Settings,
    get_settings,
)

__all__: list[str] = ["INSTANSI_CODES", "Settings", "get_settings"]
