# Project Context (v0.0.5)

## 1. Project Overview

| Field | Value |
|---|---|
| **Project Name** | SPSE Inaproc Multi-Tenant Scraper & Intelligence Dashboard |
| **Version** | 0.0.5 |
| **Previous Version** | 0.0.4 (Dashboard Enhancement, Detail Modal, Lazy Playwright) |
| **Objective** | Full-stack tender scraping system with AI matching, Opportunity Score, Tender Radar, Intelligence readiness, multi-tenant auth, submission workflow |

**Current test state: 265/265 PASS · 0 regression · Deploy: NOT DONE**

---

## 2. Readiness Matrix

> Classification: **READY** (implemented + tested + approved) · **UNDER DEVELOPMENT** (code present, not formally activated/approved) · **CURRENT DEVELOPMENT** (active focus) · **PLANNED** (future).

| Ref | Deliverable | Status |
|---|---|---|
| Base | Core crawler (2-stage, stealth, KBLI master, IT score, scheduler, flush/purge) | READY |
| Base | Multi-tenant auth (accounts.User) + Company/User + AI Match + submission + audit | READY |
| Fix | Security/CSRF hardening (F-01, F-02), `@csrf_exempt` 10→8 | READY |
| **Phase 1** | **Opportunity Score v0.1** (`opportunity_scorer_v01.py`, weights, classification, idempotent) | **READY** (64 tests) |
| **Phase 2** | **Tender Radar** (`tender_radar.py`, ranked discovery, company-isolated) | **READY** (25 tests) |
| **Phase 3** | **Intelligence Status / Readiness** (`intelligence_status.py`, API + UI chip) | **READY** (20 tests) |
| **PD-1** | **Automated Intelligence Pipeline** (queue/job, daily-usage rate limit, scheduler tick, crawler hook) | **UNDER DEVELOPMENT** (built, NOT activated) |
| **Phase 5** | **Competitive Intelligence Data Foundation** (`TenderParticipant`/`TenderWinner`, `/peserta` name+NPWP capture, awarded-stage retention gate, idempotent store) | **READY** (29 tests) |
| Future | Competitor / Winner / Win-Probability / Price Intelligence | PLANNED |
| Future | Daily Briefing, WhatsApp/Telegram/Email notification | PLANNED |

---

## 3. Tech Stack & Infrastructure

| Component | Technology | Notes |
|---|---|---|
| Language | Python 3.10+ | |
| Crawler Engine | httpx (HTTP) + Playwright (browser) | stealth JS + human simulation |
| Parsing | selectolax (fast), BeautifulSoup4 (fallback) | |
| Data Modeling | Pydantic v2 | scrape schemas + `SPSE_*` config |
| Web Framework | Django 5.2 | custom `accounts.User` |
| Task Scheduling | APScheduler 3.10 | hourly crawl + pipeline tick (tick not fired) |
| Database | PostgreSQL 15+ (prod) / SQLite (dev) | remote `DATABASE_URL` in prod |
| LLM | OpenAI / Gemini / Anthropic / RuleBased | 100% free tier: Sumopod → Gemini → RuleBased |
| WSGI / Proxy | Gunicorn / Nginx | |
| Container | Docker + Docker Compose | `env_file: - .env` |
| CLI / Logging | Typer + Rich / loguru | |

---

## 4. Core Architecture Principles

1. **Two-Stage Crawling** — Discovery (DataTables XHR) → Detail (Playwright).
2. **Resilience & Stealth First** — anti-detection, rate limit, retry w/ backoff.
3. **Strict Normalization** — lowercase, stripped, regex tahap categories.
4. **Idempotent Re-crawls** — `update_or_create` on `(kode_instansi, id_lelang)`.
5. **Zero-Regression Migration** — every old component isolated from additions; **aditive-only schema**.
6. **Fail-Safe AI** — AI match is best-effort, never raises (returns score 0 + `"AI analysis failed:"` marker).
7. **Multi-Tenant by Convention** — company isolation at query level; new endpoints company-scoped, superadmin-only `company_id` override.
8. **Deterministic Intelligence** — Opportunity Score & Radar & Status are read-only/offline (no LLM, no auto-scoring on view); `calculation_version="v0.1"`.

---

## 5. Auth & App Layout (corrected)

