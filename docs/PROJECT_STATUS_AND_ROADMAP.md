# PROJECT STATUS & DEVELOPMENT ROADMAP

**Project:** SPSE Inaproc Smart Tender Matcher & Multi-Role Workflow System / Tender Intelligence Dashboard (`scrap_lpse`)  
**Baseline Date:** 7 September 2026  
**Repository State:** Local Development Environment (`db.sqlite3` / Python 3.10.19 / Django 5.0.9)  
**Test Suite Status:** **473 passed / 0 failed in 16.03s** (100% Pass Rate)  
**Overall Deployment Status:** **CONDITIONAL GO** (Local & Staging Verified; Production Deployment Blocked on Runbook Execution & Bug Fix)

---

## 1. Executive Summary

This document establishes the authoritative project-management and engineering baseline for `scrap_lpse`. Over the development lifecycle from August 2026 through 7 September 2026, the project has evolved from a two-stage LPSE crawler into a full-stack Tender Intelligence System featuring multi-tenant role-based access control, deterministic opportunity scoring, personalized tender radar, company identity resolution, and a mobile-responsive, accessible web dashboard.

### Core Achievements to Date
1. **Data Foundation & Core Crawler:** Two-stage scraping (DataTables XHR discovery + stealth Playwright detail parser) supporting 600 government procurement instances, capturing tender metadata, schedules, qualification criteria, and participants.
2. **Historical Data Retention (Phase 6A):** Discovery parser (`parsers/discovery.py`) and scheduled purger (`services/purger.py:flush_non_retained`) preserve awarded, decided, and completed tenders along with their participant rosters.
3. **Winner Intelligence Foundation (Phase 6B):** Live-verified scraper for SPSE `/evaluasi/{id}/pemenang` capturing winning bidder name, masked NPWP, company address, and three distinct price points (`harga_penawaran`, `harga_terkoreksi`, `harga_negosiasi`).
4. **Conservative Entity Resolution (Phase 6C):** Strict identity matching engine (`services/entity_resolution.py`) enforcing that company auto-linking occurs **strictly** on exact normalized NPWP or verified official identifier (NIB). Fuzzy and name-only auto-merges are strictly forbidden.
5. **Production Hardening (Phase 6.4):** Implemented single-process scheduler daemon architecture (`run_scheduler` management command) and explicit AI fallback provenance tracking (`is_fallback`, `fallback_reason`).
6. **Dashboard UX, Ergonomics & Accessibility (UX-001 to UX-007, P2, A11y):** Complete frontend re-composition:
   - **Role-Gating (UX-005):** Operational and destructive controls (Flush Data, manual crawl, scheduler, monitor, KBLI admin) restricted strictly to Superadmin.
   - **Profile Completion Meter (UX-006):** Live integration of `/api/company/completion/` displaying weighted progress and category breakdowns with actionable CTA.
   - **Hierarchy Flip (UX-007):** Intelligence readiness bar and personalized Tender Radar positioned in the primary first-viewport.
   - **Mobile Responsiveness (P2):** Eliminated `cloneNode` DOM duplication; deployed single-state off-canvas filter drawer, responsive tender cards (`results-cards-mobile`), and &ge;44&times;44px touch targets.
   - **Accessibility & Microcopy (A11y):** Full WAI-ARIA modal dialog compliance, hierarchical Escape key navigation, WCAG AA contrast (&ge;4.5:1), and professional Indonesian microcopy.

### The Immediate Reality
The application code, local database schema, and test suite are in an advanced state (**473 automated tests passing**). However, **production deployment is not complete**. A live cross-environment inspection against `https://monitor-lpse.khansia.co.id/` revealed two production blockers:
1. **Frontend Company ID Binding Bug:** In `dashboard.html`, an unconstrained fallback binds non-superadmin users without an assigned company to `items[0]` (Company ID 6 on the server), triggering `HTTP 403 Forbidden` on qualification mutations.
2. **Unmigrated Production Database:** Production PostgreSQL has not executed migrations `companies.0003`, `web.0014`, `web.0015`, `ai_match.0002`, and `ai_match.0003`.

---

## 2. Current Repository State

