"""CLI interface built with Typer + Rich."""

from __future__ import annotations

import asyncio
import os

import typer
from loguru import logger
from rich.console import Console

from spse_crawler.config.settings import INSTANSI_CODES, InstansiConfig, get_settings

app = typer.Typer(
    name="spse-crawler",
    help="SPSE Inaproc Multi-Tenant Scraper & Intelligence CLI.",
    add_completion=False,
)
console = Console()


@app.command()
def crawl(
    instansi: str | None = typer.Option(
        None,
        "--instansi",
        "-i",
        help="Target instansi code (e.g. 'kemenkeu'). Omit to crawl DEFAULT_INSTANSI_CODES.",
    ),
    workers: int = typer.Option(
        3,
        "--workers",
        "-w",
        help="Number of concurrent workers.",
    ),
    all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Crawl ALL 736 instansi codes.",
    ),
) -> None:
    """Run the tender crawl pipeline."""
    settings = get_settings()
    _setup_logging(settings)

    # Determine target codes
    if all:
        target_codes = list(INSTANSI_CODES)
    elif instansi:
        if instansi not in INSTANSI_CODES:
            console.print(f"[red]Instansi '{instansi}' not found. Use 'list-instansi' to see valid codes.[/red]")
            raise typer.Exit(code=1)
        target_codes = [instansi]
    else:
        target_codes = list(settings.instansi_codes)

    console.print(f"[bold green]Starting crawl for {len(target_codes)} instansi (workers={workers})…[/bold green]")

    asyncio.run(_run_crawl_async(target_codes, workers))
    console.print("[bold green]Done.[/bold green]")


@app.command("recrawl-missing-details")
def recrawl_missing_details(
    limit: int = typer.Option(50, "--limit", "-l", help="Max records to re-crawl."),
    workers: int = typer.Option(3, "--workers", "-w", help="Number of concurrent workers."),
) -> None:
    """Re-crawl detail pages for records with empty enrichment fields."""
    settings = get_settings()
    _setup_logging(settings)

    # Django setup
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web_ui.settings")
    import django
    django.setup()

    from spse_crawler.web.models import TenderResult

    # Find records missing enrichment — any of the key fields empty
    from django.db.models import Q
    qs = TenderResult.objects.filter(
        Q(jadwal_json=[]) | Q(lokasi_pekerjaan="") | Q(syarat_kualifikasi="")
    ).order_by("-scraped_at")[:limit]

    records = list(qs)
    console.print(f"[bold yellow]Found {len(records)} records with missing details (limit={limit})[/bold yellow]")

    if not records:
        console.print("[green]All records have enrichment data. Nothing to do.[/green]")
        raise typer.Exit()

    asyncio.run(_recrawl_details_async(records, workers))
    console.print("[bold green]Re-crawl complete.[/bold green]")


@app.command("recrawl-tender")
def recrawl_tender(
    tender_id: str = typer.Argument(..., help="Tender ID (id_lelang) to re-crawl."),
    instansi: str = typer.Option(..., "--instansi", "-i", help="Instansi code."),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of concurrent workers."),
) -> None:
    """Re-crawl a specific tender by ID and instansi code."""
    settings = get_settings()
    _setup_logging(settings)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web_ui.settings")
    import django
    django.setup()

    from spse_crawler.web.models import TenderResult

    # Find or create record
    record, created = TenderResult.objects.get_or_create(
        kode_instansi=instansi,
        id_lelang=tender_id,
        defaults={"nama_paket": f"Re-crawl: {tender_id}", "instansi": instansi},
    )

    console.print(f"[bold blue]Re-crawling tender {tender_id} (instansi={instansi})…[/bold blue]")
    asyncio.run(_recrawl_details_async([record], workers))
    console.print("[bold green]Done.[/bold green]")

    # Show result
    record.refresh_from_db()
    console.print(f"  lokasi_pekerjaan: {record.lokasi_pekerjaan[:60] or '(empty)'}")
    console.print(f"  metode_pengadaan: {record.metode_pengadaan[:60] or '(empty)'}")
    console.print(f"  jadwal_json: {len(record.jadwal_json)} entries")
    console.print(f"  peserta_count: {record.peserta_count}")
    console.print(f"  syarat_kualifikasi: {len(record.syarat_kualifikasi)} chars")
    console.print(f"  tahun_anggaran: {record.tahun_anggaran or '(empty)'}")


