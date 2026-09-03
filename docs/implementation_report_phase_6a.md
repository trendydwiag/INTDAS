# PHASE 6A — HISTORICAL DATA RETENTION & PIPELINE CORRECTION — IMPLEMENTATION REPORT

Date: 2026-08-31
Status: **READY FOR REVIEW**

## Executive Summary

`READY FOR REVIEW`

Phase 6A delivered the two historical-retention blockers identified in the Phase 6 audit:

1. **Discovery no longer drops awarded/decided/completed tenders.** `_SKIP_STATUS_KEYWORDS` in `parsers/discovery.py` now only hard-drops aborted (cancelled/failed) statuses. Active, awarded, decided, completed, and pemenang statuses all flow to Stage 2.
2. **The scheduled auto-purge no longer destroys historical data.** The hourly crawl's `_auto_purge` now calls a new history-safe `flush_non_retained()` (in `services/purger.py`) instead of the blanket `flush_non_prakualifikasi()`, so awarded/decided/completed tenders — and their participants — survive recurring crawler execution.

A necessary Stage-2 companion fix was also made: `_TAHAP_AWARDED_KEYWORDS` in `parsers/detail.py` now includes `"pengumuman pemenang"`, because the actual SPSE data showed `"Pengumuman Pemenang"` tahap strings were previously rejected at Stage 2 (verified via DB: `is_retained_tahap("pengumuman pemenang")` was `False`).

No schema change. No migration. Scope strictly limited to DISCOVERY RETENTION + HISTORICAL PURGE SAFETY.

---

## Problem 1 — Discovery

### Before

`parsers/discovery.py` `_SKIP_STATUS_KEYWORDS` contained awarded/completed markers, so awarded/decided/completed/pemenang tenders were dropped in Stage 1 before reaching Stage 2:

```python
_SKIP_STATUS_KEYWORDS = ("selesai", "batal", "gagal", "dikontrak",
                         "penandatanganan", "kontrak", "pembatalan")

def _is_eligible_status(cls, status):
    if not status:
        return True
    lower = status.lower()
    if any(kw in lower for kw in cls._SKIP_STATUS_KEYWORDS):
        return False
    return any(kw in lower for kw in cls._PRAKUALIFIKASI_STATUS_KEYWORDS)
```

### After

`_SKIP_STATUS_KEYWORDS` now contains only aborted statuses; active/awarded/completed statuses all flow to Stage 2. The final gate on `_PRAKUALIFIKASI_STATUS_KEYWORDS` is removed (unused constant deleted) — Stage 2 `is_eligible_tahap` / `is_retained_tahap` are the authoritative retention gate:

```python
_SKIP_STATUS_KEYWORDS = ("batal", "gagal", "pembatalan")

def _is_eligible_status(cls, status):
    if not status:
        return True
    lower = status.lower()
    return not any(kw in lower for kw in cls._SKIP_STATUS_KEYWORDS)
```

**File + function changed:** `spse_crawler/parsers/discovery.py` — `_SKIP_STATUS_KEYWORDS` class attr, `_is_eligible_status()`, and two log messages.

**Verification (db-backed):**
- `_is_eligible_status("pengumuman pascakualifikasi")` → `True`
- `_is_eligible_status("Pengumuman Pemenang")` → `True`
- `_is_eligible_status("selesai")` / `"dikontrak"` / `"penandatanganan kontrak"` → `True`
- `_is_eligible_status("batal")` / `"gagal"` / `"pembatalan"` → `False`

### Companion Stage-2 fix (required for the retention contract)

**Before:** `_TAHAP_AWARDED_KEYWORDS` did not contain `"pengumuman pemenang"`; DB check showed `is_retained_tahap("pengumuman pemenang")` → `False` (these real SPSE tenders were rejected at Stage 2).

**After:** added `"pengumuman pemenang"`:

```python
_TAHAP_AWARDED_KEYWORDS = ("selesai", "tender selesai", "penandatanganan",
                           "kontrak", "penetapan pemenang",
                           "pengumuman pemenang", "rekomendasi")
```

**File + function changed:** `spse_crawler/parsers/detail.py` — `_TAHAP_AWARDED_KEYWORDS` class attr.

**Verification:** `is_retained_tahap("pengumuman pemenang")` and `"pengumuman pemenang [...]"` → `True`; aborted/unknown unchanged.

---

## Problem 2 — Historical Purge

### Before

`web/scheduler.py` `_auto_purge()` (run after every scheduled hourly crawl) called `flush_non_prakualifikasi()` which deleted **all** `TenderResult` where `is_prakualifikasi=False` — including every awarded/decided/completed tender — cascade-deleting their participants/winners/matches/scores.

```python
# Auto-purge: remove non-prakualifikasi records after scheduled crawl
@sync_to_async
def _auto_purge():
    from spse_crawler.services.purger import flush_non_prakualifikasi
    result = flush_non_prakualifikasi()
    ...
```

### After

`_auto_purge()` now calls the new history-safe `flush_non_retained()`:

```python
# Post-crawl housekeeping: safely remove stale/non-retained records.
# Historical awarded/decided/completed tenders are NEVER purged here
# (Phase 6A — historical retention). See flush_non_retained().
@sync_to_async
def _auto_purge():
    from spse_crawler.services.purger import flush_non_retained
    result = flush_non_retained()
    ...
```

**File + function changed:** `spse_crawler/web/scheduler.py` — `_auto_purge()`.

New function in `spse_crawler/services/purger.py`:

```python
def flush_non_retained() -> FlushResult:
    retained_condition = Q()
    for kw in DetailParser._TAHAP_ACCEPT_KEYWORDS:
        retained_condition |= Q(tahap_saat_ini__icontains=kw)
    for kw in DetailParser._TAHAP_AWARDED_KEYWORDS:
        retained_condition |= Q(tahap_saat_ini__icontains=kw)
    qs = (TenderResult.objects.filter(is_prakualifikasi=False)
          .exclude(retained_condition))
    ...
```

