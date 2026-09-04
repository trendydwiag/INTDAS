# PHASE 6D — INTEGRATION & DATA QUALITY HARDENING — IMPLEMENTATION REPORT

Date: 2026-08-31
Status: **READY FOR FINAL PRODUCTION AUDIT**

## 1. Executive Summary

`READY FOR FINAL PRODUCTION AUDIT`

Phase 6D is a hardening/integration pass over the accepted Phase 6 pipeline
(6A historical retention → 6B winner — 6C entity resolution more specifically).
No feature expansion, no schema change (NO migration), no Phase 7 / AI / deploy.
It **verified the full pipeline as one coherent system** and **fixed one real
integration bug** that would break live winner capture, plus added observability
and 26 integration/regression tests.

Two cross-cutting findings:

1. **BUG FIXED (PASS):** `DetailParser._fetch_winner()` built the wrong winner
   URL — `/{kode}/lelang/{id}/pemenang` — but the VERIFIED Phase 6B source is
   `/{kode}/evaluasi/{id}/pemenang`. The naive `url.replace("/pengumumanlelang",
   "/pemenang")` produced a non-existent path; in live crawling this would have
   fetched a 404 and silently skipped winner capture. Fixed with a dedicated
   `_build_winner_url()` parser + two regression tests.
2. **LEGACY DB STATE (REPORTED, not changed):** the dev SQLite database is at
   migration schema `web/0013` — migrations `web/0014`, `web/0015` and
   `companies/0003` are **NOT applied**. Schema checks (`PRAGMA`) confirm the
   `TenderParticipant`/`TenderWinner` tables lack the Phase 6B/6C columns
   (`npwp`, `harga_*`, `company`, `resolution_status`). Because participant and
   winner tables hold **0 rows**, no data is at risk. Per the Phase 6D
   constraint, this DB is **NOT migrated** here. Classified as a **BLOCKER to
   running the live pipeline on this DB** (must migrate `0014`/`0015`/`0003` in a
   separate, explicitly-approved step), but the repo migration graph is correct
   and `makemigrations --check` reports 0 pending.

Gate results: **412 tests passed / 0 failed**, `manage.py check` clean,
`makemigrations --check` clean, **NO migration** created.

---

## 2. Full Pipeline Verification

Actual traced flow (views.py `_execute_crawl` + scheduler.py `_do_crawl` are the
two crawl paths; both converge on the same stores):

```
DiscoveryParser.fetch_packages()
  → non-aborted tenders flow to Stage 2 (Phase 6A: awarded/decided retained)
  ↓
DetailParser.scrape_detail()
  → _extract_tahap_* ; is_retained_tahap() / is_eligible_tahap() gate
  → _fetch_peserta_data()  → _parse_participants()  [{name, npwp}]
  → if retained: _fetch_winner() → _build_winner_url() → _parse_winner()
        → _parse_money()
  ↓ (TenderDetail object)
TenderResult.update_or_create(kode, id_lelang)   ← unique (kode, id_lelang)
  ‖
sync_tender_participants()  → update_or_create(tender, name)   ← unique (tender, name)
  → resolve_company_identity(name, npwp) → apply_resolution_to()  (EXACT only)
  ↓
sync_tender_winner()  → TenderWinner.update_or_create(tender)    ← OneToOne
  → resolve_company_identity(company_name, npwp) → apply_resolution_to()
  ↓
CompanyProfile (linked only on EXACT identity)
  ↓
get_company_intelligence() / get_tender_winner() / get_tender_competition()
```

Transaction/error/retry/cascade semantics:

- **Transaction boundaries:** each store call persists independently
  (autocommit). The tender upsert, participant sync, and winner sync are
  *separate* transactions — a failure in one does not roll back the others.
- **Error handling:** detail fetch failure → Stage-1 fallback save (no
  participant/winner enrichment). PackageSkipped → skipped. Winner fetch/parse is
  wrapped in try/except and is **non-fatal** (missing/malformed winner never
  breaks the tender crawl).
- **Retries:** Playwright fetch has up to 3 session-invalidation retries for
  access-denied/Cloudflare; httpx falls back to Playwright.
- **Partial failure:** participant save succeeds + winner unavailable ⇒
  participant history retained (verified by test).
