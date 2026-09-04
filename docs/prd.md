# Product Requirements Document (PRD) v0.0.3

## Project Name: SPSE Inaproc Smart Tender Matcher & Multi-Role Workflow System

### 1. Document Control

| Field | Value |
|---|---|
| **Version** | 0.0.3 |
| **Status** | Implemented — Phases 1–3 Approved; PD-1 Under Development |
| **Previous Version** | v0.0.2 |
| **Target Release** | v0.0.3 |
| **Last Updated** | 2026-08-28 |

---

### 2. Version History

| Version | Date | Changes |
|---|---|---|
| v0.0.1 | 2026-08-20 | Core crawler engine, 2-stage scraping, Cloudflare stealth bypass, Web Dashboard, KBLI master CRUD, IT priority scoring, auto-scheduler, flush/purge, PostgreSQL migration |
| v0.0.2 | 2026-08-21 | RBAC multi-role auth, Company/User profiles, AI Qualification Matcher, Tender submission tracking, Audit log |
| v0.0.3 | 2026-08-28 | Security/CSRF hardening (F-01, F-02 fixed); **Phase 1** Opportunity Score v0.1; **Phase 2** Tender Radar; **Phase 3** Intelligence Status/Readiness; Automated Intelligence Pipeline infra (PD-1, under development). Suite 265/265 PASS, deploy NOT done |

---

### 3. Objectives & Problem Statement

#### 3.1 Problem Context
Platform SPSE Inaproc terdiri dari ratusan subsitus instansi/provinsi. v0.0.1 membangun crawler otomatis yang mengekstrak data tender, memfilter paket prakualifikasi, dan menampilkannya di Web Dashboard. v0.0.2 menambahkan multi-tenant auth, profil perusahaan, AI qualification matcher, submission workflow, dan audit trail.

#### 3.2 New Problem (v0.0.3)
Data AI Match telah dihasilkan per (tender, perusahaan), namun:
1. Belum ada **skor peluang agregat** yang memandu prioritas tender terbaik untuk dikejar.
2. Belum ada **Tender Radar** yang mempersonalisasi peluang cocok untuk perusahaan.
3. Belum ada indikator **kesiapan intelligence** — apakah sistem sudah punya cukup data untuk menghasilkan rekomendasi.
4. AI Match masih sangat manual (per-trigger), belum otomatis dan bounded.

#### 3.3 Objective v0.0.3
- Membangun **Opportunity Score v0.1** — skor peluang terstandar per (tender, perusahaan).
- Membangun **Tender Radar** — lapisan personalisasi peluang berperingkat untuk perusahaan.
- Membangun **Intelligence Status / Readiness** — menunjukkan kesiapan data intelligence.
- Menyiapkan fondasi **Automated Intelligence Pipeline** (queue/job, rate-limit, retry) untuk otomasi future.

---

### 4. Phase Roadmap & Readiness

> Classification: **READY** (implemented + tested + approved) · **UNDER DEVELOPMENT** (code present, not activated/approved) · **CURRENT DEVELOPMENT** (active focus) · **PLANNED** (future).

| Phase | Deliverable | Status | Tests |
|---|---|---|---|
| — | Core crawler + dashboard | READY | — |
| — | Multi-tenant auth + AI Match + submission + audit | READY | — |
| — | Security/CSRF hardening | READY | — |
| **Phase 1** | **Opportunity Score v0.1** | **READY** | 64 |
| **Phase 2** | **Tender Radar** | **READY** | 25 |
| **Phase 3** | **Intelligence Status / Readiness** | **READY** | 20 |
| **PD-1** | **Automated Intelligence Pipeline** | **UNDER DEVELOPMENT** | infra present, not activated |
| Future | Competitor / Winner / Win-Probability / Price Intelligence | PLANNED | — |
| Future | Daily Briefing, WhatsApp/Telegram/Email notification | PLANNED | — |

**Suite: 265/265 PASS · REGRESSION: 0 · DEPLOY: NOT DONE**

---

### 5. User Roles & Permissions

Sistem memiliki **3 role** dengan hak akses berbeda:

#### 5.1 Superadmin
**Target:** Developer/operator sistem.

| Capability | Scope |
|---|---|
| System configuration | Scheduler, crawler settings, instansi targets |
| Master data management | KBLI master, all company profiles |
| System observability | System logs, crawl jobs, error monitoring, pipeline status |
| User management | Create/edit/delete all users, assign roles |
| Tenant management | Create/edit company accounts |
| Cross-company inspection | May pass `?company_id=` to inspect another tenant's Opportunity/Radar/Status |

#### 5.2 Admin Perusahaan (Company Admin / C-Level)
**Target:** Direktur, C-Level, Komisaris perusahaan peserta tender.

