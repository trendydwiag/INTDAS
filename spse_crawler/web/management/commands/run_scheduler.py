"""Django management command to run the SPSE Crawler background scheduler as a dedicated single-process daemon.

Usage:
    python manage.py run_scheduler

Architecture:
    This command decouples the background scheduler (periodic crawl + PD-1 pipeline)
    from WSGI web worker processes (Gunicorn). It runs as a single-process worker
    container or systemd daemon, providing 100% scheduler liveness and lifecycle
    independence.
"""

import signal
import sys
import time
from django.core.management.base import BaseCommand
from loguru import logger
from spse_crawler.web.scheduler import get_scheduler, start_scheduler, stop_scheduler


class Command(BaseCommand):
    help = "Run the SPSE Crawler APScheduler background daemon as a single process."

    def handle(self, *args, **options):
        logger.info("[SCHEDULER] Starting dedicated single-process scheduler daemon...")
        
        running = True

        def _handle_shutdown(signum, frame):
            nonlocal running
            logger.info(f"[SCHEDULER] Signal {signum} received — shutting down scheduler daemon...")
            running = False

        signal.signal(signal.SIGINT, _handle_shutdown)
        signal.signal(signal.SIGTERM, _handle_shutdown)

        start_scheduler()
        s = get_scheduler()
        logger.info(f"[SCHEDULER] Daemon active. Running jobs: {[j.id for j in s.get_jobs()]}")

        try:
            while running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            logger.info("[SCHEDULER] Stopping scheduler daemon...")
            stop_scheduler()
            logger.info("[SCHEDULER] Daemon stopped cleanly.")