```
spse_crawler/
+-- accounts/                      # NEW: custom User, EmailBackend, RoleMiddleware, decorators
+-- companies/                     # NEW: CompanyProfile, CompanyQualification
+-- submissions/                   # NEW: TenderSubmissionStatus
+-- ai_match/                      # NEW: AIMatchResult, matcher.py, providers.py, prompts.py
+-- audit/                         # NEW: AuditLog
+-- config/                        # EXISTING: Settings, InstansiConfig (+ pipeline policy fields)
+-- core/                          # EXISTING: Browser Engine, Exceptions
+-- parsers/                       # EXISTING: Discovery, Detail parsers
+-- models/                        # EXISTING: Pydantic scrape schemas
+-- services/                      # EXISTING + NEW:
    #   priority_scorer, progress_tracker, purger
    #   opportunity_scorer (legacy/deprecated), opportunity_scorer_v01
    #   tender_radar, intelligence_status, intelligence_eligibility, intelligence_pipeline
+-- storage/                       # EXISTING: CSV/Excel exporter
+-- tasks/                         # EXISTING: placeholder
+-- web/                           # EXISTING: Dashboard views, models, scheduler
    #   models.py: TenderResult, CrawlJob, OpportunityScore, IntelligenceJob, IntelligenceDailyUsage
    #             + TenderParticipant, TenderWinner (Phase 5 CI data foundation)
+-- cli.py                         # EXISTING: Typer CLI
+-- main.py                        # EXISTING: Crawl entrypoint
```

---

## 6. Isolation Strategy (base — protecting existing engine)

### 6.1 ZERO CHANGES (frozen)

| Component | Notes |
|---|---|
| `parsers/discovery.py`, `parsers/detail.py`, `core/browser.py`, `core/exceptions.py`, `models/tender.py` | frozen |
| `services/priority_scorer.py`, `progress_tracker.py`, `purger.py` | frozen |
| `ai_match/matcher.py`, `providers.py`, `prompts.py` | frozen |
| `opportunity_scorer_v01.py` (formula/weights), `tender_radar.py` (ranking) | frozen |
| Deploy config (`.env`, `docker-compose.yml`, `Dockerfile`, `entrypoint.sh`, `deploy.sh`, `web_ui/settings.py`) | NOT MODIFIED |

### 6.2 Additive-Only (new)

| Component | Addition |
|---|---|
| `web/views.py` | `api_opportunity` (extended), `api_radar`, `api_intelligence_status`, `api_intelligence_reprocess` |
| `web/urls.py` | `/api/opportunity/<id>/`, `/api/radar/`, `/api/intelligence/status/`, `/api/intelligence/reprocess/` |
| `web/models.py` | `OpportunityScore`, `IntelligenceJob`, `IntelligenceDailyUsage`, `TenderParticipant`, `TenderWinner` (Phase 5) |
| `services/tender_participant_store.py` | idempotent participant upsert store (Phase 5); wired into `cli.py`, `web/scheduler.py`, `web/views.py` |
| `parsers/detail.py` | `/peserta` name+NPWP capture + awarded-stage retention gate (`is_retained_tahap`) — Phase 5 enrichment |
| `web/migrations/` | `0011_add_opportunity_score`, `0012_intelligencedailyusage_intelligencejob`, `0013_tenderwinner_tenderparticipant_and_more` (applied) |
| `web/scheduler.py` | pipeline tick registered ADDITIVELY (`next_run_time=None`, not fired) + crawler hook |
| `web/templates/dashboard.html` | TENDER RADAR section, Opportunity modal, intelligence chip, `loadIntelligenceStatus()` |
| `web_ui/settings.py` | `AUTH_USER_MODEL="accounts.User"` (standalone table, no `user_ptr_id` FK) |
| `config/settings.py` | `SPSE_AI_*` pipeline fields |

> **Critical rule:** No existing URL pattern, view function, or model field is modified or removed.

---

## 7. Domain Models

### 7.1 Core (existing)
- `KbliMaster`, `TenderResult`, `CrawlJob`

### 7.2 Auth / Company / Workflow
- `accounts.User` (abstract-user based, login by **email**)
- `CompanyProfile`, `CompanyQualification` (companies)
- `AIMatchResult` (ai_match)
- `TenderSubmissionStatus` (submissions)
- `AuditLog` (audit)

### 7.3 Intelligence (v0.0.5)
- **`OpportunityScore`** — per `(tender, company)` unique; `calculation_version="v0.1"`; `status in ready|not_ready|stale`.
- **`IntelligenceJob`** — queue; unique `(tender, company, job_type)`; statuses `pending|processing|completed|failed|skipped`; job types `AI_MATCH|OPPORTUNITY_SCORE`.
- **`IntelligenceDailyUsage`** — per-company daily AI counter (implements `AI_MAX_MATCHES_PER_DAY`).

### 7.4 Competitive Intelligence Data Foundation (Phase 5 — READY)
- **`TenderParticipant`** — a company that participated in a tender; `(tender, name)` unique; captures `name` + external `npwp` from the SPSE `/peserta` page with provenance (`source_url`, `source_type`, `source_fetched_at`). External identity only — **not** auto-linked to `CompanyProfile`.
- **`TenderWinner`** — `OneToOne(tender)`; data foundation only (not populated — no winner parser; awarded-page HTML not demonstrably parseable; rows never fabricated).
- **Idempotent sync** — `services/tender_participant_store.sync_tender_participants()` upserts on `(tender, name)`, never deletes history, preserves existing rows when a re-crawl returns no data.
- **Crawler enrichment** — `/peserta` page now captures participant name + NPWP (single fetch); awarded/completed tenders (`kontrak`, `penetapan pemenang`, `selesai`, etc.) are retained instead of dropped by the Stage-2 gate (`is_retained_tahap`), while aborted tenders (`pembatalan`/`batal`/`gagal`) remain skipped.

