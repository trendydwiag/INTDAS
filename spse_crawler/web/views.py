"""Dashboard views and API endpoints for the SPSE Crawler Web UI."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import threading
import traceback
from datetime import datetime
from functools import wraps

from asgiref.sync import sync_to_async
from loguru import logger
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone as dj_timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST

from spse_crawler.config.settings import INSTANSI_CODES, InstansiConfig, get_settings
from spse_crawler.core.exceptions import PackageSkippedError
from spse_crawler.services.progress_tracker import get_progress_tracker
from spse_crawler.services.priority_scorer import score_it_priority
from spse_crawler.services.purger import flush
from spse_crawler.services.tender_participant_store import sync_tender_participants
from spse_crawler.services.tender_winner_store import sync_tender_winner

from .models import CrawlJob, KbliMaster, TenderResult
from .scheduler import get_scheduler, is_scheduler_enabled, start_scheduler, stop_scheduler, trigger_now


# ---------------------------------------------------------------------------
# Superadmin-only decorator for dangerous/operational endpoints
# ---------------------------------------------------------------------------
def require_superadmin(view_func):
    """POST-only + CSRF-protected + superadmin-authorized wrapper.

    Django's CsrfViewMiddleware validates the CSRF token before this
    decorator runs. All frontend callers send X-CSRFToken header.
    """
    @wraps(view_func)
    @require_POST
    def _wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return JsonResponse({"error": "Authentication required"}, status=401)
        if not getattr(user, "is_superadmin", False):
            return JsonResponse({"error": "Forbidden: superadmin required"}, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped


_crawl_lock = threading.Lock()
_crawl_thread: threading.Thread | None = None

# HPS range boundaries (in Rupiah)
HPS_RANGES = [
    {"key": "gt10m", "label": "> 10 Miliar", "min": 10_000_000_000, "max": None},
    {"key": "5m_10m", "label": "5 - 10 Miliar", "min": 5_000_000_000, "max": 9_999_999_999},
    {"key": "2m_5m", "label": "2 - 5 Miliar", "min": 2_000_000_000, "max": 4_999_999_999},
    {"key": "1m_2m", "label": "1 - 2 Miliar", "min": 1_000_000_000, "max": 1_999_999_999},
    {"key": "500j_1m", "label": "500 Jt - 1 Miliar", "min": 500_000_000, "max": 999_999_999},
    {"key": "200j_500j", "label": "200 - 500 Jt", "min": 200_000_000, "max": 499_999_999},
    {"key": "lt200j", "label": "< 200 Juta", "min": 0, "max": 199_999_999},
]

JENIS_PENGADAAN_CHOICES = [
    "Pekerjaan Konstruksi",
    "Jasa Konsultansi",
    "Pengadaan Barang",
    "Jasa Lainnya",
]

KBLI_CHOICES = [
    {"code": "62019", "label": "KBLI 62019 - Pemrograman Komputer Lainnya"},
    {"code": "62090", "label": "KBLI 62090 - TI & Jasa Komputer Lainnya"},
    {"code": "62029", "label": "KBLI 62029 - Konsultasi & Fasilitas Komputer"},
]


def _classify_status(tahap: str, is_prakualifikasi: bool) -> str:
    t = tahap.lower()
    if is_prakualifikasi:
        return "prakualifikasi"
    if any(kw in t for kw in ["selesai", "tender selesai", "pembatalan"]):
        return "selesai"
    if any(kw in t for kw in ["kontrak", "penandatanganan"]):
        return "dikontrak"
    return "aktif"


def _format_hps_short(hps: int) -> str:
    if hps >= 1_000_000_000:
        val = hps / 1_000_000_000
        return f"{val:,.1f} M".replace(",", ".") if val != int(val) else f"{int(val)} M"
    if hps >= 1_000_000:
        val = hps / 1_000_000
        return f"{val:,.1f} Jt".replace(",", ".") if val != int(val) else f"{int(val)} Jt"
    if hps >= 1_000:
        val = hps / 1_000
        return f"{val:,.0f} Rb".replace(",", ".")
    return str(hps)


def _clamp(v, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(v)))


def _build_tahapan_q(tahapan_list: list) -> Q:
    """Build a Q object for multi-select tahapan filter (OR logic)."""
    q = Q()
    for t in tahapan_list:
        t_lower = t.lower()
        if t_lower == "prakualifikasi":
            q |= Q(tahap_saat_ini__icontains="prakualifikasi") | Q(tahap_saat_ini__icontains="kualifikasi")
        elif t_lower == "pengumuman":
            q |= Q(tahap_saat_ini__icontains="pengumuman")
        elif t_lower == "evaluasi":
            q |= Q(tahap_saat_ini__icontains="evaluasi")
        elif t_lower == "penawaran":
            q |= Q(tahap_saat_ini__icontains="penawaran")
        elif t_lower == "lainnya":
            q |= ~(Q(tahap_saat_ini__icontains="prakualifikasi") | Q(tahap_saat_ini__icontains="kualifikasi") | Q(tahap_saat_ini__icontains="pengumuman") | Q(tahap_saat_ini__icontains="evaluasi") | Q(tahap_saat_ini__icontains="penawaran"))
    return q


def _apply_filters(qs, request):
    """Apply sidebar filters (HPS, KBLI, Jenis, Tahapan, Instansi, Search, Deadline, Location) + tab filter.

    Tab filter is mutually exclusive — only ONE of: aktif / prakualifikasi /
    it_priority / (empty=all).
    """
    tab = request.GET.get("tab", "").strip()
    instansi = request.GET.get("instansi", "").strip()
    search = request.GET.get("search", "").strip()
    hps_range = request.GET.get("hps_range", "").strip()
    jenis = request.GET.get("jenis", "").strip()
    deadline_before = request.GET.get("deadline_before", "").strip()
    deadline_after = request.GET.get("deadline_after", "").strip()
    location = request.GET.get("location", "").strip()
    hps_min = request.GET.get("hps_min", "").strip()
    hps_max = request.GET.get("hps_max", "").strip()
    tahapan_list = request.GET.getlist("tahapan")
    if not tahapan_list:
        raw = request.GET.get("tahapan", "").strip()
        if raw:
            tahapan_list = [t.strip() for t in raw.split(",") if t.strip()]

    # --- Tab filter (mutually exclusive) ---
    if tab == "aktif":
        qs = qs.filter(
            ~Q(tahap_saat_ini__icontains="selesai"),
            ~Q(tahap_saat_ini__icontains="kontrak"),
            ~Q(tahap_saat_ini__icontains="pembatalan"),
            is_prakualifikasi=False,
        )
    elif tab == "prakualifikasi":
        qs = qs.filter(is_prakualifikasi=True)
    elif tab == "it_priority":
        qs = qs.filter(is_it_priority=True)
    # else: tab="" or "all" → no tab filter, show everything

    # --- Sidebar filters ---
    if instansi:
        qs = qs.filter(Q(kode_instansi__icontains=instansi) | Q(instansi__icontains=instansi))

    if tahapan_list:
        tahapan_q = _build_tahapan_q(tahapan_list)
        qs = qs.filter(tahapan_q)

    if search:
        qs = qs.filter(
            Q(nama_paket__icontains=search)
            | Q(id_lelang__icontains=search)
            | Q(instansi__icontains=search)
            | Q(satuan_kerja__icontains=search)
        )

    if hps_range:
        for r in HPS_RANGES:
            if r["key"] == hps_range:
                if r["min"] is not None:
                    qs = qs.filter(hps__gte=r["min"])
                if r["max"] is not None:
                    qs = qs.filter(hps__lte=r["max"])
                break

    if jenis:
        qs = qs.filter(jenis_pengadaan__icontains=jenis)

    if location:
        qs = qs.filter(lokasi_pekerjaan__icontains=location)

    kbli_codes = request.GET.getlist("kbli_code")
    if not kbli_codes:
        raw = request.GET.get("kbli_codes", "")
        if raw:
            kbli_codes = [c.strip() for c in raw.split(",") if c.strip()]
    if kbli_codes:
        qs = _apply_kbli_filter(qs, kbli_codes)

    # Deadline and HPS min/max post-filter (JSON field, applied in Python)
    if deadline_before or deadline_after or hps_min or hps_max:
        qs = _apply_deadline_and_hps_custom(qs, deadline_before, deadline_after, hps_min, hps_max)

    return qs


def _apply_sidebar_filters(qs, request):
    """Apply ONLY sidebar filters (HPS, KBLI, Jenis, Instansi, Search, Location, HPS min/max) — NO tab filter.

    Used by api_filter_counts so badge counts are independent of active tab.
    """
    instansi = request.GET.get("instansi", "").strip()
    search = request.GET.get("search", "").strip()
    hps_range = request.GET.get("hps_range", "").strip()
    jenis = request.GET.get("jenis", "").strip()
    location = request.GET.get("location", "").strip()
    hps_min = request.GET.get("hps_min", "").strip()
    hps_max = request.GET.get("hps_max", "").strip()

    if instansi:
        qs = qs.filter(Q(kode_instansi__icontains=instansi) | Q(instansi__icontains=instansi))

    if search:
        qs = qs.filter(
            Q(nama_paket__icontains=search)
            | Q(id_lelang__icontains=search)
            | Q(instansi__icontains=search)
            | Q(satuan_kerja__icontains=search)
        )

    if hps_range:
        for r in HPS_RANGES:
            if r["key"] == hps_range:
                if r["min"] is not None:
                    qs = qs.filter(hps__gte=r["min"])
                if r["max"] is not None:
                    qs = qs.filter(hps__lte=r["max"])
                break

    if jenis:
        qs = qs.filter(jenis_pengadaan__icontains=jenis)

    if location:
        qs = qs.filter(lokasi_pekerjaan__icontains=location)

    kbli_codes = request.GET.getlist("kbli_code")
    if not kbli_codes:
        raw = request.GET.get("kbli_codes", "")
        if raw:
            kbli_codes = [c.strip() for c in raw.split(",") if c.strip()]
    if kbli_codes:
        qs = _apply_kbli_filter(qs, kbli_codes)

    if hps_min or hps_max:
        qs = _apply_deadline_and_hps_custom(qs, "", "", hps_min, hps_max)

    return qs


def _apply_hps_filter(qs, hps_range: str):
    """Apply a single HPS range filter to a queryset."""
    if hps_range:
        for r in HPS_RANGES:
            if r["key"] == hps_range:
                if r["min"] is not None:
                    qs = qs.filter(hps__gte=r["min"])
                if r["max"] is not None:
                    qs = qs.filter(hps__lte=r["max"])
                break
    return qs


def _apply_jenis_filter(qs, jenis: str):
    """Apply a single Jenis filter to a queryset."""
    if jenis:
        qs = qs.filter(jenis_pengadaan__icontains=jenis)
    return qs


def _apply_kbli_filter(qs, kbli_codes):
    """Apply KBLI filter using OR (__in). Accepts str or list."""
    if isinstance(kbli_codes, str):
        kbli_codes = [kbli_codes]
    kbli_codes = [c for c in kbli_codes if c.strip()]
    if kbli_codes:
        qs = qs.filter(kbli_code__in=kbli_codes)
    return qs


def _extract_deadline_from_jadwal(jadwal_json):
    """Extract the submission deadline date from jadwal JSON array.

    Priority: penawaran/submission > kualifikasi > last available date.
    Uses the same logic as opportunity_scorer._extract_submission_deadline.
    """
    from datetime import datetime
    if not jadwal_json:
        return None

    fallback = None
    for item in jadwal_json:
        if not isinstance(item, dict):
            continue
        tahap = (item.get("tahap") or "").lower()
        end_str = item.get("sampai") or item.get("end") or ""
        if not end_str:
            continue
        d = None
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
            try:
                d = datetime.strptime(end_str.strip(), fmt).date()
                break
            except ValueError:
                continue
        if not d:
            continue

        if any(kw in tahap for kw in ["penawaran", "submission", "pengajuan", "ajuan"]):
            return d
        if any(kw in tahap for kw in ["kualifikasi", "pra-kualifikasi", "prakualifikasi"]):
            return d
        if fallback is None or d > fallback:
            fallback = d

    return fallback


def _apply_deadline_and_hps_custom(qs, deadline_before, deadline_after, hps_min, hps_max):
    """Post-filter queryset by deadline date range and custom HPS min/max.

    deadline is inside jadwal_json (JSONField), so we filter in Python.
    hps_min/hps_max are applied directly on the ORM.

    Input validation:
      - hps_min/hps_max must be non-negative integers
      - deadline dates must be valid YYYY-MM-DD
      - hps_min > hps_max is allowed (returns empty)
    """
    # Validate and apply HPS min/max
    if hps_min:
        try:
            hps_min_val = int(hps_min)
            if hps_min_val < 0:
                hps_min_val = 0
            qs = qs.filter(hps__gte=hps_min_val)
        except (ValueError, TypeError):
            pass
    if hps_max:
        try:
            hps_max_val = int(hps_max)
            if hps_max_val < 0:
                hps_max_val = 0
            qs = qs.filter(hps__lte=hps_max_val)
        except (ValueError, TypeError):
            pass

    # Validate and apply deadline filter
    if deadline_before or deadline_after:
        cutoff_before = None
        cutoff_after = None
        if deadline_before:
            try:
                cutoff_before = datetime.strptime(deadline_before, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
        if deadline_after:
            try:
                cutoff_after = datetime.strptime(deadline_after, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass

        if cutoff_before or cutoff_after:
            # Filter in Python since deadline is inside JSON
            ids_to_keep = []
            for t in qs.only("id", "jadwal_json").iterator():
                deadline = _extract_deadline_from_jadwal(t.jadwal_json)
                if deadline is None:
                    continue
                if cutoff_before and deadline > cutoff_before:
                    continue
                if cutoff_after and deadline < cutoff_after:
                    continue
                ids_to_keep.append(t.id)
            qs = qs.filter(id__in=ids_to_keep)

    return qs


def _get_global_queryset(request):
    """Build a base queryset with ONLY global filters (tab + search + instansi + tahapan).

    This excludes all sidebar facet filters (HPS, Jenis, KBLI) so each
    facet group can compute its own counts independently.
    """
    tab = request.GET.get("tab", "").strip()
    instansi = request.GET.get("instansi", "").strip()
    search = request.GET.get("search", "").strip()
    tahapan_list = request.GET.getlist("tahapan")
    if not tahapan_list:
        raw = request.GET.get("tahapan", "").strip()
        if raw:
            tahapan_list = [t.strip() for t in raw.split(",") if t.strip()]

    qs = TenderResult.objects.all()

    # Tab filter (mutually exclusive)
    if tab == "aktif":
        qs = qs.filter(
            ~Q(tahap_saat_ini__icontains="selesai"),
            ~Q(tahap_saat_ini__icontains="kontrak"),
            ~Q(tahap_saat_ini__icontains="pembatalan"),
            is_prakualifikasi=False,
        )
    elif tab == "prakualifikasi":
        qs = qs.filter(is_prakualifikasi=True)
    elif tab == "it_priority":
        qs = qs.filter(is_it_priority=True)

    if instansi:
        qs = qs.filter(Q(kode_instansi__icontains=instansi) | Q(instansi__icontains=instansi))

    if tahapan_list:
        tahapan_q = _build_tahapan_q(tahapan_list)
        qs = qs.filter(tahapan_q)

    if search:
        qs = qs.filter(
            Q(nama_paket__icontains=search)
            | Q(id_lelang__icontains=search)
            | Q(instansi__icontains=search)
            | Q(satuan_kerja__icontains=search)
        )

    return qs


def dashboard(request):
    scheduler_running = is_scheduler_enabled()
    kbli_items = list(KbliMaster.objects.filter(is_active=True).values_list("code", "name"))
    return render(request, "dashboard.html", {
        "scheduler_running": scheduler_running,
        "kbli_items": kbli_items,
    })


def _safe_int(val: str | None, default: int, lo: int = 1, hi: int = 9999) -> int:
    """Safely parse an integer from a query param with bounds."""
    try:
        return min(hi, max(lo, int(val)))
    except (TypeError, ValueError):
        return default


@require_GET
def api_results(request):
    qs = TenderResult.objects.all()
    qs = _apply_filters(qs, request)
    total_count = qs.count()

    page = _safe_int(request.GET.get("page"), 1)
    page_size = _safe_int(request.GET.get("page_size"), 25, 1, 100)
    start = (page - 1) * page_size
    end = start + page_size

    qs = qs.order_by("-is_it_priority", "-priority_score", "-scraped_at")[start:end]

    results = list(qs.values(
        "id", "kode_instansi", "id_lelang", "nama_paket", "instansi",
        "hps", "jenis_pengadaan", "tahap_saat_ini", "is_prakualifikasi",
        "kbli_code", "is_it_priority", "priority_score",
        "url_pengumuman", "tanggal_dibuat", "scraped_at",
        "ai_score",
    ))

    for r in results:
        display_date = r["tanggal_dibuat"] or r["scraped_at"]
        r["tanggal_dibuat"] = display_date.strftime("%d %b %Y %H:%M") if display_date else ""
        r["scraped_at"] = r["scraped_at"].strftime("%d %b %Y %H:%M") if r["scraped_at"] else ""
        r["hps_short"] = _format_hps_short(r["hps"])
        r["status_tender"] = _classify_status(r["tahap_saat_ini"], r["is_prakualifikasi"])

    return JsonResponse({
        "results": results,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total_count // page_size)),
    })


def _serialize_tender_detail(t: TenderResult) -> dict:
    display_date = t.tanggal_dibuat or t.scraped_at
    return {
        "id": t.id,
        "kode_instansi": t.kode_instansi,
        "id_lelang": t.id_lelang,
        "nama_paket": t.nama_paket,
        "instansi": t.instansi,
        "hps": t.hps,
        "hps_short": _format_hps_short(t.hps),
        "jenis_pengadaan": t.jenis_pengadaan,
        "metode_pengadaan": t.metode_pengadaan,
        "tahap_saat_ini": t.tahap_saat_ini,
        "is_prakualifikasi": t.is_prakualifikasi,
        "status_tender": _classify_status(t.tahap_saat_ini, t.is_prakualifikasi),
        "kbli_code": t.kbli_code,
        "kbli_description": t.kbli_description,
        "is_it_priority": t.is_it_priority,
        "priority_score": t.priority_score,
        "url_pengumuman": t.url_pengumuman,
        "lokasi_pekerjaan": t.lokasi_pekerjaan,
        "tahun_anggaran": t.tahun_anggaran,
        "satuan_kerja_detail": t.satuan_kerja_detail,
        "jadwal_json": t.jadwal_json or [],
        "syarat_kualifikasi": t.syarat_kualifikasi,
        "peserta_count": t.peserta_count,
        "tanggal_dibuat": display_date.strftime("%d %b %Y %H:%M") if display_date else "",
        "scraped_at": t.scraped_at.strftime("%d %b %Y %H:%M") if t.scraped_at else "",
        # AI persistence fields
        "ai_score": t.ai_score,
        "ai_analysis_json": t.ai_analysis_json or {},
    }


@require_GET
def api_tender_detail(request, pk):
    """Return full detail for a single tender by Django PK."""
    try:
        t = TenderResult.objects.get(pk=pk)
    except TenderResult.DoesNotExist:
        return JsonResponse({"error": "Tender tidak ditemukan"}, status=404)

    return JsonResponse(_serialize_tender_detail(t))


async def _execute_single_resync(tender_id: int) -> tuple[bool, str, TenderResult]:
    from spse_crawler.core.browser import SpseHttpClient
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.models.tender import TenderPackage
    from spse_crawler.config import get_settings

    t = await sync_to_async(TenderResult.objects.get)(id=tender_id)
    pkg = TenderPackage(
        kode_instansi=t.kode_instansi,
        id_lelang=t.id_lelang,
        nama_paket=t.nama_paket,
        instansi=t.instansi,
        hps=t.hps,
        jenis_pengadaan=t.jenis_pengadaan,
        tahap_saat_ini=t.tahap_saat_ini,
    )
    settings = get_settings()
    async with SpseHttpClient(settings) as http:
        detail_parser = DetailParser(http, settings=settings)
        detail = await detail_parser.scrape_detail(pkg, bypass_tahap_gate=True)
        if detail is None:
            return False, "Tidak dapat mengambil data dari LPSE", t

        if detail.tahap_saat_ini:
            t.tahap_saat_ini = detail.tahap_saat_ini
            t.is_prakualifikasi = detail_parser.is_eligible_tahap(detail.tahap_saat_ini)
        if detail.satuan_kerja_detail:
            t.satuan_kerja_detail = detail.satuan_kerja_detail
        if detail.syarat_kualifikasi:
            t.syarat_kualifikasi = detail.syarat_kualifikasi
        if detail.jadwal_json:
            t.jadwal_json = detail.jadwal_json
        if detail.peserta_count is not None:
            t.peserta_count = detail.peserta_count
        if detail.lokasi_pekerjaan:
            t.lokasi_pekerjaan = detail.lokasi_pekerjaan
        if detail.metode_pengadaan:
            t.metode_pengadaan = detail.metode_pengadaan
        if detail.tahun_anggaran:
            t.tahun_anggaran = detail.tahun_anggaran
        if detail.requirement_text:
            t.requirement_text = detail.requirement_text
        if detail.kbli_code:
            t.kbli_code = detail.kbli_code
        if detail.kbli_description:
            t.kbli_description = detail.kbli_description
        t.scraped_at = dj_timezone.now()
        try:
            await sync_to_async(t.save)()
        except Exception as save_err:
            if "500" in str(save_err) or "too long" in str(save_err).lower():
                t.lokasi_pekerjaan = (t.lokasi_pekerjaan or "")[:490]
                await sync_to_async(t.save)()
            else:
                raise

        if detail.participants:
            await sync_to_async(sync_tender_participants)(
                t,
                detail.participants,
                source_url=detail.url_pengumuman,
                source_fetched_at=detail.scraped_at,
            )
        if detail.winner:
            await sync_to_async(sync_tender_winner)(
                t,
                detail.winner,
                source_url=detail.winner_source_url,
                source_fetched_at=detail.scraped_at,
            )
    return True, "Data tender berhasil disinkronkan dari LPSE", t


def _run_coroutine_sync(coro):
    """Run an async coroutine synchronously, safely handling existing or absent event loops."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(coro))
        return future.result(timeout=120)


