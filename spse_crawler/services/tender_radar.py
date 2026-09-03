"""Tender Radar — personalized opportunity discovery service (Phase 2).

Transforms existing persisted tender data + company profile + AI Match +
Opportunity Score into a ranked list of relevant opportunities for a company.

This is a read/ranking service over existing data. It does NOT:
  - call the LLM
  - trigger crawler
  - create new database tables
  - introduce a secondary competing score

Design principles:
  - DB-oriented: uses prefetched/annotated persisted AIMatchResult + OpportunityScore
  - Avoids N+1 recalculation loops
  - Bounded recalculation ONLY for eligible candidates lacking a persisted
    ready OpportunityScore (bounded by limit, never unbounded)
  - READY vs NOT_READY distinction
"""
from __future__ import annotations

from datetime import datetime

from django.db.models import Q
from django.utils import timezone as dj_timezone

from spse_crawler.ai_match.models import AIMatchResult
from spse_crawler.web.models import OpportunityScore, TenderResult

# Real classification labels used by Opportunity Score v0.1
CLASSIFICATION_PRIORITY = {
    "PRIORITAS_TINGGI": 5,
    "LAYAK_DIKEJAR": 4,
    "REVIEW": 3,
    "RISIKO_TINGGI": 2,
    "TIDAK_DIREKOMENDASIKAN": 1,
    "NOT_READY": 0,
}

# Terminal / closed tender stages — a tender with these is not eligible
_CLOSED_KEYWORDS = [
    "selesai",
    "kontrak",
    "pembatalan",
    "dibatalkan",
    "dinyatakan gagal",
    "gugur",
]


def _parse_date(date_str: str):
    if not date_str:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _extract_submission_deadline(jadwal_json: list):
    """Extract submission deadline from jadwal JSON (matches v0.1 logic)."""
    if not jadwal_json:
        return None, None
    p1_date = None
    p1_name = ""
    p2_date = None
    p2_name = ""
    fallback_date = None
    fallback_name = ""

    for item in jadwal_json:
        if not isinstance(item, dict):
            continue
        tahap = (item.get("tahap") or "").lower()
        end_str = item.get("sampai") or item.get("end") or ""
        d = _parse_date(end_str)
        if not d:
            continue
        if any(kw in tahap for kw in ["penawaran", "submission", "pengajuan", "ajuan"]):
            if p1_date is None or d > p1_date:
                p1_date = d
                p1_name = item.get("tahap", "")
        elif any(kw in tahap for kw in ["kualifikasi", "pra-kualifikasi", "prakualifikasi"]):
            if p2_date is None or d > p2_date:
                p2_date = d
                p2_name = item.get("tahap", "")
        if fallback_date is None or d > fallback_date:
            fallback_date = d
            fallback_name = item.get("tahap", "")

    if p1_date:
        return p1_date, p1_name
    if p2_date:
        return p2_date, p2_name
    return fallback_date, fallback_name


def _is_terminal(tahap: str) -> bool:
    t = (tahap or "").lower()
    return any(kw in t for kw in _CLOSED_KEYWORDS)


