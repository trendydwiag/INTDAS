# RELEASE SCOPE AUDIT — SCRAP LPSE
**Audit Date:** 7 September 2026  
**Auditor:** Agentic AI Assistant (Antigravity)  
**Evaluation Scope:** Local Working Tree vs. Repository Baseline vs. Production Branch (`origin/production-sync-20260904`)  
**Audit Purpose:** Factual release-scope determination to guide production commit and deployment decisions.

---

# 1. Executive Summary

A comprehensive, read-only audit of the `scrap_lpse` workspace was conducted on 7 September 2026. The inspection reconciled all uncommitted local modifications, commit histories, database migrations, runtime dependencies, and branch divergences against `origin/production-sync-20260904`.

**Current Release Assessment:**
1. **Single Production Runtime Candidate:** Only one file in the current working tree represents customer-facing production runtime code:
   `spse_crawler/web/templates/dashboard.html` (+1,956 / -548 lines).
2. **Zero Backend Runtime / Migration Blockers:** All backend APIs (26 distinct endpoints), Django views, serializers, models, and database migrations required by the updated dashboard are **already committed** and **present in both `HEAD` and `origin/production-sync-20260904`**. No new database migrations are required (`set()` difference).
3. **Local-Only Assets Isolated:** The 292 newly added test lines in `spse_crawler/web/test_security.py` and the agent configuration assets (`.agents/`, `AGENTS.md`, `skills-lock.json`, and `docs/PROJECT_STATUS_AND_ROADMAP.md`) are classified strictly as `LOCAL ONLY` and must not be included in the production release commit.
4. **Release Status: `BLOCKED` (P0 Blocker Discovered):** Deployment of `dashboard.html` to production is currently blocked by a critical tenant-binding logic defect in `loadCompanyData()` (`dashboard.html:2300-2306`), which allows non-superadmin users without an assigned company ID to bind to `items[0]` (Company 6 on production), resulting in cross-tenant data leakage in the form and `HTTP 403 Forbidden` failures on qualification mutations.
5. **Branch Divergence (P1 Blocker):** The production branch `origin/production-sync-20260904` is 2 commits ahead of `HEAD`, containing production-specific Docker and Gunicorn configuration adjustments (`Dockerfile`, `docker-compose.yml`, `docs/production_migration_runbook.md`, `gunicorn.conf.py`). Staging or deploying the release must avoid overwriting these production container configurations.

---

# 2. Repository Baseline

The repository baseline was inspected using Git plumbing and porcelain commands:

* **Workspace Directory:** `/Users/trendy/scrap_lpse`
* **Active Local Branch:** `main`
* **Current Local HEAD:** `8d20a43bd7b0fd573517ed2a54dc869dd66d8edf`  
  *Commit Subject:* `feat: harden AI matching and tender intelligence`
* **Remote Tracking Branch:** `origin/main` (at `8d20a43bd7b0fd573517ed2a54dc869dd66d8edf`)
* **Production Branch:** `origin/production-sync-20260904`
* **Production Branch HEAD:** `aa671a73a0b0c7f6f4956ec767b1637cd3273ea5`  
  *Commit Subject:* `merge production updates and resolve conflicts using theirs`
* **Merge-Base:** `8d20a43bd7b0fd573517ed2a54dc869dd66d8edf`  
  *Factual Finding:* `HEAD` is the exact merge-base. This mathematically proves that `origin/production-sync-20260904` is a direct descendant of `HEAD` and already incorporates all committed backend code, models, and migrations from `main`.
* **Working Tree State:** `DIRTY` (2 modified tracked files, 4 untracked paths).

---

# 3. Local Uncommitted Changes

Detailed inspection via `git status --short`, `git diff --stat`, and `git ls-files --others --exclude-standard`:

| Status | File Path | Scope Classification | Description |
| :--- | :--- | :--- | :--- |
| `M` | `spse_crawler/web/templates/dashboard.html` | `PRODUCTION` (Candidate) | Dashboard template redesign (+1,956 / -548 lines) covering UX-005, UX-006, UX-007, P2 Mobile, A11y, and Microcopy. |
| `M` | `spse_crawler/web/test_security.py` | `LOCAL ONLY` | Local automated test suite extension (+292 lines, 15 new test methods). |
| `??` | `.agents/` | `LOCAL ONLY` | Antislop agent skills and governance tools (untracked directory). |
| `??` | `AGENTS.md` | `LOCAL ONLY` | Local agent operational rules and guidelines. |
| `??` | `docs/PROJECT_STATUS_AND_ROADMAP.md` | `LOCAL ONLY` | Project baseline status and multi-phase roadmap documentation. |
| `??` | `skills-lock.json` | `LOCAL ONLY` | Agent tool dependency lockfile. |
| *N/A* | `docs.zip` | `DELETE` (Candidate) | Checked via filesystem inspection; file is absent / already eliminated. |

---

# 4. Dashboard Diff Audit

Inspection of `git diff spse_crawler/web/templates/dashboard.html` revealed 87 diff hunks totaling +1,956 insertions and -548 deletions. Every modification was categorized:

### 4.1 UX-005 Role Gating (`VERIFIED`)
* **Separation of Operational Controls:** Destructive and maintenance controls (Flush Data, Sync SPSE, Crawl Monitor, KBLI Admin Modal) were removed from the general navigation sidebar.
* **Server-Side Template Gating:** Enclosed within `{% if user.role == 'superadmin' %}` blocks (`dashboard.html:150-250` and modal headers).
* **Defensive DOM Guards:** Added safety checks in JavaScript functions (`toggleMonitorPanel`, `fetchMonitorProgress`, `updateMonitorPanel`, `flushRecords`, `openKbliModal`) preventing null pointer exceptions when operational elements are omitted from the non-superadmin DOM.

### 4.2 UX-006 Profile Completion Banner (`VERIFIED`)
* **Completion Meter UI:** Embedded `#profile-completion-banner` in the main dashboard view above the tabbed workspace (`dashboard.html:360-440`).
* **API Integration:** Implemented `loadCompanyCompletion()` consuming `/api/company/completion/` asynchronously.
* **Dynamic Indicators:** Renders percentage completion bar, weighted status badge (`Kritis` < 40%, `Cukup` 40-79%, `Lengkap` >= 80%), missing category badges, and contextual modal CTAs (`openCompanyModal('qualifications')`).

### 4.3 UX-007 Dashboard Hierarchy Flip (`VERIFIED`)
* **Primary Viewport Elevation:** Placed AI Match recommendations and high-value opportunities at the top of the workflow hierarchy.
* **Structured Workspace:** Unified Tender Explorer, Watchlist, Radar, and Pipeline into coherent tabbed panels with synchronized state.

### 4.4 P2 Responsive & Mobile Pass (`VERIFIED`)
* **Clone-DOM Elimination:** Removed legacy `cloneNode` routine (`toggleMobileSidebar` previously duplicated sidebar DOM elements, resulting in duplicate radio IDs and unsynchronized filters).
* **Off-Canvas Drawer Navigation:** Converted the desktop filter sidebar into an off-canvas mobile drawer (`#app-sidebar` with `#sidebar-backdrop` and `#mobile-filter-btn`) maintaining a single source of truth for filter states.
* **Mobile Tender Cards:** Introduced responsive card container (`#tender-cards-container`) rendering tender records as vertical cards on screens `< 768px`, eliminating table horizontal scrolling on mobile viewports.
* **Touch Target Sizing:** Elevated tap targets across buttons, tabs, and pagination to meet the minimum 44px threshold (`min-h-[44px]`).