### 2.1 Codebase Metrics & Environment
- **Branch:** `main` (workspace: `/Users/trendy/scrap_lpse`)
- **Python Runtime:** Python 3.10.19 (via pyenv)
- **Framework:** Django 5.0.9, APScheduler 3.10, Playwright 1.40+
- **Test Suite:** 473 tests passing (`pytest` / `web_ui.settings`)
- **Active Development Database:** Local SQLite (`db.sqlite3`), fully migrated through all migrations across all 6 applications.

### 2.2 Live Database Content Audit (Local Development `db.sqlite3`)
Verified via direct ORM query on 7 September 2026:
- `TenderResult`: **823 records**
- `TenderParticipant`: **3,101 records**
- `TenderWinner`: **1 record** (Live verified from `acehbaratkab` crawl)
- `CompanyProfile`: **3 records**
- `OpportunityScore`: **13 records**
- `AIMatchResult`: **11 records**

### 2.3 Django Migration State
Verified via `manage.py showmigrations` (all applied `[X]` locally):
- `accounts`: `0001_initial`
- `companies`: `0001_initial`, `0002_add_details_kbli_codes`, `0003_alter_companyprofile_npwp_and_more`
- `ai_match`: `0001_initial`, `0002_aimatchresult_fallback_reason_and_more`, `0003_aimatchresult_blockers_and_more`
- `submissions`: `0001_initial`, `0002_alter_tendersubmissionstatus_status`
- `audit`: `0001_initial`
- `web`: `0001` through `0015_tenderparticipant_company_and_more`

---

## 3. Development Phase Matrix

The following matrix reconstructs the actual development phases from repository evidence, implementation reports, and migration histories:

