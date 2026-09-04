# Phase 6 — Winner Intelligence & Historical Tender Intelligence: AUDIT REPORT

Date: 2026-08-31
Status: **PARTIALLY READY** — BLOCKED on winner + historical data foundation (see Executive Summary)
No implementation performed. Audit-first only. HARD STOP pending user review.

---

## Executive Summary

**READY FOR IMPLEMENTATION / BLOCKED / PARTIALLY READY → `PARTIALLY READY`**

Phase 6 (Winner Intelligence) is **NOT READY** and **cannot be honestly implemented as designed** from current code/data. The audit shows:

- **Winner Intelligence: BLOCKED.** There is **no winner parser, no `/pemenang` URL builder, no winner fetcher, and no winner URL constant anywhere** in the codebase. The `TenderWinner` model exists (foundation only) but has **0 rows**. `WINNER SOURCE = NOT VERIFIED`. The `get_tender_winner()` API correctly returns `not_available`.
- **Historical Tender Intelligence: BLOCKED by the lifecycle.** Awarded/completed/pemenang tenders are **actively excluded and purged** by two independent mechanisms, so historical winner/participation data **cannot accumulate**:
  1. **Stage-1 discovery filter** — `_SKIP_STATUS_KEYWORDS` in `parsers/discovery.py` (lines 34–42) drops tenders whose status contains `kontrak`, `dikontrak`, `selesai`, `penandatanganan`, etc. at discovery, before they ever reach Phase 5's Stage-2 retention gate.
  2. **Scheduler auto-purge** — `web/scheduler.py:273–281` calls `flush_non_prakualifikasi()` at the end of **every** scheduled (hourly) crawl, deleting **all** `TenderResult` where `is_prakualifikasi=False` (i.e. every non-prakualifikasi tender, including every awarded/decided tender) via CASCADE.
- **Entity Resolution: MULTI-TENANT BLOCKED.** No persisted company identity signal links `TenderParticipant`/`TenderWinner` to `CompanyProfile`. NPWP is blank/duplicate-able on `CompanyProfile`. No participant row exists to resolve.
- **Historical data model:** existing models (TenderResult/TenderParticipant/TenderWinner) are **sufficient** — no new models needed; the gap is pipeline+lifecycle, not schema.
- **Intelligence Metrics:** `get_company_intelligence()` returns honest `null`/`data_available=False` — currently valid, and currently meaningless because no data feeds them.

The concrete, minimal Phase 6 prerequisites are: (1) stop the discovery drop and the scheduler auto-purge of decided tenders, (2) add winner fetch/parse (requires verifying a real `/pemenang` or equivalent endpoint — **not yet verified**), (3) populate participation link at save time. See Section 10 for the split.

---

## 1. Architecture (verified from code)

- Django project `web_ui`; app `spse_crawler` contains models, parsers, crawler, services, scheduler, and admin UI (single-app monolith; `companies`, `accounts`, `ai_match`, `submissions`, `audit` are separate small apps).
- Crawl lifecycle: `parsers/discovery.py` (Stage 1, list of packages per instansi) → `parsers/detail.py` (Stage 2, per-tender detail incl. participants) → `services/tender_participant_store.py` (upsert) → `models/tender.py` (TenderDetail confirmed-retained gate, Phase 5).
- Scheduler: `web/scheduler.py` APScheduler BackgroundScheduler, hourly crawl (`IntervalTrigger(hours=1)`), 10-min intelligence pipeline tick.
- Winner pipeline: **NONE** (see Section 3).
- No archive/history/snapshot layer exists (`grep` verified; only comments mention "historical" in CI service docstrings).

## 2. Data Inventory (live dev SQLite, read-only)

| Item | Count |
|---|---|
| `TenderResult` total | 809 |
| `is_prakualifikasi=True` | 195 |
| `is_prakualifikasi=False` | 614 |
| `tahap contains 'penetapan pemenang'` | 4 |
| `tahap contains 'pemenang'` | 76 |
| `TenderParticipant` | **0** |
| `TenderWinner` | **0** |
| `KbliMaster` | 9 |

Top `tahap_saat_ini` values: `pengumuman pascakualifikasi…` 157, `evaluasi administrasi…` 140, `Pengumuman Pemenang…` 66+10, `pengumuman prakualifikasi` 48.

**Decisive finding:** all 3 sampled `Pengumuman Pemenang` tenders are **shell records** — `peserta_count = 0`, no participants, no winner, `jadwal_json []`, `ai_analysis_json` empty, no winner-adjacent keyword in any free-text field. Stored fields include `nama_paket`, `hps`, `tahap`, `tanggal_dibuat` only. **No winner identity or winning value is stored anywhere.**

## 3. Winner Data Verification Matrix