@require_POST
def api_tender_resync(request, pk):
    """Re-scrape detail, participants, and winner for a single tender from LPSE."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Autentikasi diperlukan"}, status=401)

    try:
        t = TenderResult.objects.get(pk=pk)
    except TenderResult.DoesNotExist:
        return JsonResponse({"error": "Tender tidak ditemukan"}, status=404)

    try:
        success, message, updated_tender = _run_coroutine_sync(_execute_single_resync(t.id))
    except Exception as exc:
        logger.error("[RESYNC_TENDER] Error resyncing tender pk={}: {}", pk, exc)
        return JsonResponse({"error": f"Gagal menyinkronkan data tender: {exc}"}, status=500)

    if not success:
        return JsonResponse({"error": message}, status=502)

    return JsonResponse({
        "status": "success",
        "message": message,
        "tender": _serialize_tender_detail(updated_tender),
    })


@require_GET
def api_filter_counts(request):
    """Return badge counts using faceted search (cross-filtering).

    Each filter group's counters are computed by applying all filters
    EXCEPT the filters from that same group, so selecting an option
    in one group never zeroes out the other options in the same group.

    Example: User selects HPS "200-500 Jt"
      → HPS counters: apply Jenis + KBLI + Search, but NOT HPS
      → Jenis counters: apply HPS + KBLI + Search, but NOT Jenis
      → KBLI counters: apply HPS + Jenis + Search, but NOT KBLI
    """
    # Read active sidebar filters from request
    active_hps = request.GET.get("hps_range", "").strip()
    active_jenis = request.GET.get("jenis", "").strip()

    # Multi-select KBLI (OR)
    active_kbli_list = request.GET.getlist("kbli_code")
    if not active_kbli_list:
        raw = request.GET.get("kbli_codes", "")
        if raw:
            active_kbli_list = [c.strip() for c in raw.split(",") if c.strip()]

    # Global base: tab + search + instansi (no sidebar filters)
    global_qs = _get_global_queryset(request)

    # ── HPS counters (exclude active HPS, apply Jenis + KBLI) ──
    hps_base = _apply_jenis_filter(global_qs, active_jenis)
    hps_base = _apply_kbli_filter(hps_base, active_kbli_list)
    hps_counts = {}
    for r in HPS_RANGES:
        qs = hps_base
        if r["min"] is not None:
            qs = qs.filter(hps__gte=r["min"])
        if r["max"] is not None:
            qs = qs.filter(hps__lte=r["max"])
        hps_counts[r["key"]] = qs.count()

    # ── Jenis counters (exclude active Jenis, apply HPS + KBLI) ──
    jenis_base = _apply_hps_filter(global_qs, active_hps)
    jenis_base = _apply_kbli_filter(jenis_base, active_kbli_list)
    jenis_counts = {}
    for j in JENIS_PENGADAAN_CHOICES:
        jenis_counts[j] = _apply_jenis_filter(jenis_base, j).count()

    # ── KBLI counters (exclude active KBLI, apply HPS + Jenis) ──
    kbli_base = _apply_hps_filter(global_qs, active_hps)
    kbli_base = _apply_jenis_filter(kbli_base, active_jenis)
    kbli_master = list(KbliMaster.objects.filter(is_active=True).values_list("code", "name"))
    kbli_counts = {}
    for code, name in kbli_master:
        kbli_counts[code] = {"label": f"{code} — {name[:40]}", "count": _apply_kbli_filter(kbli_base, code).count()}

    # ── Status / Tab counters (independent of sidebar) ──
    status_counts = {
        "aktif": global_qs.exclude(
            Q(tahap_saat_ini__icontains="selesai")
            | Q(tahap_saat_ini__icontains="kontrak")
            | Q(is_prakualifikasi=True)
        ).count(),
        "prakualifikasi": global_qs.filter(is_prakualifikasi=True).count(),
        "all": global_qs.count(),
    }

    # ── Tahapan counters ──
    tahapan_counts = {
        "Pra-Kualifikasi": global_qs.filter(
            Q(tahap_saat_ini__icontains="prakualifikasi") | Q(tahap_saat_ini__icontains="kualifikasi")
        ).count(),
        "Pengumuman": global_qs.filter(tahap_saat_ini__icontains="pengumuman").count(),
        "Evaluasi": global_qs.filter(tahap_saat_ini__icontains="evaluasi").count(),
        "Penawaran": global_qs.filter(tahap_saat_ini__icontains="penawaran").count(),
        "Lainnya": global_qs.exclude(
            Q(tahap_saat_ini__icontains="prakualifikasi")
            | Q(tahap_saat_ini__icontains="kualifikasi")
            | Q(tahap_saat_ini__icontains="pengumuman")
            | Q(tahap_saat_ini__icontains="evaluasi")
            | Q(tahap_saat_ini__icontains="penawaran")
        ).count(),
    }

    it_priority_count = global_qs.filter(is_it_priority=True).count()

    return JsonResponse({
        "hps": hps_counts,
        "jenis": jenis_counts,
        "kbli": kbli_counts,
        "status": status_counts,
        "tahapan": tahapan_counts,
        "it_priority_count": it_priority_count,
        "total": global_qs.count(),
    })


# ---------------------------------------------------------------------------
# CRAWL CONTROL
# ---------------------------------------------------------------------------

@require_superadmin
def api_start_crawl(request):
    """Start a crawl job in the background."""
    global _crawl_thread

    if _crawl_lock.locked():
        return JsonResponse({"error": "Crawl already in progress"}, status=409)

    instansi_input = request.POST.get("instansi", "all").strip()
    workers = _safe_int(request.POST.get("workers"), 3, 1, 20)

    # Pre-validate instansi codes
    if instansi_input.lower() == "all":
        instansi_list = list(INSTANSI_CODES)
    else:
        codes = [c.strip() for c in instansi_input.split(",") if c.strip()]
        instansi_list = [c for c in codes if c in INSTANSI_CODES]
        invalid = [c for c in codes if c not in INSTANSI_CODES]
        if invalid:
            return JsonResponse({
                "error": f"Kode instansi tidak valid: {', '.join(invalid)}. "
                         f"Gunakan kode yang ada di SPSE (contoh: jakarta, kemendagri)."
            }, status=400)

    if not instansi_list:
        return JsonResponse({"error": "Tidak ada instansi valid untuk di-crawl"}, status=400)

    logger.info(
        "[CRAWL] Starting crawl for {} instansi (workers={})",
        len(instansi_list), workers,
    )

    _crawl_thread = threading.Thread(
        target=_run_crawl_in_thread,
        args=(instansi_input, workers, instansi_list),
        daemon=True,
        name="spse-crawl-worker",
    )
    _crawl_thread.start()

    return JsonResponse({
        "status": "started",
        "instansi": instansi_input,
        "instansi_count": len(instansi_list),
        "workers": workers,
    })


@require_superadmin
def api_crawl_delta(request):
    """Re-crawl minor/delta tender records with incomplete details in the database."""
    global _crawl_thread

    if _crawl_lock.locked():
        return JsonResponse({"error": "Proses crawling atau sinkronisasi delta sedang berjalan"}, status=409)

    instansi_input = request.POST.get("instansi", "all").strip()
    workers = _safe_int(request.POST.get("workers"), 3, 1, 10)

    # Filter delta tenders: missing satker detail, empty syarat kualifikasi, empty jadwal, or 0 participants
    delta_filter = (
        Q(satuan_kerja_detail="")
        | Q(syarat_kualifikasi="")
        | Q(jadwal_json=[])
        | Q(jadwal_json__isnull=True)
        | Q(peserta_count=0)
    )

    qs = TenderResult.objects.filter(delta_filter)

    if instansi_input and instansi_input.lower() != "all":
        codes = [c.strip() for c in instansi_input.split(",") if c.strip()]
        valid_codes = [c for c in codes if c in INSTANSI_CODES]
        if not valid_codes:
            return JsonResponse({
                "error": f"Kode instansi tidak valid: {instansi_input}. Gunakan kode SPSE yang valid."
            }, status=400)
        qs = qs.filter(kode_instansi__in=valid_codes)

    total_delta = qs.count()
    if total_delta == 0:
        return JsonResponse({
            "status": "no_delta",
            "message": "Tidak ada data delta yang perlu diperkaya. Seluruh data tender sudah lengkap.",
            "delta_count": 0,
        })

    tender_ids = list(qs.order_by("-tanggal_dibuat", "-id").values_list("id", flat=True))

    logger.info(
        "[DELTA_CRAWL] Starting delta enrichment for {} tenders (workers={})",
        len(tender_ids),
        workers,
    )

    _crawl_thread = threading.Thread(
        target=_run_delta_crawl_in_thread,
        args=(tender_ids, workers, instansi_input),
        daemon=True,
        name="spse-delta-crawl-worker",
    )
    _crawl_thread.start()

    return JsonResponse({
        "status": "started",
        "message": f"Sinkronisasi data delta dimulai untuk {len(tender_ids)} paket tender.",
        "delta_count": len(tender_ids),
        "workers": workers,
    })


def _run_delta_crawl_in_thread(tender_ids: list[int], workers: int, instansi_input: str) -> None:
    """Run delta enrichment in a dedicated background thread with its own event loop."""
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web_ui.settings")
    django.setup()

    if not _crawl_lock.acquire(blocking=False):
        logger.warning("[DELTA_CRAWL] Lock already held — aborting")
        return
    try:
        logger.info("[DELTA_CRAWL] Thread started — creating event loop")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_execute_delta_crawl(tender_ids, workers, instansi_input))
        logger.info("[DELTA_CRAWL] Event loop completed successfully")
    except Exception as exc:
        logger.error("[DELTA_CRAWL] Thread failed with exception: {}", exc)
        logger.error("[DELTA_CRAWL] Traceback:\n{}", traceback.format_exc())
    finally:
        _crawl_lock.release()
        loop.close()
        logger.info("[DELTA_CRAWL] Thread finished — lock released")


async def _execute_delta_crawl(tender_ids: list[int], workers: int, instansi_input: str) -> None:
    """Coroutine to re-scrape detail and sync participants for delta tenders."""
    from spse_crawler.core.browser import SpseHttpClient
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.models.tender import TenderPackage
    from spse_crawler.config import get_settings

    progress = get_progress_tracker()
    _create_job = sync_to_async(CrawlJob.objects.create)
    _save_obj = sync_to_async(lambda o: o.save())
    _sync_participants = sync_to_async(sync_tender_participants)
    _sync_winner = sync_to_async(sync_tender_winner)

    progress.start(total_instansi=1, total_packages=len(tender_ids))
    progress.set_instansi("delta_sync", 0)

    job = await _create_job(
        instansi_input=f"delta:{instansi_input[:90]}",
        workers=workers,
        total_packages=len(tender_ids),
        status="running",
    )

    settings = get_settings()
    saved = 0
    errors = 0

    try:
        async with SpseHttpClient(settings) as http:
            detail_parser = DetailParser(http, settings=settings)
            semaphore = asyncio.Semaphore(workers)

            async def _process_one_tender(tid: int):
                nonlocal saved, errors
                async with semaphore:
                    try:
                        t = await sync_to_async(TenderResult.objects.get)(id=tid)
                        pkg = TenderPackage(
                            kode_instansi=t.kode_instansi,
                            id_lelang=t.id_lelang,
                            nama_paket=t.nama_paket,
                            instansi=t.instansi,
                            hps=t.hps,
                            jenis_pengadaan=t.jenis_pengadaan,
                            tahap_saat_ini=t.tahap_saat_ini,
                        )
                        detail = await detail_parser.scrape_detail(pkg)
                        if detail is not None:
                            if detail.satuan_kerja_detail:
                                t.satuan_kerja_detail = detail.satuan_kerja_detail
                            if detail.syarat_kualifikasi:
                                t.syarat_kualifikasi = detail.syarat_kualifikasi
                            if detail.jadwal_json:
                                t.jadwal_json = detail.jadwal_json
                            if detail.peserta_count:
                                t.peserta_count = detail.peserta_count
                            if detail.lokasi_pekerjaan:
                                t.lokasi_pekerjaan = detail.lokasi_pekerjaan
                            if detail.metode_pengadaan:
                                t.metode_pengadaan = detail.metode_pengadaan
                            if detail.tahun_anggaran:
                                t.tahun_anggaran = detail.tahun_anggaran
                            if detail.requirement_text:
                                t.requirement_text = detail.requirement_text
                            if detail.kbli_code:
                                t.kbli_code = detail.kbli_code
                            if detail.kbli_description:
                                t.kbli_description = detail.kbli_description
                            try:
                                await _save_obj(t)
                            except Exception as save_err:
                                if "500" in str(save_err) or "too long" in str(save_err).lower():
                                    t.lokasi_pekerjaan = (t.lokasi_pekerjaan or "")[:490]
                                    await _save_obj(t)
                                else:
                                    raise

                            if detail.participants:
                                await _sync_participants(
                                    t,
                                    detail.participants,
                                    source_url=detail.url_pengumuman,
                                    source_fetched_at=detail.scraped_at,
                                )
                            if detail.winner:
                                await _sync_winner(
                                    t,
                                    detail.winner,
                                    source_url=detail.winner_source_url,
                                    source_fetched_at=detail.scraped_at,
                                )
                        saved += 1
                        progress.mark_processed("delta_sync", t.id_lelang, saved=True)
                    except Exception as exc:
                        errors += 1
                        progress.mark_error("delta_sync", str(exc))
                        logger.warning("[DELTA_CRAWL] Failed to enrich tender id={}: {}", tid, exc)

            tasks = [_process_one_tender(tid) for tid in tender_ids]
            await asyncio.gather(*tasks, return_exceptions=True)

        progress.finish(success=(errors == 0 or saved > 0))

        job.status = "completed" if (errors == 0 or saved > 0) else "failed"
        job.total_details = saved
        job.finished_at = dj_timezone.now()
        if errors > 0:
            job.error_message = f"{errors} error(s) during delta crawl"
        await _save_obj(job)
        logger.info("[DELTA_CRAWL] Completed — {} saved, {} errors", saved, errors)

    except Exception as exc:
        job.status = "failed"
        job.error_message = f"{exc}\n{traceback.format_exc()}"
        job.finished_at = dj_timezone.now()
        await _save_obj(job)
        progress.finish(success=False, error_msg=str(exc))
        logger.error("[DELTA_CRAWL] Job #{} failed: {}", job.id, exc)


def _run_crawl_in_thread(instansi_input: str, workers: int, instansi_list: list[str]) -> None:
    """Run crawl in a dedicated background thread with its own event loop."""
    # Ensure Django ORM is initialized for this thread
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web_ui.settings")
    django.setup()

    if not _crawl_lock.acquire(blocking=False):
        logger.warning("[CRAWL] Lock already held — aborting")
        return
    try:
        logger.info("[CRAWL] Thread started — creating event loop")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_execute_crawl(instansi_input, workers, instansi_list))
        logger.info("[CRAWL] Event loop completed successfully")
    except Exception as exc:
        logger.error("[CRAWL] Thread failed with exception: {}", exc)
        logger.error("[CRAWL] Traceback:\n{}", traceback.format_exc())
    finally:
        _crawl_lock.release()
        loop.close()
        logger.info("[CRAWL] Thread finished — lock released")


async def _execute_crawl(instansi_input: str, workers: int, instansi_list: list[str]) -> None:
    """Core crawl coroutine — runs all instansi with semaphore."""
    from spse_crawler.core.browser import SpseHttpClient
    from spse_crawler.parsers.detail import DetailParser
    from spse_crawler.parsers.discovery import DiscoveryParser

    settings = get_settings()
    total = 0
    errors = 0
    progress = get_progress_tracker()

    _create_job = sync_to_async(CrawlJob.objects.create)
    _save_obj = sync_to_async(lambda o: o.save())
    _update_or_create = sync_to_async(TenderResult.objects.update_or_create)
    _sync_participants = sync_to_async(sync_tender_participants)
    _sync_winner = sync_to_async(sync_tender_winner)

    async def _enqueue_intelligence():
        from spse_crawler.services.intelligence_pipeline import (
            enqueue_new_after_crawl,
        )
        return enqueue_new_after_crawl()

    progress.start(total_instansi=len(instansi_list))

    job = await _create_job(
        instansi_input=instansi_input,
        workers=workers,
        status="running",
    )
    logger.info("[CRAWL] CrawlJob #{} created (instansi={})", job.id, instansi_input)

    try:
        sem = asyncio.Semaphore(workers)

        async with SpseHttpClient(settings) as http:
            discovery = DiscoveryParser(http, settings)
            detail_parser = DetailParser(http, settings=settings)

            async def _process_one(kode: str, idx: int) -> tuple[int, int]:
                """Returns (saved_count, skipped_count)."""
                nonlocal errors
                async with sem:
                    saved = 0
                    skipped = 0
                    progress.set_instansi(kode, idx)
                    try:
                        logger.info("[{}] Starting crawl...", kode)
                        instansi_config = InstansiConfig(kode=kode)

                        # Stage 1: Discovery (already filters non-eligible status)
                        packages = await discovery.fetch_packages(instansi_config)
                        logger.info("[{}] Discovery: {} eligible packages", kode, len(packages))
                        progress.add_packages_found(kode, len(packages))

                        for pkg in packages:
                            detail = None
                            try:
                                # Stage 2: Detail + tahap eligibility check
                                detail = await detail_parser.scrape_detail(pkg)
                                tahap = detail.tahap_saat_ini
                                is_prakualifikasi = detail.is_prakualifikasi
                                url_pengumuman = detail.url_pengumuman
                                kbli_code = detail.kbli_code
                                kbli_description = detail.kbli_description
                                requirement_text = detail.requirement_text

                            except PackageSkippedError as exc:
                                # Stage 2 rejected the package (non-eligible tahap)
                                skipped += 1
                                progress.mark_processed(kode, pkg.id_lelang, saved=False, skipped=True)
                                logger.debug("[{}/{}] {}", kode, pkg.id_lelang, exc)
                                continue

                            except Exception as exc:
                                # Cloudflare / network error — save Stage 1 data only
                                # Stage 1 already filtered, so this package is eligible
                                logger.debug("[{}/{}] Detail failed ({}), saving Stage 1", kode, pkg.id_lelang, exc)
                                tahap = pkg.status
                                is_prakualifikasi = "prakualifikasi" in pkg.status.lower()
                                url_pengumuman = pkg.url_pengumuman
                                kbli_code = ""
                                kbli_description = ""
                                requirement_text = ""

                            # IT priority scoring
                            pscore = score_it_priority(
                                kbli_code=kbli_code,
                                nama_paket=pkg.nama_paket,
                                jenis_pengadaan=pkg.jenis_pengadaan,
                            )

                            # Stage 3: Save to DB
                            tender_obj, _ = await _update_or_create(
                                kode_instansi=pkg.kode_instansi,
                                id_lelang=pkg.id_lelang,
                                defaults={
                                    "nama_paket": pkg.nama_paket,
                                    "instansi": pkg.instansi,
                                    "hps": pkg.nilai_pagu,
                                    "jenis_pengadaan": pkg.jenis_pengadaan,
                                    "tahap_saat_ini": tahap,
                                    "is_prakualifikasi": is_prakualifikasi,
                                    "kbli_code": kbli_code,
                                    "kbli_description": kbli_description,
                                    "is_it_priority": pscore.is_it_priority,
                                    "priority_score": pscore.priority_score,
                                    "url_pengumuman": url_pengumuman,
                                    "requirement_text": requirement_text,
                                    # New enrichment fields
                                    "jadwal_json": getattr(detail, "jadwal_json", []) if detail else [],
                                    "syarat_kualifikasi": getattr(detail, "syarat_kualifikasi", "") if detail else "",
                                    "peserta_count": getattr(detail, "peserta_count", 0) if detail else 0,
                                    "lokasi_pekerjaan": getattr(detail, "lokasi_pekerjaan", "") if detail else "",
                                    "metode_pengadaan": getattr(detail, "metode_pengadaan", "") if detail else "",
                                    "tahun_anggaran": getattr(detail, "tahun_anggaran", "") if detail else "",
                                    "satuan_kerja_detail": getattr(detail, "satuan_kerja_detail", "") if detail else "",
                                },
                            )
                            # Enrich participants only when a detail page was
                            # successfully parsed (Stage 1 fallback has none).
                            if detail is not None and detail.participants:
                                await _sync_participants(
                                    tender_obj,
                                    detail.participants,
                                    source_url=detail.url_pengumuman,
                                    source_fetched_at=detail.scraped_at,
                                )
                            # Persist the awarded winner (Phase 6B) when present —
                            # only retained awarded tenders fetch a winner page,
                            # and a missing winner never destroys history.
                            if detail is not None and detail.winner:
                                await _sync_winner(
                                    tender_obj,
                                    detail.winner,
                                    source_url=detail.winner_source_url,
                                    source_fetched_at=detail.scraped_at,
                                )
                            saved += 1
                            progress.mark_processed(kode, pkg.id_lelang, saved=True)

                        progress.finish_instansi(kode)
                        logger.info(
                            "[{}] Done — {} saved, {} skipped",
                            kode, saved, skipped,
                        )
                        return saved, skipped

                    except Exception as exc:
                        errors += 1
                        progress.mark_error(kode, str(exc))
                        logger.error("[{}] Crawl failed: {}", kode, exc)
                        logger.error("[{}] Traceback:\n{}", kode, traceback.format_exc())
                        return 0, 0

            tasks = [_process_one(k, i) for i, k in enumerate(instansi_list)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            total_skipped = 0
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    errors += 1
                    logger.error("[{}] Task raised: {}", instansi_list[i], r)
                elif isinstance(r, tuple):
                    total += r[0]
                    total_skipped += r[1]

        # Cleanup cached Playwright sessions
        if detail_parser is not None:
            await detail_parser.close()

        job.status = "completed"
        job.total_details = total
        job.total_packages = total
        job.finished_at = dj_timezone.now()
        await _save_obj(job)
        progress.finish(success=True)
        try:
            await _enqueue_intelligence()
        except Exception as exc:
            logger.warning("[CRAWL] Intelligence enqueue after crawl: {}", exc)
        logger.info(
            "[CRAWL] Job #{} completed — {} saved, {} skipped, {} errors",
            job.id, total, total_skipped, errors,
        )

    except Exception as exc:
        job.status = "failed"
        job.error_message = f"{exc}\n{traceback.format_exc()}"
        job.finished_at = dj_timezone.now()
        await _save_obj(job)
        progress.finish(success=False, error_msg=str(exc))
        logger.error("[CRAWL] Job #{} failed: {}", job.id, exc)


@require_GET
def api_status(request):
    scheduler = get_scheduler()
    progress = get_progress_tracker()

    # Reconcile stale running jobs if lock is not held
    if not _crawl_lock.locked():
        stale_jobs = CrawlJob.objects.filter(status="running")
        if stale_jobs.exists():
            stale_jobs.update(
                status="failed",
                error_message="Proses crawler terhenti atau server di-restart",
                finished_at=dj_timezone.now(),
            )
            if progress.get_progress().get("status") == "Running":
                progress.finish(success=False, error_msg="Proses terhenti")

    jobs = list(CrawlJob.objects.order_by("-started_at")[:5].values(
        "id", "instansi_input", "status", "total_details",
        "error_message", "started_at", "finished_at",
    ))
    for j in jobs:
        j["started_at"] = j["started_at"].isoformat() if j["started_at"] else ""
        j["finished_at"] = j["finished_at"].isoformat() if j["finished_at"] else ""

    crawl_running = _crawl_lock.locked() or CrawlJob.objects.filter(status="running").exists()

    return JsonResponse({
        "scheduler_running": is_scheduler_enabled(),
        "crawl_in_progress": crawl_running,
        "progress": progress.get_progress(),
        "recent_jobs": jobs,
    })


@require_GET
def api_crawl_progress(request):
    """Return live crawl progress state for frontend polling."""
    progress = get_progress_tracker()
    return JsonResponse(progress.get_progress())


@require_superadmin
def api_toggle_scheduler(request):
    if is_scheduler_enabled():
        stop_scheduler()
        return JsonResponse({"scheduler_running": False})
    else:
        start_scheduler()
        return JsonResponse({"scheduler_running": True})


@require_superadmin
def api_trigger_crawl(request):
    trigger_now()
    return JsonResponse({"status": "triggered"})


@require_GET
def api_download_csv(request):
    qs = TenderResult.objects.all()
    qs = _apply_filters(qs, request)

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="tender_{datetime.now():%Y%m%d}.csv"'

    writer = csv.writer(response)
    writer.writerow(["Kode Instansi", "ID Lelang", "Nama Paket", "Instansi", "Satuan Kerja",
                      "HPS", "Jenis Pengadaan", "Tahap", "Prakualifikasi", "Prioritas IT",
                      "Skor IT", "KBLI", "URL", "Scraped At"])

    for r in qs[:10000]:
        writer.writerow([
            r.kode_instansi, r.id_lelang, r.nama_paket, r.instansi, r.satuan_kerja,
            r.hps, r.jenis_pengadaan, r.tahap_saat_ini,
            "Ya" if r.is_prakualifikasi else "Tidak",
            "Ya" if r.is_it_priority else "Tidak",
            r.priority_score,
            r.kbli_code or "",
            r.url_pengumuman, r.scraped_at.strftime("%Y-%m-%d %H:%M") if r.scraped_at else "",
        ])
    return response


@require_GET
def api_download_excel(request):
    import pandas as pd

    qs = TenderResult.objects.all()
    qs = _apply_filters(qs, request)

    data = list(qs[:10000].values(
        "kode_instansi", "id_lelang", "nama_paket", "instansi", "satuan_kerja",
        "hps", "jenis_pengadaan", "tahap_saat_ini", "is_prakualifikasi",
        "is_it_priority", "priority_score",
        "url_pengumuman", "scraped_at",
    ))

    df = pd.DataFrame(data)
    if not df.empty:
        df.columns = ["Kode Instansi", "ID Lelang", "Nama Paket", "Instansi", "Satuan Kerja",
                       "HPS", "Jenis Pengadaan", "Tahap", "Prakualifikasi",
                       "Prioritas IT", "Skor IT",
                       "URL", "Scraped At"]
        df["Prakualifikasi"] = df["Prakualifikasi"].map({True: "Ya", False: "Tidak"})
        df["Prioritas IT"] = df["Prioritas IT"].map({True: "Ya", False: "Tidak"})

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Tender")
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="tender_{datetime.now():%Y%m%d}.xlsx"'
    return response


# ---------------------------------------------------------------------------
# FLUSH / PURGE RECORDS
# ---------------------------------------------------------------------------

@require_superadmin
def api_flush_records(request):
    """Delete expired / inactive tender records.

    POST params:
      mode = "non_prakualifikasi"  (default) — delete non-prakualifikasi records
      mode = "inactive"            — delete Selesai / Dikontrak / Batal / Gagal
      mode = "all"                 — wipe entire database
    """
    mode = request.POST.get("mode", "non_prakualifikasi").strip()

    if mode not in ("non_prakualifikasi", "inactive", "all"):
        return JsonResponse(
            {"status": "error", "message": f"Mode tidak valid: {mode!r}"},
            status=400,
        )

    try:
        result = flush(mode)
    except Exception as exc:
        logger.error("[FLUSH] Error during flush (mode={}): {}", mode, exc)
        return JsonResponse(
            {"status": "error", "message": str(exc)},
            status=500,
        )

    return JsonResponse({
        "status": "success",
        "mode": result.mode,
        "deleted_count": result.deleted_count,
        "remaining_count": result.remaining_count,
    })


# ---------------------------------------------------------------------------
# KBLI CRUD
# ---------------------------------------------------------------------------

@require_GET
def api_kbli_list(request):
    """Return all KBLI master records."""
    items = list(KbliMaster.objects.values("code", "name", "is_active", "created_at"))
    return JsonResponse({"items": items})


@require_superadmin
def api_kbli_create(request):
    """Create a new KBLI entry. POST { code, name, is_active? }."""
    import json as _json
    try:
        body = _json.loads(request.body)
    except (ValueError, TypeError):
        body = dict(request.POST)

    code = (body.get("code") or "").strip()
    name = (body.get("name") or "").strip()
    is_active = body.get("is_active", True)
    if isinstance(is_active, str):
        is_active = is_active.lower() in ("1", "true", "yes")

    if not code or not name:
        return JsonResponse({"status": "error", "message": "code dan name wajib diisi"}, status=400)

    if KbliMaster.objects.filter(code=code).exists():
        return JsonResponse({"status": "error", "message": f"KBLI {code} sudah ada"}, status=409)

    KbliMaster.objects.create(code=code, name=name, is_active=is_active)
    return JsonResponse({"status": "success", "code": code, "name": name, "is_active": is_active})


@require_superadmin
def api_kbli_update(request, code_id):
    """Update a KBLI entry. POST { name?, is_active? }."""
    import json as _json
    try:
        body = _json.loads(request.body)
    except (ValueError, TypeError):
        body = dict(request.POST)

    try:
        obj = KbliMaster.objects.get(code=code_id)
    except KbliMaster.DoesNotExist:
        return JsonResponse({"status": "error", "message": f"KBLI {code_id} tidak ditemukan"}, status=404)

    if "name" in body:
        obj.name = body["name"].strip()
    if "is_active" in body:
        val = body["is_active"]
        obj.is_active = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes")
    obj.save()

    return JsonResponse({"status": "success", "code": obj.code, "name": obj.name, "is_active": obj.is_active})


@require_superadmin
def api_kbli_delete(request, code_id):
    """Delete a KBLI entry."""
    try:
        obj = KbliMaster.objects.get(code=code_id)
    except KbliMaster.DoesNotExist:
        return JsonResponse({"status": "error", "message": f"KBLI {code_id} tidak ditemukan"}, status=404)
    obj.delete()
    return JsonResponse({"status": "success", "code": code_id})


# ---------------------------------------------------------------------------
# Report Summary (Executive Dashboard)
# ---------------------------------------------------------------------------

@require_GET
def api_report_summary(request):
    """Return executive summary metrics for the report dashboard.

    Returns:
      - tender_counts: berpotensi (>60%), belum_submit, sudah_submit, menang, gagal
      - omset: total_potensi, total_submitted, total_menang (based on HPS)
      - by_instansi: top instansi by count of potential tenders
      - by_tahap: distribution of tahap stages
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    from spse_crawler.submissions.models import TenderSubmissionStatus

    base = TenderResult.objects.all()

    # Potential tenders (ai_score > 60 OR is_it_priority)
    potential = base.filter(ai_score__gt=60)
    potential_count = potential.count()
    total_potensi_hps = sum(potential.values_list("hps", flat=True))

    # Submitted
    submitted_subs = TenderSubmissionStatus.objects.filter(status="sudah_submit")
    submitted_tender_ids = submitted_subs.values_list("tender_id", flat=True).distinct()
    submitted_tenders = base.filter(id__in=submitted_tender_ids)
    submitted_count = submitted_tenders.count()
    total_submitted_hps = sum(submitted_tenders.values_list("hps", flat=True))

    # Won
    won_subs = TenderSubmissionStatus.objects.filter(status="menang")
    won_tender_ids = won_subs.values_list("tender_id", flat=True).distinct()
    won_tenders = base.filter(id__in=won_tender_ids)
    won_count = won_tenders.count()
    total_won_hps = sum(won_tenders.values_list("hps", flat=True))

    # Not yet submitted (potential but no submission record)
    not_submitted_count = base.filter(ai_score__gt=60).exclude(id__in=submitted_tender_ids).count()

    # By instansi (top 10 potential)
    from django.db.models import Count
    by_instansi = list(
        potential.values("instansi")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )

    # By tahap distribution
    by_tahap = list(
        base.values("tahap_saat_ini")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )

    return JsonResponse({
        "counts": {
            "total": base.count(),
            "berpotensi": potential_count,
            "belum_submit": not_submitted_count,
            "sudah_submit": submitted_count,
            "menang": won_count,
        },
        "omset": {
            "total_potensi": total_potensi_hps,
            "total_submitted": total_submitted_hps,
            "total_menang": total_won_hps,
        },
        "by_instansi": by_instansi,
        "by_tahap": by_tahap,
    })