- **Duplicate behavior:** duplicates prevented at every level —
  `(kode, id_lelang)` on TenderResult, `(tender, name)` on TenderParticipant,
  OneToOne on TenderWinner, `(name, nib)` on CompanyProfile.
- **Update behavior:** upsert-only; re-crawl updates source values where changed,
  never deletes; `updated_at` mean "last re-synced" (Django `update_or_create`
  touches `auto_now` on the update path — benign, not a correctness issue).
- **Cascade:** deleting a tender (only via purge of non-retained) cascades to its
  participants/winner; `company` FK uses `SET_NULL` (never deletes the company).

## 3. Integration Tests

New file `spse_crawler/services/test_phase6_pipeline.py` — **26 tests**, all PASS:

| Scenario | Result |
|---|---|
| Crawl #1 → tender+participants+winner+links+intelligence | PASS |
| Crawl #2 (re-sync) → NO duplicate tender/participant/winner/company; identity stable | PASS |
| Intelligence available after crawl | PASS |
| No duplicate winner on re-run | PASS |
| Awarded pipeline + participant + winner + company survives `flush_non_retained` | PASS |
| Active prakualifikasi + participant survives auto-purge | PASS |
| Legitimate aborted/stale record still removable | PASS |
| Missing winner → no break, existing winner preserved | PASS |
| Malformed winner → not stored, existing preserved | PASS |
| Changed source values update correctly | PASS |
| Winner fetch only for retained awarded stages (`is_retained_tahap`) | PASS |
| `_build_winner_url` yields `/evaluasi/{id}/pemenang` | PASS |
| Case A: participant NPWP == company NPWP → EXACT auto-link | PASS |
| Case B: winner NPWP == company NPWP → EXACT auto-link | PASS |
| Case C: same name, no NPWP → CANDIDATE_ONLY, no link | PASS |
| Case C': same name, different NPWP → no link | PASS |
| Case D: duplicate company NPWP → IDENTITY_CONFLICT | PASS |
| Case E: participant NPWP != winner NPWP → not inferred same company | PASS |
| Participant source fields (name/npwp) preserved after link | PASS |
| Winner source fields (name/npwp/alamat/harga_*) preserved after link | PASS |
| Coverage: 3 participations / 1 winner → win_rate 0.3333 + honest basis | PASS |
| Win-rate not presented as authoritative universe rate (disclaimer present) | PASS |
| Legacy null-company rows safe to read, not silently overwritten | PASS |
| Legacy no-NPWP company → never auto-linked by name alone | PASS |
| Participant saved + winner unavailable → history preserved | PASS |
| `mask_identifier` never exposes full value; masked source NPWP never matches | PASS |

Cases A–E and immutability/partial-failure are individually asserted, so a
regression in any rule fails loudly.

## 4. Historical Retention Verification

`flush_non_retained()` (auto-purge after each scheduled crawl) retains:
- **Tender** (awarded/decided/completed + active prakualifikasi) — confirmed.
- **Participant** — survives with its link intact — confirmed.
- **Winner** — survives — confirmed.
- **CompanyProfile** — survives (not cascade-deleted; `SET_NULL` only) — confirmed.
- **links** (participant.company, winner.company) — intact after purge — confirmed.
- **intelligence** — `data_available` still True after purge — confirmed.
- **Active tenders** — retained — confirmed.
- **Legitimate aborted/stale records** — still removed by the purge — confirmed.
- Whether Phase-6B winner source verification says retention makes winner capture
  *possible*; the retention gate itself never targets retained tenders.

## 5. Entity Resolution Verification

| Status | Verification |
|---|---|
| `EXACT` (NPWP / official identifier) | auto-link ONLY on normalized exact identity; masked/empty/different never matches (Cases A, B) |
| `CANDIDATE_ONLY` | name-only / NPWP-no-match with same-name candidate — never auto-linked (Case C) |
| `IDENTITY_CONFLICT` | duplicate company NPWP, or same-name-different-NPWP ambiguity — never arbitrarily selected (Case D) |
| `UNRESOLVED` | no identity / no candidate — recorded, never invented |

All conservative 6C rules preserved (not weakened). `AUTO_LINK_STATUSES` =
`{EXACT_NPWP, EXACT_OFFICIAL_IDENTIFIER}` only.

## 6. Intelligence API Verification

