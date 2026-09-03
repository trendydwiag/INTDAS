# Phase Migration Plan (v0.0.1 -> v0.0.2)

## Phase 1: Authentication & Profile (Week 1-2)

**Goal:** Users can login, manage profiles, and see role-based UI.

### Step 1.1: Custom User Model
- Create `spse_crawler/auth/` module
- Create `User` model extending `AbstractUser` with email, role, company fields
- Create `EmailBackend` for email-based authentication
- Set `AUTH_USER_MODEL = "auth.User"` in settings
- Create migration 0001_initial for auth module
- **CRITICAL:** Must be done BEFORE any other migration, since Django auth depends on it

### Step 1.2: Company Profile and Qualification
- Create `spse_crawler/companies/` module
- Create `CompanyProfile` and `CompanyQualification` models
- Create CRUD views and API endpoints
- Register in Django admin
- Create profile management page template

### Step 1.3: Auth Views and Middleware
- Create `RoleMiddleware` (attaches role/company to request)
- Create login/logout views and templates
- Create `@role_required` decorator
- Update INSTALLED_APPS and AUTH_USER_MODEL in settings
- Create `login.html` template

### Step 1.4: Dashboard UI Updates
- Add navbar with user info and logout button
- Update `dashboard()` view to pass user context
- Make dashboard accessible without login (anonymous mode preserved)
- Test: all v0.0.1 features still work identically

### Step 1.5: Data Migration
- Migrate existing admin user to new User model
- Assign superadmin role to admin
- Test admin login via both Django admin and new login page

**Deliverables:** Login page, role-based navbar, company profile page, qualification CRUD

---

## Phase 2: AI Qualification Matcher (Week 3-4)

**Goal:** AI automatically scores tender qualification fit against company profile.

### Step 2.1: AI Provider Abstraction
- Create `spse_crawler/ai_match/` module
- Create `providers.py` with OpenAIProvider, GeminiProvider, AnthropicProvider classes
- Create provider factory: `get_provider(provider_name)` returns appropriate class
- Add API client libraries to requirements.txt

### Step 2.2: AI Matcher Core
- Create `matcher.py` with AIMatcher class
- Implement `load_qualifications(company_id)` -- queries CompanyQualification, builds JSON
- Implement `fetch_requirement_text(tender_id)` -- fetches raw HTML from SPSE detail page, extracts qualification section
- Implement `call_llm(provider, prompt)` -- calls LLM API with structured prompt
- Implement `parse_llm_response(raw)` -- validates and parses JSON from LLM output
- Implement `run_match(tender_id, company_id)` -- orchestrates full match pipeline

### Step 2.3: Caching Layer
- Implement cache key generation: SHA256(tender_id + company_id + qualification_hash)
- Implement cache lookup: check AIMatchResult before calling LLM
- Implement cache invalidation: delete stale results when qualifications change
- Configurable TTL via AI_CACHE_TTL_DAYS env var

### Step 2.4: API Endpoints
- `POST /api/match/run/` -- trigger AI match for a tender (with company context)
- `GET /api/match/results/` -- list match results (filtered by company)
- `GET /api/match/results/<id>/` -- single match result detail
- Rate limiting: max matches per day per company

### Step 2.5: Dashboard Integration
- Add AI Fit Score column to results table
- Add fit score badge with color coding: 0-40% red, 41-70% yellow, 71-100% green
- Add click-to-expand modal showing full criteria breakdown
- Add "Re-Match" button in modal

**Deliverables:** AI match engine, provider abstraction, caching, dashboard fit score column

---

## Phase 3: Tender Submission Tracking (Week 5-6)

**Goal:** Users can track submission status with audit trail.

### Step 3.1: Submission Model and Service
- Create `spse_crawler/submissions/` module
- Create `TenderSubmissionStatus` model (unique per tender+company)
- Create `submission_service.py` with status update logic
- Implement unique constraint: one status per tender per company

### Step 3.2: Audit Log
- Create `spse_crawler/audit/` module
- Create `AuditLog` model
- Create `audit_service.py` for logging status changes
- Implement Django signals: post_save on TenderSubmissionStatus triggers AuditLog entry
- Track: user, company, action, old_value, new_value, timestamp, ip_address

### Step 3.3: API Endpoints
- `POST /api/submission/update/` -- update submission status (Admin Submitter only)
- `GET /api/submission/` -- list submission statuses (filtered by company)
- `GET /api/audit-log/` -- list audit entries (Admin Perusahaan + Superadmin only)

### Step 3.4: Dashboard Action Column
- Add Status column with dropdown (Admin Submitter: editable, Admin Perusahaan: read-only)
- Status options: Belum Diproses, Cocok & Diproses, Sudah Submit, Tidak Cocok
- Add Notes column (expandable textarea)
- Add Audit Log tab/view in sidebar

### Step 3.5: Role-Based Access Control for Submissions
- Admin Submitter: can update status for own company only
- Admin Perusahaan: can view all statuses for own company, can approve/reject
- Superadmin: can view all submissions across all companies
- Validate: Submitter cannot change status to different company

**Deliverables:** Submission tracking, audit log, action column in dashboard, role enforcement

---

## Execution Checklist

### Pre-Migration (Before Any Code)

- [ ] Backup current SQLite database
- [ ] Create git branch: `feature/v0.0.2-smart-matcher`
- [ ] Verify all v0.0.1 tests pass (manual smoke test)
- [ ] Install new requirements: `pip install openai google-generativeai anthropic`

### Phase 1 Verification

- [ ] Login page renders at `/login/`
- [ ] Admin can login with email/password
- [ ] Dashboard shows user info in navbar when logged in
- [ ] Dashboard works without login (anonymous mode)
- [ ] Company profile page accessible to Admin Perusahaan
- [ ] Qualification CRUD works
- [ ] Role-based access: Submitter cannot see company profile edit
- [ ] All 15 v0.0.1 API endpoints still work without auth
- [ ] All v0.0.1 crawler features still work (crawl, flush, KBLI CRUD)

### Phase 2 Verification

- [ ] AI match runs with OpenAI provider
- [ ] AI match runs with Gemini provider
- [ ] AI match runs with Anthropic provider
- [ ] Match result cached (second call is instant)
- [ ] Cache invalidated when qualifications change
- [ ] Fit score badge shows correct color
- [ ] Criteria breakdown modal opens with full detail
- [ ] Re-Match button triggers new analysis
- [ ] Rate limit enforced (cannot exceed daily quota)
- [ ] System works when LLM API is down (returns score 0)

### Phase 3 Verification

- [ ] Admin Submitter can update submission status
- [ ] Status change reflected in dashboard immediately
- [ ] Audit log captures all status changes
- [ ] Admin Perusahaan can see all company submissions
- [ ] Submitter cannot change status for different company
- [ ] Audit log filterable by date and user
- [ ] All existing v0.0.1 features still work identically

---

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Custom User model breaks existing Django admin | High | Use AbstractUser (Django-compatible); test admin immediately after migration |
| AI API cost exceeds budget | Medium | Rate limit per company; cache aggressively; fail-safe to score 0 |
| LLM response parsing fails | Medium | Validate JSON schema; retry with simpler prompt; fallback to score 0 |
| Existing v0.0.1 dashboard breaks | High | Isolate all changes to additive-only; smoke test after each phase |
| PostgreSQL migration fails | Medium | Keep SQLite fallback; test both DB backends before deployment |
| Multi-tenant data leakage | Critical | Enforce company filter at queryset level; test with multiple companies |