@require_GET
@login_required(login_url="/login/")
def report_summary_page(request):
    """Render the executive report summary page."""
    return render(request, "report_summary.html")


# ---------------------------------------------------------------------------
# WATCHLIST
# ---------------------------------------------------------------------------

@require_GET
def api_watchlist_list(request):
    """List watchlist items for the current user's company.

    Each item includes deadline and tahap_saat_ini for urgency display.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    from .models import TenderWatchlist
    from datetime import date
    company_id = user.company_id
    if user.role == "superadmin":
        company_id = request.GET.get("company_id")
        if company_id:
            company_id = int(company_id)

    if not company_id:
        return JsonResponse({"items": []})

    qs = TenderWatchlist.objects.filter(company_id=company_id).select_related("tender")
    today = date.today()
    items = []
    for w in qs[:200]:
        t = w.tender
        deadline = _extract_deadline_from_jadwal(t.jadwal_json)
        days_left = (deadline - today).days if deadline else None
        urgency = ""
        if days_left is not None:
            if days_left < 0:
                urgency = "overdue"
            elif days_left <= 3:
                urgency = "urgent"
            elif days_left <= 7:
                urgency = "warning"
        items.append({
            "id": w.id,
            "tender_id": t.id,
            "kode_instansi": t.kode_instansi,
            "id_lelang": t.id_lelang,
            "nama_paket": t.nama_paket,
            "instansi": t.instansi,
            "hps": t.hps,
            "hps_short": _format_hps_short(t.hps),
            "tahap_saat_ini": t.tahap_saat_ini,
            "is_it_priority": t.is_it_priority,
            "ai_score": t.ai_score,
            "priority_score": t.priority_score,
            "deadline": deadline.isoformat() if deadline else "",
            "days_left": days_left,
            "urgency": urgency,
            "notes": w.notes,
            "created_at": w.created_at.isoformat() if w.created_at else "",
        })
    return JsonResponse({"items": items})


@require_POST
def api_watchlist_add(request):
    """Add a tender to watchlist."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    from .models import TenderWatchlist
    try:
        body = json.loads(request.body) if request.body else {}
    except (ValueError, TypeError):
        body = dict(request.POST)

    tender_id = body.get("tender_id")
    if not tender_id:
        return JsonResponse({"error": "tender_id wajib"}, status=400)

    company_id = user.company_id
    if user.role == "superadmin" and body.get("company_id"):
        company_id = int(body["company_id"])
    if not company_id:
        return JsonResponse({"error": "Tidak ada perusahaan terkait"}, status=400)

    from .models import TenderResult
    try:
        tender = TenderResult.objects.get(pk=tender_id)
    except TenderResult.DoesNotExist:
        return JsonResponse({"error": "Tender tidak ditemukan"}, status=404)

    w, created = TenderWatchlist.objects.get_or_create(
        company_id=company_id,
        tender=tender,
        defaults={"user": user, "notes": body.get("notes", "")},
    )
    return JsonResponse({"status": "success", "id": w.id, "created": created})