| Phase | Phase Name | Primary Objective | Key Deliverables & Evidence | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 0** | Core Crawler & Dashboard Foundation | 2-stage LPSE scraping, stealth, basic UI | `parsers/discovery.py`, `parsers/detail.py`, `core/browser.py`, `web/views.py` | `COMPLETE` |
| **Phase 1** | Opportunity Score v0.1 | Standardized (tender, company) scoring | `services/opportunity_scorer_v01.py`, `web/models.py` (`OpportunityScore`) — 64 tests | `VERIFIED` |
| **Phase 2** | Tender Radar | Ranked personalized opportunity discovery | `services/tender_radar.py`, `api_radar` endpoint — 25 tests | `VERIFIED` |
| **Phase 3** | Intelligence Status / Readiness | System data sufficiency indicator | `services/intelligence_status.py`, readiness chip UI — 20 tests | `VERIFIED` |
| **Phase 4 / PD-1**| Automated Intelligence Pipeline | Bounded background AI matching & scoring queue | `services/intelligence_pipeline.py`, rate limiting, `select_for_update` | `VERIFIED` |
| **Phase 5** | Competitive Intelligence Data Foundation | Capture participant names, NPWPs, retention gate | `models/tender.py`, `TenderParticipant`, `TenderWinner`, migration `web/0013` — 29 tests | `VERIFIED` |
| **Phase 6A** | Historical Data Retention | Prevent drop of awarded tenders in discovery & purge | `parsers/discovery.py` (`_SKIP_STATUS_KEYWORDS`), `services/purger.py` (`flush_non_retained`) | `VERIFIED` |
| **Phase 6B** | Winner Source Verification & Parser | Discover & scrape real `/evaluasi/{id}/pemenang` | `DetailParser._build_winner_url`, `_parse_winner`, `services/tender_winner_store.py`, migration `web/0014` | `VERIFIED` |
| **Phase 6C** | Conservative Entity Resolution | Exact-only matching (NPWP/NIB) to CompanyProfile | `services/entity_resolution.py`, `normalize_npwp`, migration `companies/0003`, `web/0015` — 27 tests | `VERIFIED` |
| **Phase 6D** | Integration & Data Quality Hardening | URL bug fix, end-to-end data pipeline verification | Dedicated `_build_winner_url`, integration suite — 412 tests baseline | `VERIFIED` |
| **Phase 6E** | Final Production Readiness & SOP | Production audit, role verification, operational SOP | `docs/PHASE_6_PRODUCTION_READINESS.md`, `SOP_OPERASIONAL.md`, `SOP_SUPERADMIN.md` | `VERIFIED` |
| **Phase 6.1** | Local DB Migration & PD-1 Activation | Apply migrations `0014`/`0015`/`0003`, activate tick | `db.sqlite3` migrated, Q lookup syntax bug fixed in `_claim_jobs` | `COMPLETE` |
| **Phase 6.2** | Live Staging Crawl Verification | Live SPSE test on `acehbaratkab` | Persisted 19 live participants, 1 winner, verified zero fuzzy auto-merges | `VERIFIED` |
| **Phase 6.3** | Production Sign-Off Audit | Full configuration, security & runbook sign-off | `docs/implementation_report_phase_6_3.md` (Conditional GO) | `VERIFIED` |
| **Phase 6.4** | Scheduler & AI Provenance Hardening | Single-process scheduler daemon & fallback audit | `run_scheduler` command, `AIMatchResult.is_fallback`, migration `ai_match/0002` | `VERIFIED` |
| **Phase 6.5** | Browser-Level E2E Verification | Playwright headless browser user journeys | 14 user journeys tested; verified UI rendering of live crawl data | `VERIFIED` |
| **Phase 6.8** | Cross-Environment Parity & 403 Forensic | Audit parity with `monitor-lpse.khansia.co.id` | Root-caused server 403 error to frontend `loadCompanyData()` fallback bug; declared NO-GO | `VERIFIED` |
| **UX-001..004**| Safe Boundaries & Sanitization | Table layout boundaries, KBLI renderer XSS safety | Escaped HTML injection in KBLI renderer, tender detail modal layout | `VERIFIED` |
| **UX-005** | Role-Gating & Operational Separation | Segregate destructive actions from ordinary users | Flush modal, crawl trigger, KBLI admin gated to Superadmin (`TestUX005SidebarRoleGating`) | `VERIFIED` |
| **UX-006** | Profile Completion Banner & Meter | Consume `/api/company/completion/` in dashboard | Header meter badge, first-viewport completion banner with CTA (`TestUX006ProfileCompletionBanner`) | `VERIFIED` |
| **UX-007** | Dashboard Hierarchy Flip | Bring intelligence & radar to top priority | Re-ordered viewport: Intelligence bar & Radar above table | `VERIFIED` |
| **P2 Pass** | Responsive / Mobile Pass | Eradicate `cloneNode`, off-canvas drawer, cards | `#results-cards-mobile`, single drawer, &ge;44px tap targets (`TestP2MobileResponsivePass`) | `VERIFIED` |
| **A11y Pass**| Accessibility & Microcopy Pass | WAI-ARIA dialogs, Escape handler, contrast, copy | `role="dialog"`, `handleEscapeKey`, WCAG AA (&ge;4.5:1), Indonesian copy (`TestA11yMicrocopyPass`) | `VERIFIED` |
| **Phase 6.9** | Production Deployment Execution | Apply migrations on production DB & deploy daemon | Runbook `docs/production_migration_runbook.md` execution | `BLOCKED` |
| **Phase 7** | Advanced Analytics & Notifications | Competitor win probability, WhatsApp/Email alert | Automated alerts, dossier PDF export, tender price optimization | `NOT STARTED` |

---

## 4. Completed & Verified Work

Every item below has direct code, test, and execution evidence in the repository:

### 4.1 Crawler & Data Pipeline
- **Two-Stage Crawling:** DataTables API discovery (`parsers/discovery.py`) paired with Playwright stealth browser (`parsers/detail.py`, `core/browser.py`). Verified in live crawl (`Phase 6.2`).
- **Awarded & Decided Retention:** `parsers/discovery.py:_is_eligible_status` allows awarded and completed statuses; `services/purger.py:flush_non_retained` deletes only non-retained packages, protecting awarded records.
- **Winner & Price Scraping:** `parsers/detail.py:_build_winner_url` constructs `/{kode}/evaluasi/{id}/pemenang`; `DetailParser._parse_winner` extracts name, address, masked NPWP, and 3 price points. Tested in `spse_crawler/services/test_tender_winner.py` (22 tests).
- **Idempotent Storage:** `services/tender_participant_store.py` and `services/tender_winner_store.py` utilize `update_or_create` with unique constraints, ensuring safe re-crawling.

