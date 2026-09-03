"""In-memory progress tracker for crawl monitoring.

Thread-safe singleton — stores live crawl state polled by the frontend
via /api/crawl-progress/.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CrawlProgress:
    status: str = "Idle"  # Idle | Running | Completed | Failed
    current_instansi: str = ""
    total_instansi: int = 0
    completed_instansi: int = 0
    total_packages: int = 0
    processed_packages: int = 0
    saved_packages: int = 0
    skipped_packages: int = 0
    error_packages: int = 0
    current_activity: str = ""
    started_at: float = 0.0
    elapsed_seconds: float = 0.0
    estimated_remaining: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def percentage(self) -> float:
        if self.total_packages <= 0:
            return 0.0
        return min(100.0, (self.processed_packages / self.total_packages) * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "current_instansi": self.current_instansi,
            "total_instansi": self.total_instansi,
            "completed_instansi": self.completed_instansi,
            "total_packages": self.total_packages,
            "processed_packages": self.processed_packages,
            "saved_packages": self.saved_packages,
            "skipped_packages": self.skipped_packages,
            "error_packages": self.error_packages,
            "current_activity": self.current_activity,
            "percentage": round(self.percentage, 1),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "estimated_remaining": round(self.estimated_remaining, 1),
            "errors": self.errors[-5:],  # last 5 errors only
        }


class ProgressTracker:
    """Thread-safe crawl progress manager."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._progress = CrawlProgress()

    def reset(self) -> None:
        with self._lock:
            self._progress = CrawlProgress()

    def start(self, total_instansi: int, total_packages: int = 0) -> None:
        with self._lock:
            self._progress = CrawlProgress(
                status="Running",
                total_instansi=total_instansi,
                total_packages=total_packages,
                started_at=time.time(),
            )

    def finish(self, success: bool = True, error_msg: str = "") -> None:
        with self._lock:
            self._progress.status = "Completed" if success else "Failed"
            self._progress.elapsed_seconds = time.time() - self._progress.started_at
            if error_msg:
                self._progress.errors.append(error_msg)
            self._progress.current_activity = ""
            self._progress.current_instansi = ""

    def set_instansi(self, kode: str, index: int = 0) -> None:
        with self._lock:
            self._progress.current_instansi = kode
            self._progress.completed_instansi = index
            self._progress.current_activity = f"Memulai crawl {kode}..."
            self._update_eta()

    def add_packages_found(self, kode: str, count: int) -> None:
        with self._lock:
            self._progress.total_packages += count
            self._progress.current_activity = f"{kode}: {count} paket ditemukan"
            self._update_eta()

    def mark_processed(self, kode: str, id_lelang: str, saved: bool = True, skipped: bool = False) -> None:
        with self._lock:
            self._progress.processed_packages += 1
            if saved:
                self._progress.saved_packages += 1
            if skipped:
                self._progress.skipped_packages += 1
            self._progress.current_activity = f"[{kode}] {id_lelang}"
            self._update_eta()

    def mark_error(self, kode: str, msg: str) -> None:
        with self._lock:
            self._progress.processed_packages += 1
            self._progress.error_packages += 1
            self._progress.errors.append(f"[{kode}] {msg}")
            self._update_eta()

    def finish_instansi(self, kode: str) -> None:
        with self._lock:
            self._progress.completed_instansi += 1
            self._progress.current_activity = f"{kode} selesai"
            self._update_eta()

    def _update_eta(self) -> None:
        p = self._progress
        if p.processed_packages > 0 and p.total_packages > 0:
            elapsed = time.time() - p.started_at
            rate = p.processed_packages / elapsed
            remaining = (p.total_packages - p.processed_packages) / rate if rate > 0 else 0
            p.estimated_remaining = remaining
            p.elapsed_seconds = elapsed

    def get_progress(self) -> dict[str, Any]:
        with self._lock:
            p = self._progress
            if p.status == "Running" and p.started_at > 0:
                p.elapsed_seconds = time.time() - p.started_at
            return p.to_dict()


# Global singleton
_tracker: ProgressTracker | None = None


def get_progress_tracker() -> ProgressTracker:
    global _tracker
    if _tracker is None:
        _tracker = ProgressTracker()
    return _tracker