@require_POST
def api_watchlist_remove(request):
    """Remove a tender from watchlist."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    from .models import TenderWatchlist
    try:
        body = json.loads(request.body) if request.body else {}
    except (ValueError, TypeError):
        body = dict(request.POST)

    watchlist_id = body.get("watchlist_id")
    tender_id = body.get("tender_id")

    company_id = user.company_id
    if user.role == "superadmin" and body.get("company_id"):
        company_id = int(body["company_id"])

    if watchlist_id:
        qs = TenderWatchlist.objects.filter(id=watchlist_id)
    elif tender_id and company_id:
        qs = TenderWatchlist.objects.filter(tender_id=tender_id, company_id=company_id)
    else:
        return JsonResponse({"error": "watchlist_id atau tender_id wajib"}, status=400)

    deleted = qs.delete()[0]
    return JsonResponse({"status": "success", "deleted": deleted})


# ---------------------------------------------------------------------------
# OPPORTUNITY SCORE (v0.1)
# ---------------------------------------------------------------------------

@require_GET
def api_opportunity_score(request, tender_id):
    """Return opportunity score for a tender (v0.1 with persistence).

    Requires authentication. Uses the user's company for context.

    Response contract (backward compatible):
      - opportunity_score: int (kept for backward compat)
      - score: int (new v0.1 field)
      - classification: str
      - status: "ready" | "not_ready"
      - components: dict (v0.1 breakdown)
      - recommendation: str (backward compat)
      - explanation: list[str]
      - calculation_version: str
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    from .models import TenderResult, OpportunityScore

    try:
        tender = TenderResult.objects.get(pk=tender_id)
    except TenderResult.DoesNotExist:
        return JsonResponse({"error": "Tender tidak ditemukan"}, status=404)

    company_id = user.company_id
    if user.role == "superadmin" and request.GET.get("company_id"):
        company_id = int(request.GET.get("company_id"))
    if not company_id:
        return JsonResponse({
            "status": "not_ready",
            "opportunity_score": 0,
            "score": 0,
            "classification": "TIDAK_DIREKOMENDASIKAN",
            "recommendation": "Tidak Direkomendasikan",
            "components": {},
            "explanation": ["Profil perusahaan tidak tersedia."],
            "calculation_version": "v0.1",
        })

    from spse_crawler.services.opportunity_scorer_v01 import calculate

    try:
        obj = calculate(tender_id=tender.id, company_id=company_id)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=404)

    breakdown = obj.breakdown_json or {}
    components = breakdown.get("components", {})

    # Backward-compatible recommendation mapping
    recommendation_map = {
        "PRIORITAS_TINGGI": "Sangat Direkomendasikan",
        "LAYAK_DIKEJAR": "Direkomendasikan",
        "REVIEW": "Perlu Ditinjau",
        "RISIKO_TINGGI": "Perlu Ditinjau",
        "TIDAK_DIREKOMENDASIKAN": "Tidak Direkomendasikan",
    }

    return JsonResponse({
        # Backward-compatible fields
        "opportunity_score": obj.final_score,
        "recommendation": recommendation_map.get(
            obj.classification, "Tidak Direkomendasikan"
        ),
        "reason": breakdown.get("explanation", [""])[0]
        if breakdown.get("explanation") else "",
        "strengths": [],
        "risks": [],
        # New v0.1 fields
        "score": obj.final_score,
        "status": obj.status,
        "classification": obj.classification,
        "components": components,
        "explanation": breakdown.get("explanation", []),
        "calculation_version": obj.calculation_version,
        "ai_match_summary": breakdown.get("ai_match_summary", ""),
    })