class TenderRadarService:
    """Assemble a ranked list of tender opportunities for a company."""

    @staticmethod
    def get_radar(company_id: int, filters: dict | None = None,
                  limit: int = 20, offset: int = 0) -> dict:
        """Return ranked radar results for a company.

        filters (optional):
            classification: str  — exact classification label filter
            min_score: int       — minimum OpportunityScore.final_score
            only_ready: bool     — exclude NOT_READY (default True for default list)
            include_not_ready: bool — include NOT_READY items (default False)
            search: str          — keyword in nama_paket / instansi / id_lelang
            kbli_code: str       — tender KBLI code match
            location: str        — substring in lokasi_pekerjaan
            priority: str        — "it" to filter is_it_priority True
            deadline_before: ISO date — only deadlines before this date
            deadline_after: ISO date  — only deadlines after this date
            hps_min / hps_max: int

        Returns dict:
            status, count, results [...]
        """
        filters = filters or {}

        # Base eligible queryset: not terminal
        qs = TenderResult.objects.exclude(
            Q(tahap_saat_ini__icontains="selesai")
            | Q(tahap_saat_ini__icontains="kontrak")
            | Q(tahap_saat_ini__icontains="pembatalan")
            | Q(tahap_saat_ini__icontains="dibatalkan")
            | Q(tahap_saat_ini__icontains="gugur")
        )

        # ---- Tender-side filters (DB-level) ----
        search = filters.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(nama_paket__icontains=search)
                | Q(instansi__icontains=search)
                | Q(id_lelang__icontains=search)
            )

        kbli_code = filters.get("kbli_code", "").strip()
        if kbli_code:
            qs = qs.filter(kbli_code=kbli_code)

        location = filters.get("location", "").strip()
        if location:
            qs = qs.filter(lokasi_pekerjaan__icontains=location)

        if filters.get("priority") == "it":
            qs = qs.filter(is_it_priority=True)

        hps_min = filters.get("hps_min")
        if hps_min:
            qs = qs.filter(hps__gte=int(hps_min))
        hps_max = filters.get("hps_max")
        if hps_max:
            qs = qs.filter(hps__lte=int(hps_max))

        deadline_after = filters.get("deadline_after")
        deadline_before = filters.get("deadline_before")

        # ---- AI Match + Opportunity Score in one query (company-scoped) ----
        # Only consider tenders that have an AIMatchResult for this company.
        matched_ids = AIMatchResult.objects.filter(
            company_id=company_id
        ).values_list("tender_id", flat=True)
        qs = qs.filter(id__in=matched_ids)

        # Prefetch persisted OpportunityScore for this company
        opp_map = {
            o.tender_id: o
            for o in OpportunityScore.objects.filter(company_id=company_id)
        }

        include_not_ready = bool(filters.get("include_not_ready"))
        only_ready = filters.get("only_ready")
        if only_ready is None:
            only_ready = not include_not_ready

        # Load candidates (respecting limit window, but load some margin for
        # classification/score ordering that happens in Python).
        candidates = list(qs.order_by("-priority_score", "-hps")[: int(limit) * 4 + offset])

        results = []
        for t in candidates:
            opp = opp_map.get(t.id)

            if opp is not None and opp.status == "ready":
                radar_status = "READY"
            else:
                radar_status = "NOT_READY"
                # Create a NOT_READY placeholder view (no recalculation,
                # no LLM, no AI) unless we are in bounded recalculation path.
                opp = None

            # Deadline info derived from jadwal_json
            deadline_date, deadline_tahap = _extract_submission_deadline(
                t.jadwal_json or []
            )
            days_left = None
            if deadline_date:
                days_left = (deadline_date - dj_timezone.now().date()).days

            # Deadline filters (applied post-derive since deadline is stored in JSON)
            if deadline_after:
                try:
                    da = datetime.strptime(str(deadline_after), "%Y-%m-%d").date()
                    if deadline_date is None or deadline_date < da:
                        continue
                except ValueError:
                    pass
            if deadline_before:
                try:
                    db = datetime.strptime(str(deadline_before), "%Y-%m-%d").date()
                    if deadline_date is None or deadline_date > db:
                        continue
                except ValueError:
                    pass

            # Classification "required" filter: only READY items classify
            req_class = filters.get("classification", "").strip()
            if req_class:
                if opp is None or opp.classification != req_class:
                    continue

            # Min score filter
            min_score = filters.get("min_score")
            if min_score:
                if opp is None or opp.final_score < int(min_score):
                    continue

            # only_ready: exclude NOT_READY
            if only_ready and radar_status == "NOT_READY":
                continue

            ai = None
            try:
                ai = AIMatchResult.objects.get(tender=t, company_id=company_id)
            except AIMatchResult.DoesNotExist:
                ai = None

            results.append({
                "tender_id": t.id,
                "id": t.id,
                "id_lelang": t.id_lelang,
                "title": t.nama_paket,
                "nama_paket": t.nama_paket,
                "instansi": t.instansi,
                "hps": t.hps,
                "location": t.lokasi_pekerjaan or "",
                "lokasi_pekerjaan": t.lokasi_pekerjaan or "",
                "tahap_saat_ini": t.tahap_saat_ini,
                "is_it_priority": t.is_it_priority,
                "kbli_code": t.kbli_code or "",
                "deadline": deadline_date.isoformat() if deadline_date else None,
                "deadline_days_left": days_left,
                "ai_match": {
                    "fit_score": ai.fit_score if ai else 0,
                    "summary": (ai.summary if ai else ""),
                },
                "opportunity": {
                    "score": opp.final_score if opp else 0,
                    "classification": opp.classification if opp else "NOT_READY",
                    "explanation": (
                        (opp.breakdown_json or {}).get("explanation", []) if opp else []
                    ),
                },
                "radar_status": radar_status,
            })

        # ---- Ranking ----
        # READY above NOT_READY (when not only_ready)
        def sort_key(item):
            opp = item["opportunity"]
            classification = opp.get("classification", "NOT_READY")
            cls_priority = CLASSIFICATION_PRIORITY.get(classification, 0)
            score = item["opportunity"].get("score", 0)
            days_left = item["deadline_days_left"]
            deadline_val = days_left if days_left is not None else 999999
            return (
                cls_priority,          # classification priority DESC
                score,                 # opportunity score DESC
                -deadline_val,          # deadline urgency (sooner = higher)
            )

        results.sort(key=sort_key, reverse=True)

        # Pagination after filtering
        total = len(results)
        page = results[offset: offset + limit]

        return {
            "status": "ok",
            "count": len(page),
            "total": total,
            "offset": offset,
            "limit": limit,
            "results": page,
        }