### 4.2 Entity Resolution Engine
- **Conservative Exact-Only Matching:** Implemented in `spse_crawler/services/entity_resolution.py`.
- **Status Vocabulary:** `EXACT_NPWP`, `EXACT_OFFICIAL_IDENTIFIER`, `CANDIDATE_ONLY`, `IDENTITY_CONFLICT`, `UNRESOLVED`.
- **NPWP Normalization:** `normalize_npwp()` extracts raw digits, strips fiscal formatting, rejects masked/garbage patterns.
- **Zero Fuzzy Merging:** Name similarity alone is strictly prohibited from auto-linking. Covered by 27 tests in `spse_crawler/services/test_entity_resolution.py`.

### 4.3 Intelligence Services
- **Opportunity Score v0.1:** `services/opportunity_scorer_v01.py` implements deterministic scoring based on KBLI match, HPS fit, qualification completeness, and urgency. Tested by 64 tests in `spse_crawler/services/test_opportunity_v01.py`.
- **Tender Radar:** `services/tender_radar.py` provides personalized ranked discovery across qualification, deadline, winning, and new announcements. Tested by 25 tests in `spse_crawler/services/test_tender_radar.py`.
- **Intelligence Readiness Status:** `services/intelligence_status.py` computes coverage percentages and categorizes readiness (`SIAP`, `SEBAGIAN`, `BELUM SIAP`, `KOSONG`). Tested by 20 tests in `spse_crawler/services/test_intelligence_status.py`.

### 4.4 Dashboard UX, Responsive & Accessibility
- **Role-Gating (UX-005):** Operational controls in sidebar (`#btn-crawl`, `#crawl-instansi`, `#flush-modal`, `#btn-scheduler`, `#monitor-status-badge`, `#kbli-modal`) wrapped in `{% if user.is_authenticated and user.is_superadmin %}`. Verified via `TestUX005SidebarRoleGating`.
- **Profile Completion Banner (UX-006):** Endpoint `/api/company/completion/` integrated into frontend. Displays progress bar, categories breakdown, and modal trigger CTA. Verified via `TestUX006ProfileCompletionBanner`.
- **Hierarchy Flip (UX-007):** First viewport re-ordered to present Intelligence Readiness Bar and Tender Radar above main data tables.
- **Mobile Responsive Drawer & Cards (P2):** Eliminated `cloneNode` entirely. Single-state `#app-sidebar` with off-canvas transition, `#results-cards-mobile` view, and &ge;44&times;44px touch targets. Verified via `TestP2MobileResponsivePass`.
- **Accessibility & Microcopy (A11y):** All modals upgraded to WAI-ARIA `role="dialog" aria-modal="true" aria-labelledby="..."`, explicit `[X]` close buttons with `aria-label`, global hierarchical Escape handler (`handleEscapeKey`), `:focus-visible` styling, WCAG AA (&ge;4.5:1) text contrast, and natural Indonesian terminology. Verified via `TestA11yMicrocopyPass`.

### 4.5 Production Daemon Architecture
- **Single-Process Scheduler Daemon:** `spse_crawler/web/management/commands/run_scheduler.py` runs APScheduler isolated from Gunicorn WSGI processes.
- **AI Provenance Tracking:** `AIMatchResult` persists `is_fallback` and `fallback_reason`. Verified in `spse_crawler/web/test_phase64_hardening.py` (6 tests).

---

## 5. Conditional / Partially Verified Work

The following components are implemented and functional in code, but their operational activation or production verification remains conditional:

1. **Automated Intelligence Pipeline (PD-1):**
   - *Status:* `IMPLEMENTED — VERIFICATION REQUIRED`
   - *Detail:* Queue logic (`intelligence_pipeline.py`) and scheduler registration are built and tested. In development, jobs execute cleanly. However, in production, scheduled autonomous execution depends on deploying the dedicated scheduler container (`spse-scheduler`).
