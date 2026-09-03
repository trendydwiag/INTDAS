# Architecture Requirement Document (ARD) v0.0.3

## Project: SPSE Inaproc Smart Tender Matcher & Multi-Role Workflow System

### 1. Document Control

| Field | Value |
|---|---|
| **Version** | 0.0.3 |
| **Status** | Implemented — Multiple Phases Approved |
| **Previous Version** | ARD v0.0.2 |
| **Last Updated** | 2026-08-28 |

---

### 2. Current Architecture Summary (as of v0.0.3)

```
+--------------------------------------------------------------------+
|                      PRESENTATION LAYER                             |
|  login.html | dashboard.html (single-page, Tailwind CSS, JS)        |
|  15 original endpoints + auth + match + opportunity + radar +      |
|  intelligence/status endpoints                                     |
+----------------------------+---------------------------------------+
                             |
+----------------------------v---------------------------------------+
|                     AUTH LAYER                                      |
|  Custom User (accounts) + RoleMiddleware + Session auth            |
|  Roles: superadmin | company_admin | submitter                     |
|  EmailBackend (login by email)                                     |
+----------------------------+---------------------------------------+
                             |
+----------------------------v---------------------------------------+
|                       SERVICE LAYER                                 |
|  priority_scorer.py | progress_tracker.py | purger.py              |
|  scheduler.py (APScheduler: hourly crawl + pipeline tick)          |
|  opportunity_scorer.py (legacy) | opportunity_scorer_v01.py        |
|  tender_radar.py | intelligence_status.py                          |
|  intelligence_eligibility.py | intelligence_pipeline.py            |
+----------------------------+---------------------------------------+
                             |
+----------------------------v---------------------------------------+
|                       CRAWLER ENGINE                               |
|  parsers/discovery.py (Stage 1) + parsers/detail.py (Stage 2)      |
|  core/browser.py (SpseHttpClient + PlaywrightEngine stealth)      |
+----------------------------+---------------------------------------+
                             |
+----------------------------v---------------------------------------+
|                        DATA LAYER                                   |
|  Django ORM -> PostgreSQL (prod) / SQLite (dev)                    |
|  Models: KbliMaster, TenderResult, CrawlJob, OpportunityScore,     |
|          IntelligenceJob, IntelligenceDailyUsage, TenderWatchlist  |
|          (web) + User (accounts) + CompanyProfile,                 |
|          CompanyQualification (companies) + AIMatchResult (ai_match)|
|          + TenderSubmissionStatus (submissions)                    |
+--------------------------------------------------------------------+
```

---

### 3. Phase Roadmap & Readiness Matrix

> Classification: **READY** (implemented, tested, approved) · **UNDER DEVELOPMENT** (code exists but not formally activated/approved) · **CURRENT DEVELOPMENT** (active focus) · **PLANNED** (future, not started).

| Ref | Deliverable | Status | Tests |
|---|---|---|---|
| Base | Core crawler (2-stage, stealth, KBLI, IT score, scheduler, flush) | READY | — |
| Base | Multi-tenant auth + Company/User + AI Match + Submission + Audit | READY | — |
| Fix | Security/CSRF hardening (F-01, F-02), `@csrf_exempt` reduced | READY | — |
| **Phase 1** | **Opportunity Score v0.1** (weights, classification, idempotent) | **READY** | 64 |
| **Phase 2** | **Tender Radar** (ranked, company-isolated discovery UI + API) | **READY** | 25 |
| **Phase 3** | **Intelligence Status / Readiness** (`intelligence_status.py` + API + UI) | **READY** | 20 |
| PD-1 | Automated Intelligence Pipeline infra (IntelligenceJob queue, daily usage, scheduler tick, crawler hook) | UNDER DEVELOPMENT | infra present |
| Future | Competitor / Winner / Win-Probability / Price Intelligence | PLANNED | — |
| Future | Daily Briefing, WhatsApp / Telegram / Email notification | PLANNED | — |

**Full test suite baseline: 265/265 PASS · REGRESSION: 0 · DEPLOY: NOT DONE**

---

### 4. Opportunity Score v0.1 (Phase 1 — READY)

**Source of truth:** `services/opportunity_scorer_v01.py` (class `OpportunityScorerV01.calculate(tender_id, company_id)`).

#### 4.1 Formula (FINAL)

| Component | Weight |
|---|---|
| Qualification | 35% |
| Financial | 20% |
| Experience | 20% |
| Deadline | 15% |
| Strategic | 10% |

