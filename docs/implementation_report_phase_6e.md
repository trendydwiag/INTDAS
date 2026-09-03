# PHASE 6E — FINAL PRODUCTION READINESS + SOP OPERASIONAL — IMPLEMENTATION REPORT

Date: 2026-08-31
Status: **GO**

## 1. Executive Summary

Phase 6E is the final, non-feature pass of Phase 6: a production-readiness audit,
fixing only genuine Phase 6 deployment blockers that can be safely fixed in
scope, and producing a Bahasa Indonesia SOP for every **actual** role plus a
deployment runbook and a final GO/NO-GO.

**Key results**
- Verified the full pipeline (discovery → detail → participant → winner → entity
  resolution → intelligence → history-safe purge) as production-ready.
- Confirmed the Phase 6 migration set (`companies/0003`, `web/0014`,
  `web/0015`) is **fully additive and safe**; no data migration required.
- Confirmed production fail-fast settings (DEBUG/SECRET/ALLOWED_HOSTS/DATABASE)
  behave correctly.
- Confirmed only **3 actual roles** exist (`superadmin`, `company_admin`,
  `submitter`). The report's "Admin/Operator/Analyst/Viewer" names do **not**
  exist; SOPs are written for the real roles only (per scope rules).
- Result: **GO** — no code blocker remains. The single BLOCKER is a deployment
  step (migrating the production DB to `0014`/`0015`/`companies/0003`), not a
  code defect.

No Phase 7, no AI addition, no redesign, no auto-deploy, no production migration
applied, no destructive backfill.

## 2. Production Readiness Audit

Performed by reading all Phase 6 reports (audit, 6a–6d) AND inspecting the actual
codebase (models, views, scheduler, parsers, stores, services, settings). No
reliance on reports alone. Documented in `docs/PHASE_6_PRODUCTION_READINESS.md`.

## 3. Migration Audit

- `companies/0003`: adds `db_index=True` to `CompanyProfile.npwp` (additive).
- `web/0014`: adds `TenderWinner` `alamat`, `harga_penawaran`, `harga_terkoreksi`,
  `harga_negosiasi` (BigInteger nullable), `npwp` (CharField default ""); plus
  non-destructive help_text updates.
- `web/0015`: adds `TenderParticipant.company` (FK nullable, SET_NULL) +
  `resolution_status`; `TenderWinner.company` (FK nullable, SET_NULL) +
  `resolution_status`; indexes on `company`.

Dependency order (deterministic, Django-resolved): `companies/0003` → `web/0014`
→ `web/0015` (0015 depends on both 0003 and 0014). All additive, safe against
existing rows (nullable/default columns), no data migration required, cannot fail
on existing duplicate records (NPWP is indexed, not unique).

**Production sequence:** run `python manage.py migrate --noinput` (once). No
migration applied automatically per scope rules.

## 4. Database Safety

- FK graph: `TenderResult` → CASCADE → participants + winner; participant/winner
  → `CompanyProfile` via `SET_NULL`.
- Deleting a CompanyProfile unlinks (SET_NULL) participations/wins but CASCADE
  deletes its `OpportunityScore`/`IntelligenceJob`/`IntelligenceDailyUsage`.
- Historical purge (`flush_non_retained`) never targets retained tenders, so the
  intelligence graph is not destroyed by routine cleanup.
- Duplicate protection: unique `(kode_instansi, id_lelang)`, unique `(tender,
  name)`, OneToOne winner, unique NIB. NPWP not unique → conflicts surfaced as
  `IDENTITY_CONFLICT`, never auto-merged.
- Legacy `company=NULL` rows are safe to read and never wrongly linked.

## 5. Historical Retention

Awarded/decided/completed (`selesai`, `kontrak`, `penetapan pemenang`, etc.) and
active prakualifikasi tahap are retained by both the detail-stage gate and
`flush_non_retained()`. Tender, participant, winner, and company relationship all
survive the scheduled auto-purge. Cleanup of aborted/stale records remains
enabled. **PASS.**

## 6. Winner Pipeline

`_build_winner_url` → `/evaluasi/{id}/pemenang`; httpx→Playwright fallback;
retry/backoff; `_parse_winner` (requires "Nama Pemenang" row + non-empty name);
`_parse_money`; missing/malformed → `WINNER_FETCH_UNAVAILABLE`/`WINNER_PARSE_EMPTY`
→ None (never fabricated); missing NPWP/price → nullable, no fabrication;
idempotent OneToOne upsert; masked-NPWP logging. **PASS.**

## 7. Entity Resolution

Only `EXACT_NPWP` and `EXACT_OFFICIAL_IDENTIFIER` auto-link. Name-only →
`CANDIDATE_ONLY`; conflict → `IDENTITY_CONFLICT`; otherwise `UNRESOLVED`. No fuzzy
auto-merge, no destructive merge, no arbitrary duplicate NPWP selection. Masked
NPWP (`*`) never matches. **PASS.**