### 4.5 Accessibility (A11y) Pass (`VERIFIED`)
* **ARIA Dialog Attributes:** Added `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, and `aria-hidden` attributes across all modal dialogs (Detail, AI Match, Company Profile, KBLI).
* **Tablist Semantics:** Added `role="tablist"`, `role="tab"`, `aria-selected`, and `aria-controls` to view switches.
* **Keyboard Navigation:** Implemented hierarchical `Escape` key event listener dismissing the topmost active overlay (modal or drawer) in proper stack sequence.
* **WCAG AA Color Contrast:** Upgraded low-contrast typography tokens from `text-slate-400` / `text-slate-500` to `text-slate-600` / `text-slate-700` across labels, metadata subtitles, and table headers to ensure compliance with the 4.5:1 contrast ratio.

### 4.6 Microcopy Consistency (`VERIFIED`)
* Standardized mixed English/Indonesian labels into professional, natural Bahasa Indonesia:
  * "Sync" / "Syncing..." $\rightarrow$ "Sinkronisasi / Perbarui" / "Menyinkronkan..."
  * "AI Fit" $\rightarrow$ "Kecocokan AI / Analisis AI"
  * "READY" / "EMPTY" $\rightarrow$ "SIAP" / "KOSONG"
  * "Apply" $\rightarrow$ "Terapkan Filter"
  * "ERROR" $\rightarrow$ "GAGAL"

### 4.7 Security & Sanitization (`VERIFIED`)
* Maintained universal XSS sanitization via `esc()` helper across dynamic string interpolations in DOM templates.
* Retained `apiFetch` abstraction enforcing `X-CSRFToken` and `X-Requested-With: XMLHttpRequest` headers.

### 4.8 Unknown or Unrelated Changes (`VERIFIED`)
* Zero unrelated changes found. All 87 diff hunks correspond strictly to the approved UX-005, UX-006, UX-007, P2 Mobile, A11y, Microcopy, and defensive error-guarding tasks.

---

# 5. Test Diff Audit

Inspection of `git diff spse_crawler/web/test_security.py` (+292 lines):

* **Nature of Code:** Development and CI verification tests. It is **NOT** production runtime code.
* **Scope of Added Tests:** 4 test classes containing 15 test methods:
  1. `TestUX005SidebarRoleGating` (4 tests):
     * `test_anonymous_user_sidebar_has_no_operational_controls`
     * `test_submitter_user_sidebar_has_no_operational_controls`
     * `test_company_admin_user_sidebar_has_no_operational_controls`
     * `test_superadmin_user_sidebar_has_operational_controls`
  2. `TestUX006ProfileCompletionBanner` (3 tests):
     * `test_dashboard_renders_completion_banner_elements`
     * `test_authenticated_user_can_fetch_completion`
     * `test_superadmin_can_fetch_completion_with_param`
  3. `TestP2MobileResponsivePass` (4 tests):
     * `test_dashboard_has_no_clonenode`
     * `test_off_canvas_drawer_elements_exist`
     * `test_mobile_cards_container_exists`
     * `test_touch_target_accessibility`
  4. `TestA11yMicrocopyPass` (4 tests):
     * `test_modals_dialog_aria_attributes`
     * `test_tabs_and_navigation_a11y`
     * `test_hierarchical_escape_key_handler`
     * `test_microcopy_indonesian_consistency`
* **Execution Evidence:** Test suite executed via pytest:
  `98 passed in 6.27s` (100% pass rate in `test_security.py`).
  Full repository test suite: `473 passed in 16.03s`.
* **Release Boundary:** `LOCAL ONLY`. Tests validate the template changes in local development and CI pipelines, but must not be mixed into production runtime asset deployments.

---

# 6. Runtime Dependency Audit

`spse_crawler/web/templates/dashboard.html` was audited to verify all external and backend runtime dependencies:

### 6.1 Static Assets
* `favicon.ico` $\rightarrow$ Present in `spse_crawler/web/static/` (`VERIFIED`).
* `img/logo.png` $\rightarrow$ Present in `spse_crawler/web/static/` (`VERIFIED`).

### 6.2 External CDN Dependencies
* Tailwind CSS CDN (`https://cdn.tailwindcss.com`) $\rightarrow$ Standard CDN link (`VERIFIED`).
* Google Fonts Inter (`https://fonts.googleapis.com`) $\rightarrow$ Standard typography stylesheet (`VERIFIED`).