---

## 8. Opportunity Score v0.1 (Phase 1 — READY)

- **Source:** `services/opportunity_scorer_v01.py` → `OpportunityScorerV01.calculate(tender_id, company_id)`.
- Weights: qual 35% / fin 20% / exp 20% / deadline 15% / strategic 10%.
- Missing component → weight redistributed (not zeroed).
- Prerequisite AI Match; if absent → `not_ready`, NO fabricated score.
- Classification: `PRIORITAS_TINGGI ≥90`, `LAYAK_DIKEJAR ≥75`, `REVIEW ≥60`, `RISIKO_TINGGI ≥40`, `TIDAK_DIREKOMENDASIKAN <40`.
- Idempotent `update_or_create`; legacy `opportunity_scorer.py` deprecated (docstring only).
- API: `GET /api/opportunity/<tender_id>/` (backward-compatible).

## 9. Tender Radar (Phase 2 — READY)

- **Source:** `services/tender_radar.py` → `TenderRadarService.get_radar(company_id, filters, limit, offset)`.
- Read-only over persisted data; no LLM/auto-calc; prefetch (no N+1).
- Excludes terminal stages; requires `AIMatchResult`; `radar_status` READY (persisted ready score) / NOT_READY.
- Rank: classification priority DESC → score DESC → deadline urgency.
- API: `GET /api/radar/`; UI: collapsible Tender Radar section with filter chips, IT chip, deadline urgency.

## 10. Intelligence Status / Readiness (Phase 3 — READY)

- **Source:** `services/intelligence_status.py` → `compute_status(company_id)`.
- Levels: `READY` (full 100% coverage) / `PARTIAL` (<100%) / `NOT_READY` (no tender/AI/ready score) / `EMPTY` (no company at API level).
- Pure aggregate COUNT (no N+1); read-only; no LLM/score/mutation (tested).
- Endpoint: `GET /api/intelligence/status/`; UI: readiness chip + coverage pct, `loadIntelligenceStatus()` init + 30s poll.

## 11. Automated Intelligence Pipeline (PD-1 — UNDER DEVELOPMENT, NOT ACTIVATED)

- **Source:** `services/intelligence_pipeline.py` (executor), `services/intelligence_eligibility.py` (pre-filter).
- Built + migrated (`0012_*`) but the scheduler job is `next_run_time=None`; **no mass LLM triggered**.
- Features (in code): persistent queue, day budget, bounded batch, idempotent, retry+backoff (3 attempts/15 min), stale recovery (30 min), company isolation, failure-safe.
- Reuses `ai_match.matcher.run_match` + `opportunity_scorer_v01.calculate` — NO second AI/scorer implementation.
- Config: `SPSE_AI_PIPELINE_BATCH_SIZE`, `SPSE_AI_PIPELINE_MAX_ATTEMPTS`, `SPSE_AI_PIPELINE_RETRY_DELAY_MINUTES`, `SPSE_AI_PIPELINE_STALE_MINUTES`, `SPSE_AI_MAX_MATCHES_PER_DAY`.

> **Retry note (for activation):** `run_match` never raises on LLM failure (returns fit_score 0 + `"AI analysis failed:"` summary, persists zero result with same `cache_key` — blocking retry). When PD-1 is activated, executor must detect the failure marker and delete the failed record before re-invoking.

---

## 12. Security & Deployment

### 12.1 Security
- Auth by email (PBKDF2, session 24h); `AUTH_USER_MODEL = "accounts.User"`; `accounts_user` standalone (no `user_ptr_id` FK).
- Roles: superadmin / company_admin / submitter via `RoleMiddleware`.
- Company isolation enforced in every new endpoint; `company_admin`/`submitter` cannot select other company; only superadmin may pass `company_id`.
- `@csrf_exempt` reduced 10→8; new endpoints have none.
- F-01 (`api_company_delete`) and F-02 (`api_submission_update`) FIXED.

### 12.2 Deployment (NOT DONE)
- Prod: `https://monitor-lpse.khansia.co.id/` · CentOS 7 · PostgreSQL on separate server · docker-compose `env_file: - .env`.
- Deploy config unchanged; no deploy until approval.

---

## 13. Next Steps

1. Verify docs (ADR 0.0.3, PRD 0.0.3, this context 0.0.5) — docs-only, no code change.
2. Obtain approval to proceed with **PD-1 activation** (enable scheduler tick, enqueue on crawl, retry/delete failed markers, daily budget enforcement).
3. After PD-1 → planned intelligence features (Competitor / Winner / Win-Probability / Price) and notifications (Daily Briefing, WhatsApp/Telegram/Email).