- Prerequisite: `AIMatchResult` must exist for `(tender, company)`.
- If `AIMatchResult` missing → returns `NOT_READY` state (no fallback scoring, no fabricated score).
- Missing component → weight redistributed among present components (not zeroed).
- Idempotent: uses `update_or_create` on `(tender, company)` — never duplicates.

#### 4.2 Classification Thresholds

| Label | final_score |
|---|---|
| `PRIORITAS_TINGGI` | ≥ 90 |
| `LAYAK_DIKEJAR` | ≥ 75 |
| `REVIEW` | ≥ 60 |
| `RISIKO_TINGGI` | ≥ 40 |
| `TIDAK_DIREKOMENDASIKAN` | < 40 |

#### 4.3 Persistence

- Model: `web.OpportunityScore`, `unique_together (tender, company)`.
- Field `calculation_version = "v0.1"`.
- Status in `ready | not_ready | stale`.

---

### 5. Tender Radar (Phase 2 — READY)

**Source of truth:** `services/tender_radar.py` (`TenderRadarService.get_radar`).

- Read-only over persisted `OpportunityScore` + `AIMatchResult`. **No LLM, no auto-calculation, no N+1** (prefetched persisted scores).
- Eligibility: excludes terminal stages (`selesai/kontrak/pembatalan/dibatalkan/gugur`); only tenders with `AIMatchResult`.
- `radar_status`: `READY` (persisted `OpportunityScore.status == "ready"`) · `NOT_READY` (has AI match but no ready score).
- Ranking: classification priority (PRIORITAS_TINGGI=5 … NOT_READY=0) DESC → Opportunity Score DESC → deadline urgency (sooner higher). READY always above NOT_READY.
- Filters: classification, min_score, only_ready/include_not_ready, search, kbli_code, location, priority(it), deadline_before/after, hps_min/max.

**API:** `GET /api/radar/` (auth, company-scoped; superadmin may pass `?company_id=`; ordinary users cannot).

---

### 6. Intelligence Status / Readiness (Phase 3 — READY)

**Source of truth:** `services/intelligence_status.py` (`compute_status(company_id)`).

Deterministic, read-only, company-scoped. Uses aggregate COUNT queries (no N+1). **Never calls an LLM, never scores, never mutates, never creates matches/scores.**

#### 6.1 Readiness Level

| Level | Condition |
|---|---|
| `NOT_READY` | no active tender, OR no AI match, OR no ready Opportunity Score |
| `READY` | tender + AI match + ready score present **and** full (100%) coverage |
| `PARTIAL` | data present but coverage incomplete (< 100%) |
| `EMPTY` | authenticated user with no company (API level) |

Coverage = `company AI matches / active tenders × 100` and `company ready scores / active tenders × 100`. Terminal stages excluded.

**API:** `GET /api/intelligence/status/` (GET-only, authenticated, company-scoped, no `@csrf_exempt`, no LLM/score/mutation).

---

### 7. Automated Intelligence Pipeline Infra (PD-1 — UNDER DEVELOPMENT)

**Source of truth:** `services/intelligence_pipeline.py`, `services/intelligence_eligibility.py`.

Built into the codebase but **not formally activated** (the APScheduler pipeline job is registered with `next_run_time=None`; no LLM/mass processing triggered).

- **Queue:** `web.IntelligenceJob` — persistent, `unique_together (tender, company, job_type)`, statuses `pending | processing | completed | failed | skipped`, job types `AI_MATCH | OPPORTUNITY_SCORE`.
- **Budget:** `web.IntelligenceDailyUsage` — per-company daily AI execution counter (implements previously-documented-but-unimplemented `AI_MAX_MATCHES_PER_DAY=100`).
- **Eligibility:** `intelligence_eligibility.is_eligible(tender, company)` — deterministic; skips terminal/expired/insufficient-text tenders and inactive/no-qualification companies.
- **Executor:** `run_cycle()` — bounded (batch_size=10), idempotent, retry w/ backoff (max_attempts=3, 15-min base), stale-processing recovery (30-min), company-isolated, failure-safe. Reuses `ai_match.matcher.run_match` and `opportunity_scorer_v01.calculate` — no second AI/scoring implementation.
- **Config:** `spse_crawler/config/settings.py` pydantic `SPSE_` fields: `ai_pipeline_batch_size`, `ai_pipeline_max_attempts`, `ai_pipeline_retry_delay_minutes`, `ai_pipeline_stale_minutes`, `ai_max_matches_per_day`.
- **Enqueue hooks:** after crawl completion (`_do_crawl` scheduled, `_execute_crawl` manual) via `enqueue_new_after_crawl()`.
- **Migration:** `web/0012_intelligencedailyusage_intelligencejob` (applied).