- `get_company_intelligence()` reports real `total_participation`,
  `total_wins`, `total_winning_value`, `win_rate`, `recent_tenders`,
  `recent_wins`, `institution_frequency`, `location_frequency`,
  `kbli_frequency`, and `coverage`.
- **Win-rate honesty:** denominator is *captured* participations; the API
  explicitly documents this in `win_rate_basis` ("reflects only the
  crawled/retained subset, not the full tender universe") and in
  `coverage.winner_coverage_basis`. A 100-`participation`/20-`winner`/5-`wins`
  scenario reports `win_rate = 5/100 = 0.05` WITH the explicit subset disclaimer
  — it is never presented as authoritative without that caveat.
- `get_tender_winner()` returns real winner data when a `TenderWinner` row exists
  (`ok`), `not_available` when none, `not_found` when the tender is missing.
- No fabricated zeros; no-data keeps `data_available=False` + `None` statistics.

## 7. Coverage Metadata

API exposes `coverage`:
- `participation_coverage` — count of captured participations.
- `winner_coverage` — fraction of captured participations that yielded a captured
  win (= win_rate).
- `winner_coverage_basis` — honest textual limitation.
- `identity-resolution coverage` is exposed per-row via `resolution_status`
  (EXACT / CANDIDATE / CONFLICT / UNRESOLVED) on both `TenderParticipant` and
  `TenderWinner`, and surfaced in `get_tender_winner().winner.resolution_status`.

Adding an aggregate "identity-resolution coverage" key to the API would be
**backward-compatible** (additive key), but is NOT added here — the per-row
`resolution_status` already provides full provenance without changing the
response contract.

## 8. Legacy Data

Dev DB reality (read-only, non-destructive):
- **809 `TenderResult`**, **0 `TenderParticipant`**, **0 `TenderWinner`**,
  **2 `CompanyProfile`** (distinct non-empty NPWPs, none empty — no duplicates).
- **Schema lag:** tables are at migration `0013`. `web/0014`, `web/0015`,
  `companies/0003` are unapplied. Because participant/winner tables are empty,
  there are **no rows with `company=NULL`** to backfill, and no crashes occur on
  the participant/winner graph (there is no data to read).
- Behavior verified in tests with simulated pre-Phase-6 rows: reading legacy
  null-company rows does **not** crash and does **not** silently assign a wrong
  company; a CompanyProfile with missing NPWP is **never** auto-linked by name
  alone.
- **Backfill:** NOT executed (would be destructive/unsafe). Determined that no
  backfill is currently warranted (0 rows). If participant/winner data is later
  migrated in from an older schema, rows would carry `company=NULL` +
  `resolution_status=""`; any backfill must be explicit, conservative, and
  reviewed.

## 9. Partial Failure Behavior / Transaction Semantics

- Tender upsert, participant sync, winner sync are independent persistence steps
  (autocommit). Failure of any one does not roll back the others.
- Winner source unavailable ⇒ `sync_tender_winner(None)` returns `False`, creates
  nothing, and leaves any existing winner intact. The tender crawl continues.
- Winner entity-resolution failure cannot roll back the winner store
  (resolution is applied after the winner row is persisted, and only when a
  strict EXACT match happens).
- Participant save succeeds + winner fetch fails ⇒ participant history preserved
  (verified by test).
- Malformed winner response (empty name) ⇒ not persisted, does not destroy an
  existing winner (verified).

## 10. Observability

Added loguru logging (no full NPWP ever logged; `mask_identifier` keeps first-2 +
last-2 digits, e.g. `11****11`):

| Event | Log marker (level) |
|---|---|
| Winner page could not be fetched | `WINNER_FETCH_UNAVAILABLE` (debug) |
| Winner page returned no parseable record | `WINNER_PARSE_EMPTY` (debug) |
| Winner stored (with masked NPWP + resolution status) | `WINNER_STORED` (info) |
| Entity auto-linked (NPWP or NIB; masked NPWP) | `ENTITY_AUTO_LINK` (info) |
| Entity candidate only (no auto-link) | `ENTITY_CANDIDATE` (debug) |
| Entity identity conflict (id list, no NPWP) | `ENTITY_ID_CONFLICT` (warning) |
| Participant sync aggregate | `PARTICIPANT_SYNC` (info) |

Safe identifiers (`id_lelang`), company names, and **masked** NPWPs are used;
full tax-number values are never written to logs.

## 11. Performance

- `get_company_intelligence()` and `get_tender_winner()` use `select_related`
  for `tender`; participant/`winner` reads are constant-query (no N+1). Covered
  by existing `ServiceQueryCountTests`.
- **Deferred (acceptable, documented):** each `resolve_company_identity()` call
  scans `CompanyProfile.objects.all()` in Python (O(companies)) because the
  corpus is tiny (~2 rows). With a small company table this is not a bottleneck;
  it should be reconsidered only if the company corpus grows large. Flagged, not
  pre-optimized.
- No other obvious repeated DB query was found in the Phase 6 path.

## 12. Files Changed

- `spse_crawler/parsers/detail.py` — added `_build_winner_url()` (correct
  `/evaluasi/{id}/pemenang`); `_fetch_winner` + `scrape_detail` now use it; added
  `WINNER_FETCH_UNAVAILABLE`/`WINNER_PARSE_EMPTY` logs.
- `spse_crawler/services/entity_resolution.py` — added `mask_identifier()` and
  resolution-outcome logging (`ENTITY_AUTO_LINK` / `ENTITY_CANDIDATE` /
  `ENTITY_ID_CONFLICT` / `ENTITY_UNRESOLVED`).
- `spse_crawler/services/tender_participant_store.py` — added `PARTICIPANT_SYNC`
  aggregate log.
- `spse_crawler/services/tender_winner_store.py` — added `WINNER_STORED` log +
  `_safe_tender_id`/`_log_winner_stored` helpers.
- `spse_crawler/services/test_phase6_pipeline.py` — **NEW**: 26 integration &
  regression tests.

## 13. Tests

- `pytest`: **412 passed / 0 failed** (baseline 386 → 412; +26 new).
- `python manage.py check`: **0 errors**.
- `python manage.py makemigrations --check`: **No changes detected** (0 pending).

## 14. Migration

**NO migration created.** Phase 6D required no schema change. The repo migration
graph is unchanged and consistent. The migration-state finding is about the
**dev runtime DB** (at `0013`), which is NOT migrated here per the Phase 6D
constraint.

## 15. Remaining Risks

**BLOCKER**
- Dev/prod runtime DB is at schema `0013`. Migrations `web/0014`, `web/0015`,
  `companies/0003` are unapplied; the Phase 6B/6C store code assumes their
  columns. Running the live participant/winner write path on an unmigrated DB
  would hit `OperationalError: no such column`. The repo migration graph is
  correct; applying migrations must be an explicit, separately-approved deploy
  step (not done here).

**HIGH**
- None identified within Phase 6 scope.

**MEDIUM**
- `resolve_company_identity()` O(companies) scan per row — fine at current
  corpus, revisit if CompanyProfile grows.
- `updated_at` reflects "last re-synced" (Django `update_or_create` touches
  `auto_now`) — benign, but consumers should not treat it as "last data change".

**LOW**
- `@evaluasi/{id}/pemenangberkontrak` (contract-value page) remains unimplemented
  (out of Phase 6B minimal slice) — noted for future.

## 16. HARD STOP

Phase 6D is complete and `READY FOR FINAL PRODUCTION AUDIT`. Work **stops here**.

- No deployment.
- No Phase 7 / AI / Win-Probability / predictive work.
- No production migration applied.
- Awaiting review.

---

# Phase 6D complete.

Integration:
**PASS**

Pipeline:
**PASS**

Data integrity:
No participant/winner data exists in the dev DB (0 rows) so the graph is clean —
no orphans, no duplicates, no broken links. The 2 CompanyProfiles have distinct,
non-empty NPWPs. One integration bug fixed: the winner URL now builds the
verified `/evaluasi/{id}/pemenang` path. Legacy/dev DB is at migration `0013`
(reported, not migrated).

Tests:
**412 passed / 0 failed**

Migration:
**NO** (no schema change in Phase 6D; dev DB migration state reported, not applied)

Files:
- spse_crawler/parsers/detail.py
- spse_crawler/services/entity_resolution.py
- spse_crawler/services/tender_participant_store.py
- spse_crawler/services/tender_winner_store.py
- spse_crawler/services/test_phase6_pipeline.py

Report:
docs/implementation_report_phase_6d.md

HARD STOP.