2. **Competitive Intelligence API Coverage on Production:**
   - *Status:* `CONDITIONAL GO`
   - *Detail:* `/api/intelligence/company/{id}/` and `/api/intelligence/tender/{id}/winner/` compute real metrics. In local development, 3,101 participants and 1 winner exist. In production, metrics currently return `data_available=False` because the production database has not yet scraped and stored post-Phase 6 data.
3. **Cross-Environment Data Parity:**
   - *Status:* `CONDITIONAL GO`
   - *Detail:* Local environment exhibits 100% detail richness (schedules, qualifications, participants). Production server `monitor-lpse.khansia.co.id` currently displays blank schedules and qualifications on sample tenders due to running on pre-Phase 6 crawler code and unmigrated database.

---

## 6. Remaining Work

1. **Frontend Company ID Binding Fix (Bug G from Phase 6.8):**
   - Restrict fallback `company = items[0]` in `dashboard.html:loadCompanyData()` to Superadmin users only. For company admins and submitters, strictly require `me.company_id`.
2. **CSRF Exemption Elimination on Data Mutation Endpoints:**
   - 8 endpoints still carry `@csrf_exempt` (`companies/views.py`, `ai_match/views.py`, `web/views.py`). Replace with standard Django CSRF header verification (`X-CSRFToken`).
3. **Production Database Migration Execution:**
   - Execute `python manage.py migrate --noinput` against production PostgreSQL (`DATABASE_URL`).
4. **Production Deployment of Single-Process Scheduler Daemon:**
   - Update production Docker Compose / systemd deployment to launch `python manage.py run_scheduler` as an independent service.
5. **Initial Production Historical Crawl:**
   - Execute an initial crawl on production to populate real `TenderParticipant` and `TenderWinner` records under the history-safe retention policy.
6. **Documentation Synchronization:**
   - Reconcile `PRD.md`, `PROJECT_CONTEXT.md`, and `adr.md` with the 7 September 2026 state.

---

## 7. Current Blockers

### P0 — Production Blockers (Must resolve before Production GO)
1. **Frontend Company ID Binding Bug (`dashboard.html:2300-2306`):**
   - *Evidence:* `docs/implementation_report_phase_6_8.md`. Non-superadmin users without an assigned company ID are assigned `items[0]` (Company 6 on server), resulting in `HTTP 403 Forbidden` (`{"error": "Tidak ada akses ke perusahaan ini"}`) during qualification creation.
   - *Impact:* Non-superadmin users cannot save company qualifications on server.
2. **Unmigrated Production PostgreSQL Database:**
   - *Evidence:* Server inspection in Phase 6.8. Production DB lacks columns from `companies.0003`, `web.0014`, `web.0015`, `ai_match.0002`, `ai_match.0003`.
   - *Impact:* Crawler cannot persist winners or participant company links; intelligence queries fail.

### P1 — Important Closures (Resolve during deployment maintenance window)
1. **Multi-Worker WSGI Scheduler In-Memory Deprecation:**
   - Ensure Gunicorn web workers do not run in-memory schedulers; deploy `spse-scheduler` container as defined in `docker-compose.yml`.
2. **Production Historical Data Cold Start:**
   - Production has 0 participants and winners until the first post-migration crawl executes.

### P2 — Improvements (Post-baseline enhancements)
1. **CSRF Token Hardening:** Convert remaining 8 `@csrf_exempt` endpoints to standard CSRF token validation.
2. **Company Onboarding Guided Flow:** Provide explicit UI modal when a newly registered user logs in without an associated company profile.

### P3 — Future Features (Do not start yet)
1. Phase 7: WhatsApp/Email notifications, PDF export, ML price-to-win modeling.

---

## 8. Track A — Intelligence / Production Closure

Track A represents the engineering path required to achieve final production sign-off. The following table verifies the actual state of each Track A hypothesis against repository evidence:

| Hypothesis / Area | Verified State | Repository Evidence | Remaining Task |
| :--- | :--- | :--- | :--- |
| **1. Real DB Migration State** | **VERIFIED (Local)** / **PENDING (Prod)** | SQLite: all applied (`showmigrations`). Prod: unapplied. | Run `migrate --noinput` on prod DB |
| **2. Participant Data Flow** | **VERIFIED** | 3,101 participants in local DB; `_parse_participants` captures name + NPWP | Execute crawl on prod |
| **3. Winner Data Flow** | **VERIFIED** | 1 live winner in local DB; `/evaluasi/{id}/pemenang` extracts 3 prices + address | Execute crawl on prod |
| **4. Entity Resolution** | **VERIFIED** | `services/entity_resolution.py` enforces exact match; 0 fuzzy auto-merges | None (operational) |
| **5. AI Matching in Real Data** | **VERIFIED** | 11 `AIMatchResult` in local DB; RuleBased zero-cost fallback functional | Verify LLM keys on prod |
| **6. Opportunity Scoring** | **VERIFIED** | 13 `OpportunityScore` in local DB; v0.1 calculation deterministic | Trigger on prod |
| **7. AI Fallback Provenance** | **VERIFIED** | `is_fallback`, `fallback_reason` persisted in DB and JSON | Expose visual badge in detail modal |
| **8. Scheduler Daemon** | **VERIFIED** | `python manage.py run_scheduler` command tested & operational | Configure prod service |
| **9. End-to-End Persistence** | **VERIFIED** | Live crawl of `acehbaratkab` persisted end-to-end | Run on prod |
| **10. Final Phase 6.4 Prod GO**| **CONDITIONAL GO** | Code approved; blocked on prod DB migration & Bug G | Execute deployment runbook |

---

## 9. Dashboard UX Status

The Dashboard UX is **IMPLEMENTED & VERIFIED**. It is not "Not Started" or "In Progress".

### Verified Deliverables & Test Evidence
- **UX-001 (Table Boundaries):** `dashboard.html:640-665` (Clean table grid, horizontal overflow handling).
- **UX-002 (KBLI Sanitization):** `dashboard.html:2209-2218` (`esc()` escaping on KBLI code and name).
- **UX-003 (First Viewport Cleanup):** Visual hierarchy reorganized to prioritize high-value insights.
- **UX-004 (Tender Detail Modal):** `dashboard.html:1044-1147` (2-column grid, schedules, qualifications, competitive intelligence).
- **UX-005 (Role-Gating):** `dashboard.html:320-424` (Operational controls restricted to Superadmin). Verified by `TestUX005SidebarRoleGating`.
- **UX-006 (Profile Completion Banner):** `dashboard.html:48-78` & `dashboard.html:1845-1910`. Verified by `TestUX006ProfileCompletionBanner`.
- **UX-007 (Hierarchy Flip):** `dashboard.html:48-180` (Intelligence Bar & Radar in primary view).
- **P2 (Mobile Responsive Pass):** `dashboard.html:668-680`, `toggleMobileSidebar()`, `results-cards-mobile`. Verified by `TestP2MobileResponsivePass`.
- **A11y & Microcopy Pass:** `dashboard.html:24` (`:focus-visible`), `dashboard.html:3790-3840` (`handleEscapeKey`), WAI-ARIA dialog attributes, WCAG AA contrast. Verified by `TestA11yMicrocopyPass`.

---

## 10. What Must NOT Be Started Yet

To prevent distraction from production closure, the following tasks are explicitly marked **DO NOT START YET**:

1. **DO NOT perform additional frontend visual redesigns:** The dashboard UX is verified, responsive, role-gated, and accessible. Further UI tweaks are unnecessary.
2. **DO NOT add new AI matching providers or scoring algorithms:** The existing provider abstraction (RuleBased, OpenAI, Gemini, Anthropic) and Opportunity Scorer v0.1 are complete and verified.
3. **DO NOT implement Phase 7 external notification channels (WhatsApp / Telegram / Email):** Notifications depend on an active, stable production crawler baseline.
4. **DO NOT implement automated fuzzy entity resolution:** Merging companies by name alone is strictly forbidden by architectural rules.
5. **DO NOT rewrite or refactor PRD / ARD from scratch:** Documentation updates must follow the reconciliation plan in Section 13.

---

## 11. Recommended Development Sequence