`flush_non_retained()` keeps:
- All active prakualifikasi tenders (`is_prakualifikasi=True`)
- All active non-prakualifikasi tenders (accept/eligible keyword)
- All awarded/decided/completed tenders (awarded keyword)

and deletes only non-prakualifikasi records that match neither (aborted/cancelled/failed/unrecognised-status stale rows). It reuses the same keyword sets as Stage 2 to avoid logic drift. The existing manual purge functions (`flush_non_prakualifikasi`, `flush_inactive`, `flush_all`) are unchanged and remain available to the `@require_superadmin` manual `api_flush_records` endpoint.

**File + function changed:** `spse_crawler/services/purger.py` — added `flush_non_retained()`.

---

## Historical Retention Contract (verified behavior)

The following behaviors are now guaranteed by code and proven by tests:

- **Awarded / decided / completed / pemenang tenders are discovered** and carried through Stage 1 to Stage 2.
- **Awarded / decided / completed / pemenang tenders are retained at Stage 2** (`is_retained_tahap` True), including `"Pengumuman Pemenang"` / `"pengumuman pemenang [...]"`.
- **Awarded / decided / completed tenders survive the scheduled hourly crawl auto-purge** — `flush_non_retained()` deletes none of them.
- **Participants of awarded/decided/completed tenders survive** — the cascade never fires for retained tenders.
- **Active tenders (prakualifikasi and non-prakualifikasi) survive** the auto-purge.
- **Truly aborted / cancelled / failed / unrecognised-status non-prakualifikasi records** are still purged, preserving legitimate cleanup.
- **Idempotency unchanged:** discovery does not duplicate tenders; participant upsert is unchanged (`(tender, name)` unique); no new FK/broken-key risk introduced.

---

## NPWP Verification

Per scope, NPWP is **verified but NOT fixed in 6A** (NPWP implementation is scheduled for Phase 6C). Chain, via evidence from reading code + tests:

- **HTML:** `/peserta` page table is `[No, Nama, NPWP, ...]` — `_parse_peserta_count`/`_parse_participants` treat 3+ cell rows with a numeric first cell as participant rows (`parsers/detail.py:518-554`).
- **Parser:** `_parse_participants` extracts `name = cells[1].text()`, `npwp = cells[2].text()` into `{"name", "npwp"}` dicts (detail.py:549-553). Verified by existing test `test_parse_participants`.
- **Parsed object:** `TenderDetail.participants: list[dict]` with `name` + `npwp` fields (`models/tender.py:182-185`).
- **Storage:** `sync_tender_participants` persists `npwp` into `TenderParticipant.npwp` (`services/tender_participant_store.py:59`); parser→store contract covered by tests.
- **Database:** `TenderParticipant.npwp` field exists (`web/models.py:77`, indexed). **Currently 0 participant rows exist in dev DB**, so NPWP is not present in data — but the full capture chain ALREADY supports NPWP end-to-end at the code level.

**Gap identified (Phase 6C):** participants are only fetched for tenders that reach Stage 2 and whose `/peserta` page returns rows; NPWP is stored but no entity-resolution / CompanyProfile linking exists (by design, deferred). No fix applied in 6A — out of scope.

---

## Tests

**Result:** `338 passed` (previous 325 + 13 new).

New test files:
- `spse_crawler/parsers/test_discovery.py` — 7 tests: active/discovered, awarded/discovered, decided+completed/discovered, aborted/filtered, empty→Stage2, unknown→Stage2, skip-keyword regression guard.
- `spse_crawler/services/test_purger.py` — 6 tests: awarded kept, active kept, aborted deleted, mixed, participant survives with awarded tender, aborted participant cascade-deleted.

Command:
```
DJANGO_DEBUG=1 DJANGO_SECRET_KEY=testsecret123 python -m pytest --tb=short -q spse_crawler
→ 338 passed in 12.81s
```

## Migration

`Required: NO` — no schema change.

```
python manage.py makemigrations --check
→ No changes detected
python manage.py check
→ System check identified no issues (0 silenced)
```

---

## Remaining Phase 6 Blockers

(unchanged by 6A — still open)

- **Winner source:** WINNER SOURCE still `NOT VERIFIED` — no winner parser/URL/selector; `TenderWinner` remains foundation-only (0 rows). Historical retention now makes winner capture *possible*, but the source must be verified in a later phase.
- **NPWP persistence:** NPWP capture chain exists but participant rows are empty in dev data; no NPWP-based handling (deferred to 6C).
- **Entity resolution:** no NPWP↔CompanyProfile linking; name-only matching forbidden (deferred).
- **Company intelligence:** `get_company_intelligence()`/`get_tender_winner()` still return honest `null`/`not_available` until winner/participation data population lands.

## Files Changed

- `spse_crawler/parsers/discovery.py` — retention fix (skip keywords, eligibility gate, logs)
- `spse_crawler/parsers/detail.py` — added `"pengumuman pemenang"` to `_TAHAP_AWARDED_KEYWORDS`
- `spse_crawler/services/purger.py` — added `flush_non_retained()`
- `spse_crawler/web/scheduler.py` — `_auto_purge` now uses `flush_non_retained()`
- `spse_crawler/parsers/test_discovery.py` — new tests
- `spse_crawler/services/test_purger.py` — new tests
- `docs/implementation_report_phase_6a.md` — this report

## Git / Deployment

No deployment, no production migration, no destructive data cleanup, no DB reset. Code changes only in workspace.

## HARD STOP

Implementation + tests + report complete. **STOP — awaiting user review.** Do not start Phase 6B.