### 6.3 Django Template Context
* `user.is_authenticated`, `user.role`, `user.get_full_name`, `user.get_role_display` $\rightarrow$ Standard Django User and Custom User model properties (`VERIFIED`).

### 6.4 Backend API Endpoints Invoked
All 26 API endpoints invoked in `dashboard.html` were mapped to Django URL patterns and backend views:
* `/api/results/` $\rightarrow$ `web.views.api_results` (`VERIFIED`)
* `/api/filter-counts/` $\rightarrow$ `web.views.api_filter_counts` (`VERIFIED`)
* `/api/auth/me/` $\rightarrow$ `accounts.views.api_auth_me` (`VERIFIED`)
* `/api/company/` $\rightarrow$ `companies.views.api_company_list` (`VERIFIED`)
* `/api/company/completion/` $\rightarrow$ `web.views.api_company_completion` (`VERIFIED`)
* `/api/company/create/` $\rightarrow$ `companies.views.api_company_create` (`VERIFIED`)
* `/api/company/<id>/update/` $\rightarrow$ `companies.views.api_company_update` (`VERIFIED`)
* `/api/company/<id>/qualifications/` $\rightarrow$ `companies.views.api_qualification_list` (`VERIFIED`)
* `/api/company/<id>/qualifications/create/` $\rightarrow$ `companies.views.api_qualification_create` (`VERIFIED`)
* `/api/company/<id>/qualifications/<id>/delete/` $\rightarrow$ `companies.views.api_qualification_delete` (`VERIFIED`)
* `/api/start-crawl/` $\rightarrow$ `web.views.api_start_crawl` (`VERIFIED`)
* `/api/crawl-progress/` $\rightarrow$ `web.views.api_crawl_progress` (`VERIFIED`)
* `/api/status/` $\rightarrow$ `web.views.api_status` (`VERIFIED`)
* `/api/status/toggle/` $\rightarrow$ `web.views.api_toggle_scheduler` (`VERIFIED`)
* `/api/flush-records/` $\rightarrow$ `web.views.api_flush_records` (`VERIFIED`)
* `/api/kbli/` $\rightarrow$ `web.views.api_kbli_list` (`VERIFIED`)
* `/api/kbli/create/` $\rightarrow$ `web.views.api_kbli_create` (`VERIFIED`)
* `/api/kbli/<code>/update/` $\rightarrow$ `web.views.api_kbli_update` (`VERIFIED`)
* `/api/kbli/<code>/delete/` $\rightarrow$ `web.views.api_kbli_delete` (`VERIFIED`)
* `/api/submission/` $\rightarrow$ `submissions.views.api_submission_list` (`VERIFIED`)
* `/api/submission/update/` $\rightarrow$ `submissions.views.api_submission_update` (`VERIFIED`)
* `/api/watchlist/` $\rightarrow$ `web.views.api_watchlist_list` (`VERIFIED`)
* `/api/watchlist/add/` $\rightarrow$ `web.views.api_watchlist_add` (`VERIFIED`)
* `/api/watchlist/remove/` $\rightarrow$ `web.views.api_watchlist_remove` (`VERIFIED`)
* `/api/opportunity/<id>/` $\rightarrow$ `web.views.api_opportunity_score` (`VERIFIED`)
* `/api/pipeline/` $\rightarrow$ `web.views.api_pipeline_summary` (`VERIFIED`)
* `/api/radar/` $\rightarrow$ `web.views.api_radar` (`VERIFIED`)
* `/api/recommended/` $\rightarrow$ `web.views.api_recommended` (`VERIFIED`)
* `/api/intelligence/status/` $\rightarrow$ `web.views.api_intelligence_status` (`VERIFIED`)