## 8. Security

Production settings fail-fast (DEBUG/ALLOWED_HOSTS/SECRET_KEY/DATABASE verified
via subshell: `ImproperlyConfigured` raised without env). CSRF enforced on all
operational + KBLI + company-delete + submission-update endpoints. Superadmin-only
gating on crawl/trigger/toggle/flush/KBLI. Company-scoped authorization on
intelligence. NPWP masked in logs (`mask_identifier`). Session/CSRF cookies
`Secure` in production + HttpOnly. Password validators active.

Regression notes (non-blocking): watchlist add/remove use `@csrf_exempt` (pre-existing
Phase 3, authenticated & company-scoped) — tracked as MEDIUM.

## 9. Scheduler/Crawler

In-process lock prevents parallel crawls; per-instansi partial failures don't
abort others; retry/timeout configured; duplicate prevention via unique
constraints + `update_or_create`; post-crawl `flush_non_retained` (history-safe);
clear log markers. Multi-replica scheduler caveat documented (MEDIUM). **PASS.**

## 10. Backup & Rollback

Full procedure in `docs/DEPLOYMENT_RUNBOOK.md`: pg_dump backup, verification,
restore, code rollback, migration rollback guidance (additive; prefer code
rollback, never reverse a migration after new data exists in added columns),
historical-data protection. **COMPLETE.**

## 11. SOP Documentation

- `docs/SOP_OPERASIONAL.md` — general SOP in Bahasa Indonesia covering all roles,
  role matrix, daily ops, incident handling, data quality, security.
- `docs/SOP_SUPERADMIN.md` — detailed superadmin procedures incl. AMAN vs
  BERISIKO/DESTRUKTIF purge.
- Per scope rules, role-specific files for **Admin/Operator/Analyst/Viewer were
  NOT created** because those roles do not exist; their functions are mapped to
  the real roles (`company_admin`, `submitter`) inside `SOP_OPERASIONAL.md`.

## 12. Role Matrix

See `docs/SOP_OPERASIONAL.md` §3 (Fitur × Superadmin / Admin Perusahaan / Admin
Submitter) using only actual roles and values Boleh / Tidak boleh / Baca saja.

## 13. Data Quality

Coverage is exposed honestly: `participation_coverage`, `winner_coverage`,
`winner_coverage_basis` ("fraction of captured participations that resulted in a
captured win; coverage limited to tenders where winner data was actually
persisted"), and `win_rate_basis` ("reflected only the crawled/retained subset").
Operators are instructed **not** to manually "fix" source-derived data without an
approved procedure.

## 14. Incident Handling

Procedures for crawler-stopped, SPSE-down, winner-page-missing, participant-
missing, empty NPWP, identity conflict, duplicate company, database error,
migration error, scheduler error — each as Gejala → Pemeriksaan → Tindakan →
Verifikasi → Eskalasi. See `docs/SOP_OPERASIONAL.md` §8.

## 15. Tests

- `python -m pytest`: **412 passed / 0 failed** (baseline 412 at Phase 6D end).
- `python manage.py check`: **0 errors**.
- `python manage.py makemigrations --check`: **No changes detected**.

## 16. Files Changed

- `docs/SOP_OPERASIONAL.md` (new)
- `docs/SOP_SUPERADMIN.md` (new)
- `docs/DEPLOYMENT_RUNBOOK.md` (new)
- `docs/PHASE_6_PRODUCTION_READINESS.md` (new)
- `docs/implementation_report_phase_6e.md` (this report)

No application code changed in Phase 6E.

## 17. Remaining Risks

**BLOCKER**
- B1: Production DB is not yet migrated to schema Phase 6B/6C. Dev DB is at
  `0013`; `companies/0003`, `web/0014`, `web/0015` are unapplied. Resolved as a
  **deployment step** (`migrate --noinput`); not applied automatically. Not a
  code defect.

**HIGH**
- None.

**MEDIUM**
- M1 watchlist `@csrf_exempt` (pre-existing, auth-scoped).
- M2 in-process scheduler; single-replica required.
- M3 `resolve_company_identity` O(companies) scan per row.

**LOW**
- L1 anonymous CSV/Excel export (no NPWP; Phase 1 design).
- L2 `updated_at` = last re-sync semantics (benign).

## 18. Final GO / NO-GO

# **GO**

- Production readiness: **PASS**
- Tests: **412 passed / 0 failed**
- Django check: **PASS**
- Migration check: **PASS** (makemigrations --check = No changes; apply 0003/0014/0015 at deploy)
- Security: **PASS**
- Historical retention: **PASS**
- Winner pipeline: **PASS**
- Entity resolution: **PASS**
- SOP: **COMPLETE**
- Deployment: **GO**