> Note: run_match never raises on LLM failure (returns fit_score=0 + summary prefix `"AI analysis failed:"` and persists the zero result). Enabling correct retry requires the pipeline to detect the marker and delete the failed record so a retry re-invokes the LLM. This is reserved for when PD-1 is activated.

---

### 8. Security Architecture

#### 8.1 Authentication Flow

```
Browser -> GET /login/ -> login.html form
Browser -> POST /login/ -> Django authenticate(email, password)
    |-> Success -> Session cookie set -> Redirect to /
    |-> Failure -> Error message -> Re-render login.html

Browser -> GET / -> RoleMiddleware checks session
    |-> No session -> Redirect to /login/
    |-> Has session -> Render dashboard with role context
```

#### 8.2 Authorization & Company Isolation

- Custom `User` (accounts) extends `AbstractUser`; login by `email` via `EmailBackend`.
- `RoleMiddleware` attaches `user_role` / `user_company`.
- **Rules enforced across all new APIs (opportunity, radar, status, reprocess):**
  - `superadmin` may pass `?company_id=` to inspect another company.
  - `company_admin` / `submitter` are ALWAYS scoped to their own company; a passed `company_id` is ignored.
  - No `@csrf_exempt` on new endpoints.
  - No horizontal privilege escalation / no leaking another company's data or existence.

#### 8.3 CSRF Hardening (READY)

- `@csrf_exempt` reduced from 10 → 8 endpoints (remaining 8 accepted as LOW risk GET-only/public helpers).
- Security findings F-01 (`api_company_delete`) and F-02 (`api_submission_update`) FIXED.

---

### 9. File Structure (current)

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
+    #   priority_scorer, progress_tracker, purger
+    #   opportunity_scorer (legacy/deprecated), opportunity_scorer_v01
+    #   tender_radar, intelligence_status, intelligence_eligibility, intelligence_pipeline
+-- storage/                       # EXISTING: CSV/Excel exporter
+-- tasks/                         # EXISTING: placeholder
+-- web/                           # EXISTING: Dashboard views, scheduler, models
+    #   models.py (TenderResult, CrawlJob, OpportunityScore,
+    #               IntelligenceJob, IntelligenceDailyUsage)
+    #   scheduler.py (hourly crawl + pipeline tick)
+-- cli.py                         # EXISTING: Typer CLI
+-- main.py                        # EXISTING: Crawl entrypoint
```

---

### 10. Environment Variables (current)

```bash
# AI Provider Configuration
AI_PROVIDER=openai              # openai | gemini | anthropic | rule_based
AI_API_KEY=sk-...               # Provider API key
AI_MODEL=gpt-4o                 # Model name (provider-specific)
AI_MAX_MATCHES_PER_DAY=100      # Per-company daily AI match budget (implemented via IntelligenceDailyUsage)

# Auth Configuration
AUTH_LOGIN_REDIRECT=/           # After login
AUTH_LOGOUT_REDIRECT=/login/    # After logout
SESSION_COOKIE_AGE=86400        # 24 hours

# Intelligence Pipeline (PD-1 — under development)
SPSE_AI_PIPELINE_BATCH_SIZE=10
SPSE_AI_PIPELINE_MAX_ATTEMPTS=3
SPSE_AI_PIPELINE_RETRY_DELAY_MINUTES=15
SPSE_AI_PIPELINE_STALE_MINUTES=30
SPSE_AI_MAX_MATCHES_PER_DAY=100
```

---

### 11. Backward Compatibility Guarantees

| Component | Status |
|---|---|
| `parsers/discovery.py`, `parsers/detail.py` | ZERO CHANGES |
| `core/browser.py`, `core/exceptions.py`, `models/tender.py` | ZERO CHANGES |
| `services/priority_scorer.py`, `progress_tracker.py`, `purger.py` | ZERO CHANGES |
| `ai_match/matcher.py`, `providers.py`, `prompts.py` | ZERO CHANGES (formula/prompt untouched) |
| `opportunity_scorer_v01.py` | ZERO CHANGES (formula/weights untouched) |
| `tender_radar.py` | Ranking untouched |
| `scheduler.py` | Additive (added pipeline tick; crawl job unchanged) |
| `web/models.py` | Additive (added OpportunityScore, IntelligenceJob, IntelligenceDailyUsage) |
| Existing API contracts / auth / CSRF | Backward compatible |
| Deploy config, `.env`, Caddy | NOT MODIFIED |

**Critical rule: No existing URL pattern, view function, or model field is modified or removed.**