@app.command("list-instansi")
def list_instansi_cmd() -> None:
    """List all configured instansi codes."""
    console.print(f"[bold]Configured Instansi ({len(INSTANSI_CODES)} total):[/bold]\n")
    for i, kode in enumerate(INSTANSI_CODES):
        console.print(f"  {kode}")
        if i >= 49:
            console.print(f"  ... and {len(INSTANSI_CODES) - 50} more")
            break


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _run_crawl_async(target_codes: list[str], workers: int) -> None:
    """Run the crawl pipeline for given instansi codes."""
    from spse_crawler.core.browser import SpseHttpClient
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.parsers.discovery import DiscoveryParser

    settings = get_settings()

    async with SpseHttpClient(settings) as http:
        discovery = DiscoveryParser(http, settings)
        detail_parser = DetailParser(http, settings=settings)

        for kode in target_codes:
            instansi = InstansiConfig(kode=kode)
            try:
                packages = await discovery.fetch_packages(instansi)
                logger.info("[{}] Found {} eligible packages", kode, len(packages))

                for pkg in packages:
                    try:
                        detail = await detail_parser.scrape_detail(pkg)
                        logger.info("[{}/{}] OK — {}", kode, pkg.id_lelang, detail.nama_paket[:50])
                    except Exception as exc:
                        logger.warning("[{}/{}] Failed: {}", kode, pkg.id_lelang, str(exc)[:80])
            except Exception as exc:
                logger.error("[{}] Discovery failed: {}", kode, exc)

        await detail_parser.close()

    logger.info("Crawl finished.")


async def _recrawl_details_async(records: list, workers: int) -> None:
    """Re-crawl detail pages for existing TenderResult records with parallel workers.

    Each worker gets its own DetailParser (with separate Playwright engine + session cache).
    The shared SpseHttpClient is async-safe and handles rate limiting centrally.
    """
    import itertools
    from asgiref.sync import sync_to_async
    from spse_crawler.core.browser import SpseHttpClient, PlaywrightEngine
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.models.tender import TenderPackage
    from spse_crawler.services.tender_participant_store import sync_tender_participants
    from spse_crawler.web.models import TenderResult

    settings = get_settings()
    _update = sync_to_async(TenderResult.objects.update_or_create)
    _sync_participants = sync_to_async(sync_tender_participants)

    async with SpseHttpClient(settings) as http:
        # Create N worker parsers, each with its own Playwright engine
        parsers: list[DetailParser] = []
        for i in range(workers):
            pw = PlaywrightEngine(settings)
            await pw.start()
            parser = DetailParser(http, playwright_engine=pw, settings=settings)
            parsers.append(parser)
            logger.debug("[worker-{}] Started Playwright engine", i)

        semaphore = asyncio.Semaphore(workers)
        succeeded = 0
        failed = 0

        async def _process_one(rec, worker_idx):
            nonlocal succeeded, failed
            async with semaphore:
                parser = parsers[worker_idx]
                kode = rec.kode_instansi
                tid = rec.id_lelang
                pkg = TenderPackage(
                    kode_instansi=kode,
                    id_lelang=tid,
                    nama_paket=rec.nama_paket,
                    instansi=rec.instansi,
                    status=rec.tahap_saat_ini,
                    nilai_pagu=rec.hps,
                    jenis_pengadaan=rec.jenis_pengadaan,
                )
                try:
                    detail = await parser.scrape_detail(pkg)
                    tender_obj, _ = await _update(
                        kode_instansi=detail.kode_instansi,
                        id_lelang=detail.id_lelang,
                        defaults={
                            "nama_paket": detail.nama_paket or rec.nama_paket,
                            "instansi": detail.instansi or rec.instansi,
                            "hps": detail.hps or rec.hps,
                            "jenis_pengadaan": detail.jenis_pengadaan or rec.jenis_pengadaan,
                            "metode_pengadaan": detail.metode_pengadaan,
                            "tahap_saat_ini": detail.tahap_saat_ini,
                            "is_prakualifikasi": detail.is_prakualifikasi,
                            "kbli_code": detail.kbli_code,
                            "kbli_description": detail.kbli_description,
                            "url_pengumuman": detail.url_pengumuman,
                            "requirement_text": detail.requirement_text,
                            "jadwal_json": detail.jadwal_json,
                            "syarat_kualifikasi": detail.syarat_kualifikasi,
                            "peserta_count": detail.peserta_count,
                            "lokasi_pekerjaan": detail.lokasi_pekerjaan,
                            "tahun_anggaran": detail.tahun_anggaran,
                            "satuan_kerja_detail": detail.satuan_kerja_detail,
                        },
                    )
                    await _sync_participants(
                        tender_obj,
                        detail.participants,
                        source_url=detail.url_pengumuman,
                        source_fetched_at=detail.scraped_at,
                    )
                    succeeded += 1
                    logger.info("[w{}][{}/{}] Enriched — jadwal={} peserta={} lokasi={}",
                                worker_idx, kode, tid,
                                len(detail.jadwal_json), detail.peserta_count,
                                detail.lokasi_pekerjaan[:30])
                except Exception as exc:
                    failed += 1
                    logger.warning("[w{}][{}/{}] Re-crawl failed: {}",
                                   worker_idx, kode, tid, str(exc)[:100])

        # Round-robin worker assignment: each record goes to parser[i % workers]
        tasks = [
            _process_one(rec, i % workers)
            for i, rec in enumerate(records)
        ]

        logger.info("Starting {} tasks with {} workers…", len(tasks), workers)
        await asyncio.gather(*tasks)

        # Cleanup: close all Playwright engines
        for parser in parsers:
            await parser.close()

        logger.info("Re-crawl finished — {} succeeded, {} failed", succeeded, failed)


def _setup_logging(settings) -> None:
    """Configure loguru."""
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


if __name__ == "__main__":
    app()
