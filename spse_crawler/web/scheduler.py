"""APScheduler integration for background crawl scheduling."""

from __future__ import annotations

import asyncio
import os
import threading
import traceback
from datetime import datetime, timezone

import json
from pathlib import Path
from django.conf import settings

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.utils import timezone as dj_timezone
from loguru import logger

from spse_crawler.config.settings import INSTANSI_CODES, InstansiConfig, get_settings

_scheduler: BackgroundScheduler | None = None


def _get_state_file() -> Path:
    """Return the persistent scheduler state file path."""
    base_dir = getattr(settings, "BASE_DIR", Path("."))
    data_dir = Path(base_dir) / "data"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return data_dir / "scheduler_state.json"


def is_scheduler_enabled() -> bool:
    """Check whether scheduler is enabled across all processes."""
    state_file = _get_state_file()
    try:
        if state_file.exists():
            with open(state_file, "r") as f:
                data = json.load(f)
                return bool(data.get("enabled", False))
    except Exception as e:
        logger.debug("[SCHEDULER] Failed to read scheduler state: {}", e)
    return False


def set_scheduler_enabled(enabled: bool) -> None:
    """Persist scheduler enabled state across all worker processes."""
    state_file = _get_state_file()
    try:
        data = {
            "enabled": bool(enabled),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_file = state_file.parent / f"scheduler_state.tmp.{os.getpid()}"
        with open(tmp_file, "w") as f:
            json.dump(data, f)
        tmp_file.replace(state_file)
        logger.info("[SCHEDULER] Persistent scheduler state set to: {}", enabled)
    except Exception as e:
        logger.error("[SCHEDULER] Failed to write scheduler state: {}", e)


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler


def start_scheduler() -> None:
    set_scheduler_enabled(True)
    scheduler = get_scheduler()
    if scheduler.running:
        try:
            scheduler.modify_job("hourly_crawl", next_run_time=datetime.now(timezone.utc))
        except Exception:
            pass
        return

    # Trigger immediately upon starting, then every 3 hours
    scheduler.add_job(
        func=_run_background_crawl,
        trigger=IntervalTrigger(hours=3),
        id="hourly_crawl",
        name="SPSE Crawl (Every 3 Hours)",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    # Intelligence pipeline — process queued AI Match / Opportunity Score jobs.
    scheduler.add_job(
        func=_run_background_pipeline,
        trigger=IntervalTrigger(minutes=10),
        id="intelligence_pipeline_tick",
        name="Intelligence Pipeline Tick",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.start()
    logger.info("APScheduler started — 3-hourly crawl + pipeline jobs registered")


def stop_scheduler() -> None:
    set_scheduler_enabled(False)
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


def trigger_now() -> None:
    scheduler = get_scheduler()
    if not scheduler.running:
        start_scheduler()
    scheduler.modify_job("hourly_crawl", next_run_time=datetime.now(timezone.utc))
    logger.info("Crawl job triggered manually")


def trigger_pipeline_now() -> None:
    scheduler = get_scheduler()
    if not scheduler.running:
        start_scheduler()
    scheduler.modify_job("intelligence_pipeline_tick", next_run_time=datetime.now(timezone.utc))
    logger.info("Intelligence pipeline job triggered manually")


def _run_background_crawl() -> None:
    if not is_scheduler_enabled():
        logger.info("[SCHEDULER] Scheduler is marked disabled in persistent state — skipping tick")
        return

    from .views import _crawl_lock
    if not _crawl_lock.acquire(blocking=False):
        logger.warning("[SCHEDULER] Crawl already in progress — skipping")
        return
    try:
        # Django setup for background thread
        import django
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web_ui.settings")
        django.setup()

        from spse_crawler.web.models import CrawlJob
        if CrawlJob.objects.filter(status="running").exists():
            logger.warning("[SCHEDULER] Another crawl job is already running in the database — skipping")
            return

        logger.info("[SCHEDULER] Starting background crawl...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_do_crawl())
        logger.info("[SCHEDULER] Background crawl completed")
    except Exception as exc:
        logger.error("[SCHEDULER] Background crawl failed: {}", exc)
        logger.error("[SCHEDULER] Traceback:\n{}", traceback.format_exc())
    finally:
        _crawl_lock.release()
        loop.close()


def _run_background_pipeline() -> None:
    """Bounded background tick for the intelligence pipeline (APScheduler).

    Executes at most AI_PIPELINE_BATCH_SIZE jobs per tick. Isolated from the
    crawl lock — pipeline and crawl may run independently.
    """
    try:
        import django
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web_ui.settings")
        django.setup()

        from spse_crawler.services.intelligence_pipeline import run_cycle

        logger.info("[PIPELINE] Starting background intelligence cycle...")
        summary = run_cycle()
        logger.info("[PIPELINE] Cycle complete — {}", summary)
    except Exception as exc:
        logger.error("[PIPELINE] Background intelligence cycle failed: {}", exc)
        logger.error("[PIPELINE] Traceback:\n{}", traceback.format_exc())


async def _do_crawl() -> None:
    from asgiref.sync import sync_to_async
    from spse_crawler.core.browser import SpseHttpClient
    from spse_crawler.core.exceptions import PackageSkippedError
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.parsers.discovery import DiscoveryParser
    from spse_crawler.services.progress_tracker import get_progress_tracker
    from spse_crawler.services.priority_scorer import score_it_priority
    from spse_crawler.services.tender_participant_store import sync_tender_participants
    from spse_crawler.services.tender_winner_store import sync_tender_winner
    from spse_crawler.web.models import CrawlJob, TenderResult

    _create_job = sync_to_async(CrawlJob.objects.create, thread_sensitive=False)
    _update_or_create = sync_to_async(TenderResult.objects.update_or_create, thread_sensitive=False)
    _sync_participants = sync_to_async(sync_tender_participants, thread_sensitive=False)
    _sync_winner = sync_to_async(sync_tender_winner, thread_sensitive=False)

    settings = get_settings()
    worker_count = 3
    instansi_list = list(INSTANSI_CODES)
    progress = get_progress_tracker()

    progress.start(total_instansi=len(instansi_list))

    job = await _create_job(
        instansi_input="all (scheduled)",
        workers=worker_count,
        status="running",
    )
    logger.info("[SCHEDULER] CrawlJob #{} created", job.id)

    try:
        sem = asyncio.Semaphore(worker_count)
        total = 0
        skipped = 0
        errors = 0

        async with SpseHttpClient(settings) as http:
            discovery = DiscoveryParser(http, settings)
            detail_parser = DetailParser(http, settings=settings)

            async def _process_instansi(kode: str, idx: int) -> tuple[int, int]:
                """Returns (saved_count, skipped_count)."""
                nonlocal errors
                async with sem:
                    saved = 0
                    local_skipped = 0
                    progress.set_instansi(kode, idx)
                    try:
                        instansi_config = InstansiConfig(kode=kode)
                        packages = await discovery.fetch_packages(instansi_config)
                        progress.add_packages_found(kode, len(packages))
                        for pkg in packages:
                            try:
                                detail = await detail_parser.scrape_detail(pkg)
                                kbli_code = detail.kbli_code
                                pscore = score_it_priority(
                                    kbli_code=kbli_code,
                                    nama_paket=detail.nama_paket,
                                    jenis_pengadaan=detail.jenis_pengadaan,
                                )
                                try:
                                    tender_obj, _ = await _update_or_create(
                                        kode_instansi=detail.kode_instansi,
                                        id_lelang=detail.id_lelang,
                                        defaults={
                                            "nama_paket": detail.nama_paket[:500],
                                            "instansi": detail.instansi[:200],
                                            "hps": detail.hps,
                                            "jenis_pengadaan": detail.jenis_pengadaan[:200],
                                            "tahap_saat_ini": detail.tahap_saat_ini[:200],
                                            "is_prakualifikasi": detail.is_prakualifikasi,
                                            "kbli_code": kbli_code[:10] if kbli_code else "",
                                            "kbli_description": detail.kbli_description[:300],
                                            "is_it_priority": pscore.is_it_priority,
                                            "priority_score": pscore.priority_score,
                                            "url_pengumuman": detail.url_pengumuman,
                                            "requirement_text": detail.requirement_text,
                                            "jadwal_json": detail.jadwal_json,
                                            "syarat_kualifikasi": detail.syarat_kualifikasi,
                                            "peserta_count": detail.peserta_count,
                                            "lokasi_pekerjaan": detail.lokasi_pekerjaan,
                                            "metode_pengadaan": detail.metode_pengadaan[:200],
                                            "tahun_anggaran": detail.tahun_anggaran[:20],
                                            "satuan_kerja_detail": detail.satuan_kerja_detail[:300],
                                        },
                                    )
                                except Exception as save_err:
                                    if "500" in str(save_err) or "too long" in str(save_err).lower():
                                        logger.warning("[{}/{}] DB column length limitation hit, truncating lokasi_pekerjaan...", kode, detail.id_lelang)
                                        tender_obj, _ = await _update_or_create(
                                            kode_instansi=detail.kode_instansi,
                                            id_lelang=detail.id_lelang,
                                            defaults={
                                                "nama_paket": detail.nama_paket[:500],
                                                "instansi": detail.instansi[:200],
                                                "hps": detail.hps,
                                                "jenis_pengadaan": detail.jenis_pengadaan[:200],
                                                "tahap_saat_ini": detail.tahap_saat_ini[:200],
                                                "is_prakualifikasi": detail.is_prakualifikasi,
                                                "kbli_code": kbli_code[:10] if kbli_code else "",
                                                "kbli_description": detail.kbli_description[:300],
                                                "is_it_priority": pscore.is_it_priority,
                                                "priority_score": pscore.priority_score,
                                                "url_pengumuman": detail.url_pengumuman,
                                                "requirement_text": detail.requirement_text,
                                                "jadwal_json": detail.jadwal_json,
                                                "syarat_kualifikasi": detail.syarat_kualifikasi,
                                                "peserta_count": detail.peserta_count,
                                                "lokasi_pekerjaan": detail.lokasi_pekerjaan[:490],
                                                "metode_pengadaan": detail.metode_pengadaan[:200],
                                                "tahun_anggaran": detail.tahun_anggaran[:20],
                                                "satuan_kerja_detail": detail.satuan_kerja_detail[:300],
                                            },
                                        )
                                    else:
                                        raise
                                await _sync_participants(
                                    tender_obj,
                                    detail.participants,
                                    source_url=detail.url_pengumuman,
                                    source_fetched_at=detail.scraped_at,
                                )
                                # Persist the awarded winner (Phase 6B) when
                                # present — only retained awarded tenders fetch a
                                # winner page; a missing winner never destroys
                                # existing history.
                                if detail.winner:
                                    await _sync_winner(
                                        tender_obj,
                                        detail.winner,
                                        source_url=detail.winner_source_url,
                                        source_fetched_at=detail.scraped_at,
                                    )
                                saved += 1
                                progress.mark_processed(kode, detail.id_lelang, saved=True)
                            except PackageSkippedError as exc:
                                local_skipped += 1
                                progress.mark_processed(kode, pkg.id_lelang, saved=False, skipped=True)
                                logger.debug("[{}/{}] {}", kode, pkg.id_lelang, exc)
                            except Exception as exc:
                                # Cloudflare blocked — save Stage 1 data if eligible
                                if pkg.status and "prakualifikasi" in pkg.status.lower():
                                    pscore = score_it_priority(
                                        kbli_code="",
                                        nama_paket=pkg.nama_paket,
                                        jenis_pengadaan=pkg.jenis_pengadaan,
                                    )
                                    await _update_or_create(
                                        kode_instansi=pkg.kode_instansi,
                                        id_lelang=pkg.id_lelang,
                                        defaults={
                                            "nama_paket": pkg.nama_paket,
                                            "instansi": pkg.instansi,
                                            "hps": pkg.nilai_pagu,
                                            "jenis_pengadaan": pkg.jenis_pengadaan,
                                            "tahap_saat_ini": pkg.status,
                                            "is_prakualifikasi": True,
                                            "kbli_code": "",
                                            "kbli_description": "",
                                            "is_it_priority": pscore.is_it_priority,
                                            "priority_score": pscore.priority_score,
                                            "url_pengumuman": pkg.url_pengumuman,
                                        },
                                    )
                                    saved += 1
                                    progress.mark_processed(kode, pkg.id_lelang, saved=True)
                                else:
                                    local_skipped += 1
                                    errors += 1
                                    progress.mark_error(kode, str(exc))
                                    logger.debug("[{}/{}] Detail failed + non-eligible: {}", kode, pkg.id_lelang, exc)
                        progress.finish_instansi(kode)
                        return saved, local_skipped
                    except Exception as exc:
                        errors += 1
                        progress.mark_error(kode, str(exc))
                        logger.error("[{}] Scheduled crawl failed: {}", kode, exc)
                        return 0, 0

            tasks = [_process_instansi(k, i) for i, k in enumerate(instansi_list)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, tuple):
                    total += r[0]
                    skipped += r[1]

        # Cleanup cached Playwright sessions
        if detail_parser is not None:
            await detail_parser.close()

        @sync_to_async(thread_sensitive=False)
        def _finish_job():
            job.status = "completed"
            job.total_packages = total
            job.total_details = total
            job.finished_at = dj_timezone.now()
            job.save()

        await _finish_job()
        progress.finish(success=True)
        logger.info("[SCHEDULER] Completed — {} saved, {} skipped, {} errors", total, skipped, errors)

        # Post-crawl housekeeping: safely remove stale/non-retained records.
        # Historical awarded/decided tenders are NEVER purged here
        # (Phase 6A — historical retention). See flush_non_retained().
        # Completed ("selesai") tenders without active submissions are purged
        # by purge_completed_tenders() — they are garbage data.
        @sync_to_async(thread_sensitive=False)
        def _auto_purge():
            from spse_crawler.services.purger import flush_non_retained, purge_completed_tenders
            result = flush_non_retained()
            if result.deleted_count > 0:
                logger.info("[SCHEDULER] Auto-purge: removed {} non-retained records", result.deleted_count)
            completed_result = purge_completed_tenders()
            if completed_result.deleted_count > 0:
                logger.info("[SCHEDULER] Auto-purge: removed {} completed records", completed_result.deleted_count)

        await _auto_purge()

        # Post-crawl: enqueue newly discovered active tenders for intelligence.
        @sync_to_async(thread_sensitive=False)
        def _enqueue_intelligence():
            from spse_crawler.services.intelligence_pipeline import (
                enqueue_new_after_crawl,
            )
            count = enqueue_new_after_crawl()
            if count:
                logger.info("[SCHEDULER] Intelligence: enqueued {} AI_MATCH jobs",
                            count)

        await _enqueue_intelligence()

    except Exception as exc:
        @sync_to_async(thread_sensitive=False)
        def _fail_job():
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = dj_timezone.now()
            job.save()

        await _fail_job()
        progress.finish(success=False, error_msg=str(exc))
        logger.error("[SCHEDULER] Job #{} failed: {}", job.id, exc)