The project must follow this sequential path to achieve production deployment:

```text
STEP 1: Workspace Bug Fix (Bug G)
   │  Fix frontend company ID binding in dashboard.html (lines 2300-2306).
   │  Verify via automated test that non-superadmins without company_id are not bound to items[0].
   ▼
STEP 2: Automated Regression Verification
   │  Run full test suite: ensure 473+ tests pass with 0 failures.
   │  Run Playwright headless check on qualification form binding.
   ▼
STEP 3: Production Maintenance Window Execution
   │  Execute docs/production_migration_runbook.md (v2.0):
   │  1. Take PostgreSQL pre-migration backup.
   │  2. Run python manage.py migrate --noinput (applies 0003, 0014, 0015, 0002, 0003).
   │  3. Verify migration status via showmigrations.
   ▼
STEP 4: Deploy Container Services
   │  Deploy spse-web (Gunicorn WSGI, HTTP only).
   │  Deploy spse-scheduler (Single-process daemon: run_scheduler).
   │  Verify container liveness and health endpoints.
   ▼
STEP 5: Production Smoke Test & Live Crawl Verification
   │  Execute controlled crawl on 1 instansi (e.g. acehbaratkab).
   │  Verify persistence of TenderResult, TenderParticipant, and TenderWinner.
   │  Verify non-superadmin qualification mutation returns 200 OK (not 403).
   ▼
PRODUCTION GO
```

---

## 12. Production GO Criteria

All of the following criteria must be satisfied to declare full **PRODUCTION GO**:

- [x] Full automated test suite passing (&ge;473 tests, 0 failures).
- [x] Fail-fast production settings verified (`DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DATABASE_URL`).
- [x] History-safe retention gate verified (`flush_non_retained`).
- [x] Conservative entity resolution verified (0 fuzzy auto-merges).
- [x] Single-process scheduler daemon command created (`run_scheduler`).
- [x] Dashboard role-gating, mobile responsiveness, and WAI-ARIA accessibility verified.
- [ ] **CRITERION 1:** Frontend company ID binding bug in `dashboard.html` fixed and tested locally.
- [ ] **CRITERION 2:** Production PostgreSQL database successfully migrated to latest schema.
- [ ] **CRITERION 3:** Dedicated `spse-scheduler` container deployed in production environment.
- [ ] **CRITERION 4:** Live crawl on production successfully stores participants and winners without error.
- [ ] **CRITERION 5:** Company qualification CRUD verified in production browser without 403 Forbidden.

---

## 13. Documentation Reconciliation Plan

| Document | Current Inconsistency / Stale Information | Required Change | Priority | Action |
| :--- | :--- | :--- | :--- | :--- |
| `docs/prd.md` | Version v0.0.3 (2026-08-28). Describes only Phases 1–3; cites 265 tests; lacks Phase 6A–6E, Phase 6.1–6.8, and UX passes. | Update to v0.0.6. Incorporate Phase 6 (Winner/Retention/Resolution) and UX-001–007 / P2 / A11y acceptance criteria. | High | UPDATE LATER |
| `docs/PROJECT_CONTEXT.md` | Version v0.0.5. Cites 265 tests; marks deploy NOT DONE; lists DB state as 0 participants / 0 winners. | Update test baseline (473 passed), record counts (823 tenders, 3,101 participants), and current phase status. | High | UPDATE LATER |
| `docs/adr.md` | Last updated for v0.0.3. Missing architectural records for single-process scheduler daemon and conservative entity resolution. | Add ADR-007 (Single-Process Scheduler Daemon Pattern) and ADR-008 (Exact-Only NPWP/NIB Entity Resolution). | Medium | UPDATE LATER |
| `README.md` | Version v0.0.4. Lacks Phase 6 intelligence features, role-gated operational controls, and updated test counts. | Update version badge, document `run_scheduler` command, and summarize responsive/A11y capabilities. | Medium | UPDATE LATER |
| `docs/DEPLOYMENT.md` | References legacy in-memory scheduler execution under Gunicorn workers. | Update deployment instructions to reference `docs/production_migration_runbook.md` v2.0. | Medium | UPDATE LATER |
| `docs/implementation_report_phase_6_6.md` | Empty 0-byte file created accidentally. | Retain as placeholder or document as empty pass. | Low | DO NOT TOUCH YET |
| `docs/implementation_report_phase_6_7.md` | Empty 0-byte file created accidentally. | Retain as placeholder or document as empty pass. | Low | DO NOT TOUCH YET |