| Capability | Scope |
|---|---|
| Company profile management | Edit profil perusahaan, unggah dokumen legalitas |
| Qualification data | Izin Usaha, SBU, KBLI, Kemampuan Keuangan, Portofolio, Sertifikasi |
| User management (internal) | Akun Admin Submitter untuk perusahaan mereka |
| AI match + Opportunity | Melihat hasil pencocokan AI dan skor peluang perusahaan mereka |
| Tender Radar | Melihat radar peluang personalisasi perusahaan mereka |
| Intelligence readiness | Melihat status kesiapan intelligence perusahaan mereka |
| Dashboard access | Tab IT Priority, Prakualifikasi, filter non-system-log |

#### 5.3 Admin Submitter (BD Staff / Operational)
**Target:** Staff Business Development, Proposal Writer, Operasional lapangan.

| Capability | Scope |
|---|---|
| Tender browsing | Melihat daftar tender, filter, search |
| AI match + Opportunity review | Melihat hasil AI dan skor peluang untuk tender relevan |
| Tender Radar | Melihat radar peluang perusahaan mereka (read) |
| Submission status update | `Belum Diproses` → `Cocok & Diproses` → `Sudah Submit` / `Tidak Cocok` |
| Notes & annotations | Catatan internal per tender |

> **Isolation rule (semua role):** `company_admin` / `submitter` hanya melihat data perusahaan mereka; tidak bisa memilih perusahaan lain. Hanya `superadmin` yang bisa lintas-perusahaan.

---

### 6. User Stories & Functional Requirements

#### US-04 (READY): AI Qualification Matcher Engine
AI match menghasilkan `fit_score` 0–100, `summary`, dan breakdown `criteria`. Hasil di-cache per (tender, company). Fit Score ditampilkan sebagai badge 🔴 0–40 / 🟡 41–70 / 🟢 71–100.

#### US-07 (READY, Phase 1): Opportunity Score v0.1
* **As a** Admin Perusahaan,
* **I want to** melihat skor peluang agregat (0–100) per tender untuk perusahaan saya,
* **So that** saya tahu tender mana yang paling layak dikejar.

**Acceptance Criteria:**
- Skor dihitung dari 5 komponen: Kualifikasi 35%, Finansial 20%, Pengalaman 20%, Deadline 15%, Strategis 10%.
- Menggunakan formula v0.1 yang deterministik (bukan LLM).
- Klasifikasi: `PRIORITAS_TINGGI ≥90`, `LAYAK_DIKEJAR ≥75`, `REVIEW ≥60`, `RISIKO_TINGGI ≥40`, `TIDAK_DIREKOMENDASIKAN <40`.
- Membutuhkan hasil AI Match (tanpa AI Match → `NOT_READY`, tanpa skor fiktif).
- Idempotent (tidak duplikat per tender+company).
- Disimpan di `OpportunityScore` dengan `calculation_version="v0.1"`.
- Endpoint: `GET /api/opportunity/<tender_id>/` (backward-compatible).

#### US-08 (READY, Phase 2): Tender Radar
* **As a** Admin Perusahaan / Admin Submitter,
* **I want to** melihat daftar peluang tender berperingkat yang cocok dengan perusahaan saya,
* **So that** saya bisa fokus ke tender terbaik tanpa menyaring semua data.

**Acceptance Criteria:**
- Hanya tender dengan AI Match (dan Opportunity Score ready) yang muncul sebagai READY.
- Ranking: klasifikasi (PRIORITAS_TINGGI dulu) → skor peluang (tinggi dulu) → deadline (segera dulu).
- READY selalu di atas NOT_READY.
- Menampilkan AI Fit + Opportunity berdampingan.
- Filter: klasifikasi, min score, search, KBLI, lokasi, IT priority, deadline, HPS.
- Isolasi per perusahaan; tidak ada kebocoran antar-perusahaan.
- Endpoint: `GET /api/radar/`.

#### US-09 (READY, Phase 3): Intelligence Status / Readiness
* **As a** Admin Perusahaan,
* **I want to** melihat apakah sistem sudah punya cukup data intelligence untuk perusahaan saya,
* **So that** saya tahu apakah Tender Radar / Opportunity sudah siap digunakan.

**Acceptance Criteria:**
- Menampilkan level readiness: `READY` / `PARTIAL` / `NOT_READY` (dan `EMPTY` jika tanpa perusahaan).
- Menampilkan hitungan: `tenders`, `ai_matches`, `opportunity_scores`, `ready_opportunities`.
- Menampilkan coverage: `ai_match_percent`, `opportunity_score_percent`.
- Read-only: tidak memanggil LLM, tidak melakukan scoring, tidak mutasi data (tested).
- Isolasi per perusahaan; superadmin dapat menggunakan `?company_id=`.
- Endpoint: `GET /api/intelligence/status/`.