# ---------------------------------------------------------------------------
# RECOMMENDED TENDERS
# ---------------------------------------------------------------------------

@require_GET
def api_recommended(request):
    """Return recommended tenders for the user's company based on v0.1 opportunity score.

    Each result includes reason, strengths, and risks for explainability.
    Uses persisted OpportunityScore (v0.1) when available.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    company_id = user.company_id
    if user.role == "superadmin" and request.GET.get("company_id"):
        company_id = int(request.GET.get("company_id"))
    if not company_id:
        return JsonResponse({"items": []})

    from spse_crawler.companies.models import CompanyProfile

    try:
        company = CompanyProfile.objects.get(pk=company_id)
    except CompanyProfile.DoesNotExist:
        return JsonResponse({"items": []})

    # Broader filter: active tenders in qualification phases, excluding terminal states
    qs = TenderResult.objects.exclude(
        Q(tahap_saat_ini__icontains="selesai")
        | Q(tahap_saat_ini__icontains="kontrak")
        | Q(tahap_saat_ini__icontains="pembatalan")
    )

    # If company has KBLI codes, prioritize matching tenders but also include others
    company_kbli = list(company.kbli_codes_from_quals) if company.kbli_codes_from_quals else []
    if company_kbli:
        kbli_qs = qs.filter(kbli_code__in=company_kbli).order_by("-priority_score", "-hps")[:20]
        other_qs = qs.exclude(kbli_code__in=company_kbli).filter(
            Q(tahap_saat_ini__icontains="prakualifikasi")
            | Q(tahap_saat_ini__icontains="kualifikasi")
        ).order_by("-priority_score", "-hps")[:10]
        candidates = list(kbli_qs) + list(other_qs)
    else:
        candidates = list(qs.filter(
            Q(tahap_saat_ini__icontains="prakualifikasi")
            | Q(tahap_saat_ini__icontains="kualifikasi")
        ).order_by("-priority_score", "-hps")[:50])

    # Use v0.1 scorer for calculation
    from spse_crawler.services.opportunity_scorer_v01 import calculate

    scored = []
    for t in candidates:
        try:
            obj = calculate(tender_id=t.id, company_id=company_id)
        except ValueError:
            continue

        breakdown = obj.breakdown_json or {}
        explanation = breakdown.get("explanation", [])

        recommendation_map = {
            "PRIORITAS_TINGGI": "Sangat Direkomendasikan",
            "LAYAK_DIKEJAR": "Direkomendasikan",
            "REVIEW": "Perlu Ditinjau",
            "RISIKO_TINGGI": "Perlu Ditinjau",
            "TIDAK_DIREKOMENDASIKAN": "Tidak Direkomendasikan",
        }

        scored.append({
            "id": t.id,
            "nama_paket": t.nama_paket,
            "instansi": t.instansi,
            "hps": t.hps,
            "hps_short": _format_hps_short(t.hps),
            "tahap_saat_ini": t.tahap_saat_ini,
            "is_it_priority": t.is_it_priority,
            "ai_score": t.ai_score,
            "opportunity_score": obj.final_score,
            "recommendation": recommendation_map.get(
                obj.classification, "Tidak Direkomendasikan"
            ),
            "classification": obj.classification,
            "status": obj.status,
            "reason": explanation[0] if explanation else "",
            "strengths": [],
            "risks": [],
        })

    scored.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return JsonResponse({"items": scored[:20]})


# ---------------------------------------------------------------------------
# TENDER RADAR
# ---------------------------------------------------------------------------

@require_GET
def api_radar(request):
    """Return personalized Tender Radar for the user's company.

    Read-only. Uses persisted AIMatchResult + OpportunityScore.
    Does NOT trigger AI/LLM or crawler operations.

    Company context from request.user.company_id for company_admin/submitter.
    Superadmin may inspect explicitly via ?company_id=<id>.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    company_id = user.company_id
    if user.role == "superadmin" and request.GET.get("company_id"):
        try:
            company_id = int(request.GET.get("company_id"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid company_id"}, status=400)

    if not company_id:
        # Authenticated user without a company — empty state per project convention
        return JsonResponse({
            "status": "empty",
            "error": "Lengkapi profil perusahaan untuk mendapatkan rekomendasi tender.",
            "count": 0,
            "total": 0,
            "results": [],
        })

    from spse_crawler.companies.models import CompanyProfile
    if not CompanyProfile.objects.filter(pk=company_id, is_active=True).exists():
        return JsonResponse({
            "status": "empty",
            "error": "Lengkapi profil perusahaan untuk mendapatkan rekomendasi tender.",
            "count": 0,
            "total": 0,
            "results": [],
        })

    from spse_crawler.services.tender_radar import TenderRadarService

    filters = {}
    for key in ["classification", "search", "kbli_code", "location", "priority"]:
        val = request.GET.get(key, "").strip()
        if val:
            filters[key] = val

    for key in ["min_score", "hps_min", "hps_max"]:
        val = request.GET.get(key, "").strip()
        if val:
            try:
                filters[key] = int(val)
            except ValueError:
                pass

    for key in ["deadline_before", "deadline_after"]:
        val = request.GET.get(key, "").strip()
        if val:
            filters[key] = val

    if request.GET.get("include_not_ready") in ("1", "true", "True"):
        filters["include_not_ready"] = True

    try:
        limit = int(request.GET.get("limit", "20"))
    except ValueError:
        limit = 20
    limit = max(1, min(limit, 100))

    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        offset = 0
    offset = max(0, offset)

    data = TenderRadarService.get_radar(
        company_id=company_id,
        filters=filters,
        limit=limit,
        offset=offset,
    )

    return JsonResponse(data)


# ---------------------------------------------------------------------------
# INTELLIGENCE PIPELINE (status + manual reprocess)
# ---------------------------------------------------------------------------

@require_GET
def api_intelligence_status(request):
    """Return intelligence readiness status for the user's company.

    Read-only. Company-scoped:
      - company_admin/submitter -> their own company only
      - superadmin -> ?company_id=<id> or their own company
    Does NOT call an LLM, score, or mutate any data.

    Response reflects persisted readiness (READY / PARTIAL / NOT_READY) plus
    tender/coverage counts. See services/intelligence_status.py.
    """
    from spse_crawler.services.intelligence_status import compute_status

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    company_id = user.company_id
    if user.role == "superadmin" and request.GET.get("company_id"):
        try:
            company_id = int(request.GET.get("company_id"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid company_id"}, status=400)

    if not company_id:
        return JsonResponse({
            "status": "empty",
            "company_id": None,
            "readiness": {"ready": False, "level": "NOT_READY"},
            "counts": {
                "tenders": 0, "ai_matches": 0,
                "opportunity_scores": 0, "ready_opportunities": 0,
            },
            "coverage": {"ai_match_percent": 0, "opportunity_score_percent": 0},
            "message": "Lengkapi profil perusahaan untuk menilai kesiapan intelligence.",
        })

    data = compute_status(company_id)
    data["status"] = "ok"
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# COMPETITIVE & WINNER INTELLIGENCE (Phase 4)
# ---------------------------------------------------------------------------

@require_GET
def api_intelligence_company(request, company_id):
    """Return read-only competitive intelligence for a CompanyProfile.

    Authorization:
      - superadmin: may inspect any company_id.
      - company_admin / submitter: only their OWN company; any other
        company_id is denied (403).
    Read-only. No LLM, no mutation, no scoring. Does not fabricate
    participation / wins when the persisted data does not support them.
    """
    from spse_crawler.services.competitive_intelligence import (
        get_company_intelligence,
    )

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    if user.role != "superadmin" and user.company_id != company_id:
        return JsonResponse({"error": "Forbidden"}, status=403)

    data = get_company_intelligence(company_id)
    return JsonResponse(data)


@require_GET
def api_intelligence_tender_winner(request, tender_id):
    """Return the winner for a tender, if reliably persisted.

    Tenders are shared/global; any authenticated user may view this read-only
    data. No LLM, no mutation, no inference from names.
    """
    from spse_crawler.services.competitive_intelligence import get_tender_winner

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    return JsonResponse(get_tender_winner(tender_id))


@require_GET
def api_intelligence_tender_competition(request, tender_id):
    """Return the participant count + names for a tender (read-only).

    Real participant_count from TenderResult.peserta_count; participant names
    come from persisted TenderParticipant rows (Phase 5 /peserta capture) when
    available, and are never fabricated. Not available -> not_available.
    Tenders are shared/global.
    """
    from spse_crawler.services.competitive_intelligence import (
        get_tender_competition,
    )

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    return JsonResponse(get_tender_competition(tender_id))


@require_POST
def api_intelligence_reprocess(request):
    """Request reprocessing of a tender/company pair.

    Company-scoped:
      - company_admin/submitter -> must specify tender_id; company = own company
      - superadmin -> may pass company_id
    Requeues an AI_MATCH job (does NOT invoke the LLM synchronously).
    CSRF-enforced (no @csrf_exempt).
    """
    import json as _json
    from spse_crawler.services.intelligence_pipeline import reprocess

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    try:
        body = _json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        body = {}

    try:
        tender_id = int(body.get("tender_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "tender_id wajib diisi"}, status=400)

    company_id = user.company_id
    if user.role == "superadmin" and body.get("company_id"):
        try:
            company_id = int(body.get("company_id"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid company_id"}, status=400)

    if not company_id:
        return JsonResponse({"error": "Tidak ada perusahaan terkait"}, status=400)

    result = reprocess(tender_id, company_id)
    if not result.get("ok"):
        return JsonResponse({"error": result.get("error", "Gagal reprocess")}, status=400)
    return JsonResponse({"status": "ok", "queued": result.get("queued", 1)})


# ---------------------------------------------------------------------------
# PIPELINE SUMMARY
# ---------------------------------------------------------------------------

@require_GET
def api_pipeline_summary(request):
    """Return submission pipeline counts + urgency for the user's company.

    Counts tenders WITH submission status by status.
    Also counts tenders WITHOUT any submission status as "belum_diproses".
    Includes urgency: how many tenders have deadline < 3 or < 7 days.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    from spse_crawler.submissions.models import TenderSubmissionStatus

    company_id = user.company_id
    if user.role == "superadmin" and request.GET.get("company_id"):
        company_id = int(request.GET.get("company_id"))

    if not company_id:
        return JsonResponse({
            "belum_diproses": 0, "cocok_diproses": 0, "sudah_submit": 0,
            "menang": 0, "gagal": 0, "tidak_cocok": 0, "total": 0,
            "urgency": {"deadline_under_3_days": 0, "deadline_under_7_days": 0},
        })

    # Count submissions with each status (using conditional aggregation)
    from django.db.models import Count, Q as DQ
    qs = TenderSubmissionStatus.objects.filter(company_id=company_id)
    status_counts = {}
    for status_key, _label in TenderSubmissionStatus.STATUS_CHOICES:
        status_counts[status_key] = qs.filter(status=status_key).count()
    total_submitted = sum(status_counts.values())

    # Count tenders that have NO submission status for this company
    tender_ids_with_status = set(qs.values_list("tender_id", flat=True))
    total_tenders = TenderResult.objects.exclude(
        DQ(tahap_saat_ini__icontains="selesai")
        | DQ(tahap_saat_ini__icontains="kontrak")
        | DQ(tahap_saat_ini__icontains="pembatalan")
    ).count()
    belum_diproses_count = max(0, total_tenders - len(tender_ids_with_status))

    # Urgency: count tenders with deadline approaching
    from datetime import date, timedelta
    today = date.today()
    under_3 = 0
    under_7 = 0
    # Check active tenders (not terminal)
    active_tenders = TenderResult.objects.exclude(
        DQ(tahap_saat_ini__icontains="selesai")
        | DQ(tahap_saat_ini__icontains="kontrak")
        | DQ(tahap_saat_ini__icontains="pembatalan")
    ).exclude(id__in=tender_ids_with_status).values_list("jadwal_json", flat=True)[:200]

    for jadwal in active_tenders:
        dl = _extract_deadline_from_jadwal(jadwal)
        if dl:
            diff = (dl - today).days
            if 0 < diff <= 3:
                under_3 += 1
            elif 0 < diff <= 7:
                under_7 += 1

    return JsonResponse({
        "belum_diproses": belum_diproses_count,
        "cocok_diproses": status_counts.get("cocok_diproses", 0),
        "sudah_submit": status_counts.get("sudah_submit", 0),
        "menang": status_counts.get("menang", 0),
        "gagal": status_counts.get("gagal", 0),
        "tidak_cocok": status_counts.get("tidak_cocok", 0),
        "total": belum_diproses_count + total_submitted,
        "urgency": {
            "deadline_under_3_days": under_3,
            "deadline_under_7_days": under_7,
        },
    })


# ---------------------------------------------------------------------------
# COMPANY COMPLETION %
# ---------------------------------------------------------------------------

@require_GET
def api_company_completion(request):
    """Return company profile completion percentage.

    Uses weighted categories:
      - Identitas Perusahaan (name, nib): 20%
      - Legal & Kontak (npwp, address, phone, email, contact): 20%
      - Keuangan (modal_disetor, penghasilan_tahunan): 20%
      - Kualifikasi Izin/SBU: 15%
      - Pengalaman Kerja: 15%
      - SDM/Manajerial: 10%
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    company_id = user.company_id
    if user.role == "superadmin" and request.GET.get("company_id"):
        company_id = int(request.GET.get("company_id"))
    if not company_id:
        return JsonResponse({"completion_pct": 0, "categories": {}})

    from spse_crawler.companies.models import CompanyProfile, CompanyQualification

    try:
        company = CompanyProfile.objects.get(pk=company_id)
    except CompanyProfile.DoesNotExist:
        return JsonResponse({"completion_pct": 0, "categories": {}})

    # Category 1: Identitas (20%) — name + nib
    identitas_pct = 0
    if company.name:
        identitas_pct += 50
    if company.nib:
        identitas_pct += 50

    # Category 2: Legal & Kontak (20%) — npwp, address, phone, email, contact
    legal_fields = ["npwp", "address", "phone", "email", "contact_person"]
    legal_filled = sum(1 for f in legal_fields if getattr(company, f, ""))
    legal_pct = int((legal_filled / len(legal_fields)) * 100)

    # Category 3: Keuangan (20%) — modal + penghasilan
    keuangan_pct = 0
    if company.modal_disetor and company.modal_disetor > 0:
        keuangan_pct += 50
    if company.penghasilan_tahunan and company.penghasilan_tahunan > 0:
        keuangan_pct += 50

    # Category 4-6: Qualification-based
    quals = CompanyQualification.objects.filter(company=company)
    active_quals = quals.filter(status="active")

    has_izin = active_quals.filter(category="izin_usaha").exists()
    has_sbu = active_quals.filter(category="sbu").exists()
    has_pengalaman = active_quals.filter(category="pengalaman_kerja").exists()
    has_sdm = active_quals.filter(category="sdm").exists()

    # Kualifikasi Izin/SBU (15%)
    kualifikasi_pct = 0
    if has_izin:
        kualifikasi_pct += 50
    if has_sbu:
        kualifikasi_pct += 50

    # Pengalaman (15%)
    pengalaman_pct = 100 if has_pengalaman else 0

    # SDM (10%)
    sdm_pct = 100 if has_sdm else 0

    # Weighted total
    completion_pct = _clamp(int(
        identitas_pct * 0.20
        + legal_pct * 0.20
        + keuangan_pct * 0.20
        + kualifikasi_pct * 0.15
        + pengalaman_pct * 0.15
        + sdm_pct * 0.10
    ))

    qual_counts = {
        "active": active_quals.count(),
        "expired": quals.filter(status="expired").count(),
        "needs_renewal": quals.filter(status="needs_renewal").count(),
    }

    return JsonResponse({
        "completion_pct": completion_pct,
        "categories": {
            "identitas": identitas_pct,
            "legal_kontak": legal_pct,
            "keuangan": keuangan_pct,
            "kualifikasi": kualifikasi_pct,
            "pengalaman": pengalaman_pct,
            "sdm": sdm_pct,
        },
        "qual_counts": qual_counts,
    })