---

## 14. Risks & Unknowns

1. **Production LPSE Network Latency / Cloudflare Rate Limiting:**
   - *Risk:* Heavy concurrent Playwright scraping on production may trigger IP-level rate limiting or Cloudflare challenges from specific LPSE instances.
   - *Mitigation:* `stealth` plugin is active; default concurrency configured conservatively to 3 workers; session warming enabled.
2. **Production DB Migration Timing:**
   - *Risk:* Running migrations on production with active crawler execution could lock tables.
   - *Mitigation:* Migration runbook mandates stopping web and crawler containers before executing `migrate --noinput`.
3. **Multi-Tenant User Profile Association:**
   - *Risk:* If external users register without completing company onboarding, their `user.company_id` will be null.
   - *Mitigation:* Frontend fix (Step 1 of Roadmap) explicitly handles `user.company_id == null` gracefully and prompts profile creation.

---

## 15. Evidence Index

| Claim / Artifact | Verified Location in Repository | Verification Method |
| :--- | :--- | :--- |
| **Test Suite Baseline (473 passed)** | `spse_crawler/web/test_security.py` | Pytest run (`473 passed in 16.03s`) |
| **Two-Stage Crawler** | `spse_crawler/parsers/discovery.py`, `detail.py` | Live crawl test (`implementation_report_phase_6_2.md`) |
| **History-Safe Purge** | `spse_crawler/services/purger.py:flush_non_retained` | Unit tests (`test_purger.py`) & live SQLite run |
| **Winner Scraper & URL Builder** | `spse_crawler/parsers/detail.py:_build_winner_url` | Unit tests (`test_tender_winner.py`) |
| **Entity Resolution Logic** | `spse_crawler/services/entity_resolution.py` | Unit tests (`test_entity_resolution.py`) |
| **Scheduler Daemon Command** | `spse_crawler/web/management/commands/run_scheduler.py` | Unit tests (`test_phase64_hardening.py`) |
| **AI Fallback Provenance** | `spse_crawler/ai_match/models.py:AIMatchResult` | Schema inspection & migration `ai_match/0002` |
| **Role-Gating Implementation** | `spse_crawler/web/templates/dashboard.html:320` | Django test client (`TestUX005SidebarRoleGating`) |
| **Profile Completion Meter** | `spse_crawler/web/templates/dashboard.html:48` | Django test client (`TestUX006ProfileCompletionBanner`) |
| **Mobile Drawer & Cards** | `spse_crawler/web/templates/dashboard.html:668` | Django test client (`TestP2MobileResponsivePass`) |
| **A11y WAI-ARIA & Escape Key** | `spse_crawler/web/templates/dashboard.html:3790` | Django test client (`TestA11yMicrocopyPass`) |
| **Production Migration Runbook** | `docs/production_migration_runbook.md` (v2.0) | File inspection |
| **Server 403 Root Cause** | `docs/implementation_report_phase_6_8.md` | Live browser forensic on `monitor-lpse.khansia.co.id` |

---

## 16. Final Decision

### **PROJECT STATUS: CONDITIONAL GO**

**Core Conclusion:**  
The engineering codebase of `scrap_lpse` is **feature-complete, robustly tested (473 passed), role-gated, and accessible**. The frontend mega-SPA has been successfully restructured to emphasize intelligence while maintaining strict safety boundaries for business users.

**Immediate Next Action:**  
Proceed to **Track A: Production Closure**:
1. Apply the 6-line fix in `dashboard.html:loadCompanyData()` to resolve Bug G (company binding fallback).
2. Verify resolution with an automated test.
3. Execute the production maintenance window per `docs/production_migration_runbook.md` (v2.0) to migrate production PostgreSQL and deploy the `run_scheduler` daemon.