#### US-10 (UNDER DEVELOPMENT, PD-1): Automated Intelligence Pipeline
* **As a** Superadmin/operator,
* **I want to** sistem otomatis memproses tender baru melalui AI Match → Opportunity Score,
* **So that** Tender Radar selalu segar tanpa intervensi manual.

**Acceptance Criteria (fondasi):**
- Queue persisten `IntelligenceJob` dengan unique `(tender, company, job_type)`.
- Rate limit per company (`AI_MAX_MATCHES_PER_DAY=100`) via `IntelligenceDailyUsage`.
- Bounded (`batch_size`), idempotent, retry + backoff, stale-recovery, company-isolated, failure-safe.
- Deterministik eligibility pre-filter (tanpa data cukup → SKIP).
- Reuse matcher + scorer existing — TIDAK membuat implementasi AI kedua.

> **STATUS CATATAN:** Infrastruktur PD-1 sudah ada di kode & ter-migrasi, namun **belum diaktifkan** (job scheduler `next_run_time=None`; tidak ada pemicuan LLM massal). Aktivasi termasuk fase lanjutan.

---

### 7. Dashboard UI Requirements (v0.0.3 Additions)

#### 7.1 Opportunity Modal
- Dibuka dari skor peluang / AI Fit.
- Menampilkan AI Fit + Opportunity berdampingan.
- Komponen skor, klasifikasi, dan penjelasan (breakdown).

#### 7.2 Tender Radar Section
- Section "Tender Radar" di atas tabel utama (collapsible).
- Chip klasifikasi (Sangat Layak / Layak / Perlu Ditinjau).
- Kartu peluang: AI FIT + OPPORTUNITY, deadline urgency, HPS, lokasi, badge IT.

#### 7.3 Intelligence Readiness Indicator
- Bar "AI Analysis" di bawah Tender Radar.
- Chip status: `READY` (hijau) / `PARTIAL` (kuning) / `NOT READY` (merah) / `EMPTY` (abu) / `ERROR`.
- Coverage: "AI Match X% · Skor Peluang Y%".
- Dimuat via `loadIntelligenceStatus()` pada init dashboard + polling 30 detik.
- **Tidak mengubah** ranking Tender Radar, AI Fit card, atau Opportunity Score; tidak memicu scoring otomatis.

---

### 8. API Endpoint Requirements (v0.0.3 New Endpoints)

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/opportunity/<tender_id>/` | GET | Authenticated | Opportunity Score (backward-compatible; adds score/status/classification/components/explanation) |
| `/api/radar/` | GET | Authenticated | Ranked personalized tender opportunities |
| `/api/intelligence/status/` | GET | Authenticated | Intelligence readiness (READY/PARTIAL/NOT_READY) |
| `/api/intelligence/reprocess/` | POST | Authenticated, CSRF | Request tender/company reprocessing (PD-1; queues job, no sync LLM) |

Company scoping: superadmin may pass/select `company_id`; company_admin/submitter always own-company only.

---

### 9. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Backward Compatibility** | Semua fitur v0.0.1–v0.0.2 tetap berjalan; Phase 1–3 aditif; 265/265 PASS, 0 regression |
| **Performance** | Radar & status read-only, no N+1 (aggregate COUNT / prefetch) |
| **Scalability** | Mendukung 10+ perusahaan × 1000 tender aktif |
| **Security** | Bassword PBKDF2; session 24 jam; CSRF (no `@csrf_exempt` pada endpoint baru); company isolation; no horizontal escalation |
| **Observability** | AI match log, submission audit log, pipeline status/readiness |
| **Data Isolation** | Admin Perusahaan A tidak bisa melihat data/intelligence Perusahaan B |

---

### 10. Out of Scope (v0.0.3)

- **Belum lanjut ke Phase 4** (Automated Pipeline aktivasi penuh).
- Competitor Intelligence, Winner Intelligence, Win Probability, Price Intelligence.
- Daily Briefing, WhatsApp / Telegram / Email notification.
- Automatic submission / tender bidding.
- Automatic LLM processing massal terhadap production.

---

### 11. Success Criteria

| Metric | Target |
|---|---|
| AI match accuracy | ≥ 80% konsisten dengan penilaian manual |
| Opportunity Score determinism | 100% idempotent, reproducible (v0.1) |
| Tender Radar relevance | Ranking konsisten dengan klasifikasi + skor + deadline |
| Intelligence readiness accuracy | Level sesuai kondisi data aktual (tested) |
| Company isolation | 0 kebocoran lintas perusahaan (tested) |
| Regression | 0 regression pada seluruh suite (265/265) |
| Deploy | NOT DONE (menunggu approval) |