| Check | Result | Evidence |
|---|---|---|
| Winner parser exists | **NOT VERIFIED / not present** | `parsers/detail.py` 23-method catalog — no winner method |
| `/pemenang` URL builder | **NOT VERIFIED / absent** | `config/settings.py` has only `dt_endpoint`, `detail_url`, `main_page_url`, `host_url`; no winner builder |
| Winner URL/endpoint constant | **NOT VERIFIED / absent** | `config/settings.py` |
| Winner location (in which page) | **NOT VERIFIED** | no fetch/selector code exists |
| Persisted winner rows | **NOT VERIFIED / 0** | `TenderWinner.objects.count() == 0` |
| Winner identity/value stored | **NOT VERIFIED / none** | shell records, sampled pemenang tenders |
| Winner fetch during crawl | **NOT VERIFIED / none** | no `pemenang` in crawler flow |
| Stored/raw HTML available | **NONE** | no raw HTML retained |
| `get_tender_winner()` accuracy | **Correct** (honest `not_available`) | CI service harness |

**WINNER SOURCE = NOT VERIFIED.** Do not fabricate endpoints/selectors. Build requires verifying a real winner endpoint by live inspection (blocked), OR deferring.

## 4. Participant Readiness (feeds Winner/Historical Intelligence)

- `TenderParticipant` unique `(tender, name)`; `TenderWinner` OneToOne on TenderResult (Phase 5, migration 0013). Models ready.
- **BUT** `TenderParticipant.objects.count() == 0` and `api_intelligence_company` returns `data_available=False`, `total_participation=null`.
- Participation is stored by `_parse_participants` from the `/peserta` page — but only for tenders that **reach Stage 2** and are retained. Awarded tenders rarely reach Stage 2 (blocked by Stage-1 filter) and are purged even if they do.
- `peserta_count` comes from the `/peserta` detail fetch (0 for the sampled pemenang tenders); the discovery-time `peserta` column count is parsed but **not persisted**.

## 5. Lifecycle / Purge Audit (BLOCKER for historical data)

Callers of purge (`grep` verified):

1. **`web/scheduler.py:273–281` `_auto_purge()` — AUTOMATIC, after EVERY scheduled crawl** → `flush_non_prakualifikasi()` deletes ALL `is_prakualifikasi=False` tenders. **This includes every awarded/decided/pemenang tender.** ⚠️ **BLOCKER.**
2. `web/views.py:1006–1037` `api_flush_records` — manual, `@require_superadmin`, POST `mode`. Not automatic.
3. `web/management/commands/seed_data.py:51` `_flush` — dev reseed; does not touch TenderResult (only submission/audit/users/quals/companies/KBLI).

**Cascade audit:** every child of `TenderResult` uses `on_delete=CASCADE`:
`TenderParticipant` (models.py:70), `TenderWinner` (OneToOne), `OpportunityScore` (models.py:179), `IntelligenceJob`, `AIMatchResult`, `TenderSubmissionStatus`, `TenderWatchlist`. **Deleting a TenderResult silently destroys all related intelligence.**

**Net effect:** even if Phase 5's Stage-2 gate were the only change, awarded tenders would still (a) be dropped at Stage-1 discovery and (b) be auto-purged after every hourly crawl. **Historical winner/participation data cannot accumulate** under current lifecycle. Archive/survival mechanism does not exist.

## 6. Entity Resolution (Multi-Tenant) Readiness

- Identity signals on `TenderParticipant`: only **name** (persisted). No NPWP on participant/winner. `CompanyProfile`: `nib` unique, **`npwp` blank + NOT unique**, and no FK from participant/winner to company.
- **Rule from spec:** NPWP must be treated as a much stronger identity than name; no auto-merges for `POSSIBLE`/`UNKNOWN`. Currently **no NPWP is captured at all** for participants/winers, so NPWP-based resolution cannot run.
- Realistic status today: **UNKNOWN / NOT READY** — name-only matching would risk wrong merges (forbidden). Needs (a) NPWP capture in the winner/participant parser (once winner source verified), and (b) a conservative matcher before any company link.

## 7. Historical Data Model Audit

Existing models are **sufficient**:
- `TenderResult` — tender + `hps` + `tahap` (won/deal value not persisted; no `nilai_kontrak` column — `nilai_kontrak` is parsed by discovery but **dropped at persistence**).
- `TenderParticipant`, `TenderWinner` — per-tender participation/wins.

No new tables required. The blocker is **pipeline + lifecycle**, not schema. Do not create new models.

## 8. Intelligence Metrics Audit (must be valid from existing data)