**Conclusion:** The updated `dashboard.html` does **NOT** depend on any uncommitted or missing backend files. All endpoints are fully implemented in committed codebase.

---

# 7. Migration Audit

Inspection of database migrations across the repository and branches:

```bash
git ls-tree -r --name-only HEAD | grep -E "migrations/0[0-9]+"
git ls-tree -r --name-only origin/production-sync-20260904 | grep -E "migrations/0[0-9]+"
```

* **Local HEAD Migrations Count:** 25 migration files.
* **Production Branch Migrations Count:** 25 migration files.
* **Migration Set Difference:** `set()` (Identical).
* **Local Uncommitted Migrations:** 0 new migrations.
* **Verdict:** `VERIFIED` — The local changes do not introduce any database schema alterations and require zero migration execution.

---

# 8. Production Branch Comparison

Comparison between Local `main` (`HEAD: 8d20a43`) and `origin/production-sync-20260904` (`aa671a7`):

### 8.1 Commit Tree Relationship
* Commits in `origin/production-sync-20260904` not in `HEAD`:
  * `0595147 chore: snapshot production before github sync`
  * `aa671a7 merge production updates and resolve conflicts using theirs`
* Commits in `HEAD` not in `origin/production-sync-20260904`: **0 commits** (Merge-base is `8d20a43`).

### 8.2 File Divergence Between Committed Trees
Only 4 infrastructure and operational documentation files differ between `HEAD` and `origin/production-sync-20260904`:
1. `Dockerfile`: Sets CMD `["gunicorn", "-c", "gunicorn.conf.py", "web_ui.wsgi:application"]` and installs production dependencies.
2. `docker-compose.yml`: Configures production services (`spse-web` on port 8000 and single-instance `spse-scheduler`).
3. `gunicorn.conf.py`: Adds `forwarded_allow_ips = "*"`.
4. `docs/production_migration_runbook.md`: Aligns operational steps with production container names.

### 8.3 Tree Separation Summary
1. **Committed Local Changes in `main`:** All backend intelligence, AI matching, multi-tenant models, and migrations are already merged into the production branch.
2. **Committed Production-Specific Changes:** Production containerization and reverse-proxy settings are isolated to the production branch.
3. **Local Uncommitted Changes:** Strictly frontend template improvements in `dashboard.html` and validation tests in `test_security.py`.

---

# 9. Release Scope Matrix

| File Path | Current State | Runtime Required? | Production Release? | Local Only? | Delete? | Factual Evidence |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `spse_crawler/web/templates/dashboard.html` | Modified (+1956 / -548) | **YES** | **YES** *(Candidate)* | NO | NO | Core customer-facing SPA template; implements UX-005 to UX-007 and P2 Mobile/A11y improvements. |
| `spse_crawler/web/test_security.py` | Modified (+292 / -0) | **NO** | **NO** | **YES** | NO | Automated unit/integration test suite; executes in local development and CI; not runtime code. |
| `.agents/` | Untracked directory | **NO** | **NO** | **YES** | NO | Local AI agent skills (antislop, contrast tools); local tooling only. |
| `AGENTS.md` | Untracked file | **NO** | **NO** | **YES** | NO | Local developer/agent instruction protocol; development governance only. |
| `docs/PROJECT_STATUS_AND_ROADMAP.md` | Untracked file | **NO** | **NO** | **YES** | NO | Local project management status baseline and phase roadmap. |
| `skills-lock.json` | Untracked file | **NO** | **NO** | **YES** | NO | Local agent package lockfile; development tooling only. |
| `docs.zip` | Absent on disk | **NO** | **NO** | NO | **YES** *(Candidate)* | Disposable archive candidate; verified deleted from disk. |

---

# 10. Release Blockers

The audit identified the following factual release blockers that prevent immediate production deployment:

### P0 Blocker: Defective Tenant Binding in `loadCompanyData()`
* **File & Lines:** `spse_crawler/web/templates/dashboard.html:2300-2306`
* **Vulnerability Analysis:**
  ```javascript
  let company = null;
  if (me.company_id) {
    company = items.find(c => c.id === me.company_id);
  } else if (items.length > 0) {
    company = items[0]; // superadmin: pick first
  }
  ```
  The comment states `// superadmin: pick first`, but the code condition only checks `else if (items.length > 0)`.
* **Runtime Failure Scenario:**
  1. If an authenticated user with role `viewer` or `operator` (or an unassigned admin) has `me.company_id == null`, the frontend binds `_companyId = items[0].id` (Company ID 6 on production/staging).
  2. The company profile modal pre-populates with another organization's confidential company details (tenant data leak).
  3. When the user attempts to add or edit qualifications, the backend enforces authorization (`user.role != 'superadmin' and user.company_id != company.id`), immediately rejecting requests with `HTTP 403 Forbidden`.
* **Status:** `BLOCKED`. Must be resolved by adding `if (me.role === 'superadmin')` check before releasing `dashboard.html`.

### P1 Blocker: Production Branch Merge Divergence
* **Issue:** `origin/production-sync-20260904` contains 2 commits ahead of `HEAD` configuring Docker, Compose, and Gunicorn (`0595147` and `aa671a7`).
* **Risk:** Directly pushing `main` to the production branch or executing an unmanaged git merge would overwrite or cause merge conflicts with production infrastructure configurations.
* **Status:** `BLOCKED`. A formal release branching strategy (cherry-pick or rebase onto production sync branch) must be executed.

### P2 Blocker: Staging Hygiene & Tooling Exclusion
* **Issue:** Untracked developer and agent files (`.agents/`, `AGENTS.md`, `skills-lock.json`, `docs/PROJECT_STATUS_AND_ROADMAP.md`) sit in the working tree.
* **Risk:** Running generic `git add .` would inadvertently bundle local AI governance and development docs into the production Git history.
* **Status:** `BLOCKED`. Git staging must explicitly target `spse_crawler/web/templates/dashboard.html` only.

---

# 11. Recommended Release Set

The **ONLY** file approved for inclusion in the upcoming production release commit is:

```text
spse_crawler/web/templates/dashboard.html
```

*(Conditioned on resolving the P0 Blocker in Section 10 prior to commit/deployment).*

Zero backend Python files, zero database migrations, and zero static media files need to be bundled with this release, as backend parity is already `VERIFIED`.

---

# 12. Files Explicitly Excluded

The following files and paths are explicitly excluded from the production release:

1. `spse_crawler/web/test_security.py`
   * **Exclusion Reason:** Development test suite file. Tests validate the code locally and in CI, but should remain in development/testing branches rather than runtime deployments.
2. `.agents/`
   * **Exclusion Reason:** Internal agent skill prompts, Python contrast checker scripts, and AI assistant configurations. Non-runtime development tooling.
3. `AGENTS.md`
   * **Exclusion Reason:** Instructions and constraints for AI coding agents. Not part of the production software application.
4. `skills-lock.json`
   * **Exclusion Reason:** Dependency tracking file for local agent skills.
5. `docs/PROJECT_STATUS_AND_ROADMAP.md`
   * **Exclusion Reason:** Internal development roadmap and project management documentation; kept in repository documentation for project tracking, not bundled in production release patches.
6. `docs.zip`
   * **Exclusion Reason:** Disposable archive candidate; confirmed already deleted from workspace.

---

# 13. Next Safe Action

The human operator or developer should review the P0 Blocker in `spse_crawler/web/templates/dashboard.html:2300-2306` and authorize a single, targeted code adjustment to restrict the `items[0]` fallback exclusively to `me.role === 'superadmin'`:

```javascript
    let company = null;
    if (me.company_id) {
      company = items.find(c => c.id === me.company_id);
    } else if (me.role === 'superadmin' && items.length > 0) {
      company = items[0]; // superadmin only: pick first
    }
```

*(Do NOT execute any code modification, commit, or deployment during this audit).*