- `get_company_intelligence()` — `total_participation/total_wins/total_winning_value` all `null`, `data_available=False`. **Currently valid** because no participant/winner rows exist. Would require participation/winner linkage to become meaningful.
- `get_tender_competition()` — Phase 5 enriched (participant names), but 0 rows → returns no real competition data.
- `get_tender_winner()` — `not_available` (correct).
- Consumers `TenderRadar` + `IntelligenceStatus` **explicitly exclude terminal stages** (`selesai/kontrak/pembatalan/dibatalkan/gagal`) — so historical/awarded tenders have no current consumer and no metric is computed from them.

## 9. Phase 4 API Readiness

| Endpoint | Service | Status |
|---|---|---|
| `GET /api/intelligence/company/<id>/` | `get_company_intelligence` | AVAILABLE (always `null` metrics) |
| `GET /api/intelligence/tender/<id>/winner/` | `get_tender_winner` | AVAILABLE (always `not_available`) |
| `GET /api/intelligence/tender/<id>/competition/` | `get_tender_competition` | AVAILABLE (empty w/o data) |
| `GET /api/radar/` | TenderRadar | AVAILABLE (excludes terminal) |
| `GET /api/opportunity/<id>/` | OpportunityScore | AVAILABLE |
| `GET /api/intelligence/status/` | IntelligenceStatus | AVAILABLE (excludes terminal) |

APIs exist and are honest; they surface `null`/`not_available`/empty because no underlying data exists.

## 10. Recommended Phase 6 Scope

### IMPLEMENT NOW (minimal, evidence-backed)
1. **Stop the discovery drop of decided tenders** — revise `_SKIP_STATUS_KEYWORDS` (`parsers/discovery.py:34–42`) so awarded/completed statuses reach Stage 2 (align with Phase 5's retention intent).
2. **Stop automatic destruction of historical data** — change scheduler `_auto_purge` (`web/scheduler.py:273–281`) to NOT purge decided/awarded tenders (e.g. only purge non-retained/ineligible rows, or gate on retention). Minimal change, proven prerequisite.
3. **Only if winner source is verified** (live inspection): add winner URL builder + parser + `_parse_winner` + persistence into `TenderWinner`, capturing **NPWP** when available.
4. **Only if winner/participant NPWP captured:** add conservative entity-resolution (NPWP-anchored, `POSSIBLE`/`UNKNOWN` never auto-merged).
5. Update CI services/metrics to consume newly persisted data, keeping `WINNER SOURCE` honest.

### DEFER
- Full multi-tenant winner dashboards, win-rate statistics, recent-win lists, historical trend charts (require accumulated data that cannot exist until 1&2 land and a crawl runs).
- NPWP-based company auto-linking (needs NPWP capture first).

### NOT POSSIBLE YET (BLOCKED, do not build on assumption)
- Any winner parsing without a verified real endpoint/selector — **WINNER SOURCE NOT VERIFIED**.
- Any `total_wins`/win-rate from fabricated data.

### Exact files likely to change (NOT modified now)
- `spse_crawler/parsers/discovery.py` (`_SKIP_STATUS_KEYWORDS`)
- `spse_crawler/web/scheduler.py` (`_auto_purge`)
- `spse_crawler/config/settings.py` (winner URL builder — only after endpoint verified)
- `spse_crawler/parsers/detail.py` / `models/tender.py` (winner fetch+gating)
- `spse_crawler/services/competitive_intelligence.py` (consumer updates)
- Possibly a new `spse_crawler/services/entity_resolution.py`

## 11. Risk Classification

| Risk | Severity | Notes |
|---|---|---|
| Scheduler auto-purge destroys awarded/historical tenders (+ cascade children) | **BLOCKER** | scheduler.py:273–281 |
| Stage-1 discovery filter drops awarded tenders before retention | **HIGH** | discovery.py:34–42 |
| Winner source/endpoint NOT VERIFIED | **HIGH** | cannot implement winner parser honestly |
| No NPWP captured → entity resolution blocked | **HIGH** | NPWP rule cannot be honored |
| `nilai_kontrak` parsed but dropped | **MEDIUM** | no won-value persistence for win metrics |
| Historical data lacks archive/survival | **MEDIUM** | no snapshot/history layer |
| Cascade deletes all child intelligence on purge | **MEDIUM** | all children CASCADE |
| Multi-tenant name-only matching would risk wrong merges | **MEDIUM** | forbidden without NPWP anchor |

---

## Verification Baseline (unchanged, re-confirmed this audit)
- `DJANGO_DEBUG=1 DJANGO_SECRET_KEY=testsecret123 python -m pytest --tb=short -q spse_crawler` → **325 passed**
- `manage.py makemigrations --check` → **No changes detected**
- `manage.py check` → **No issues (0 silenced)**
- Migrations `0011, 0012, 0013` applied (dev SQLite)

## HARD STOP
**No implementation performed. No winners, no crawler/parser changes, no new models/migrations, no new APIs/UI/AI, no deployment changes.** Awaiting user review of this report before any Phase 6 coding.
