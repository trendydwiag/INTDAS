# PRODUCTION MIGRATION RUNBOOK — SPSE INAPROC CRAWLER

**Document Version:** 3.0 (Phase 6.7 Pre-Deployment Verified & Synchronized)  
**Target Environment:** Production PostgreSQL Server + Docker Compose / Systemd  
**Gate Declaration:** **GO** (Runbook 100% executable & synchronized with repository)  

---

> [!IMPORTANT]
> **HARD RULE:** Do NOT execute production migrations during Phase 6.7. This document defines the exact step-by-step procedure to be executed by the DevOps / Superadmin team during the planned production maintenance window.

---

## 1. PRODUCTION ARCHITECTURE SPECIFICATION

In Phase 6.4 and Phase 6.7, the single-process process model was hardened to separate Web Request Handling from Background Scheduling:

```text
               +----------------------------------+
               |    Production Reverse Proxy      |
               |         (Nginx / Caddy)          |
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |        Gunicorn Web Service      |
               | (4 WSGI workers — NO scheduler)  |
               +----------------------------------+
                                |
         +----------------------+----------------------+
         |                                             |
         v                                             v
+------------------+                        +--------------------+
|  PostgreSQL DB   | <--------------------- |  Scheduler Daemon  |
|  (Production DB) |                        | (single container) |
+------------------+                        +--------------------+
```

### 1.1 Process Model Breakdown
1. **Web Service Container (`spse-web`):**
   - Service Name: `web`
   - Container Name: `spse-web`
   - Command: `gunicorn web_ui.wsgi:application --config gunicorn.conf.py`
   - Workers: `GUNICORN_WORKERS=4`
   - Exposed Port: `8000:8000`
   - Responsibilities: Serves HTTP/HTTPS user interface and REST API requests ONLY.
   - Scheduler Responsibility: **DISABLED / REMOVED** from WSGI request loop.

2. **Scheduler Daemon Container (`spse-scheduler`):**
   - Service Name: `scheduler`
   - Container Name: `spse-scheduler`
   - Command: `python manage.py run_scheduler`
   - Workers: **1 single-process daemon instance**
   - Exposed Port: None (internal process only)
   - Responsibilities: Executes periodic hourly SPSE crawl and 10-minute PD-1 intelligence pipeline ticks (`run_cycle()`).
   - Lifecycle: Runs independently of web worker recycling; handles `SIGTERM` / `SIGINT` gracefully.

---

## 2. MANDATORY PRODUCTION ENVIRONMENT VARIABLES (`.env`)

Verify that the following environment variables are present in the production `.env` file before launching containers:

| Variable Name | Required | Example Production Value | Purpose / Constraint |
| ------------- | -------- | ------------------------ | -------------------- |
| `DJANGO_SETTINGS_MODULE` | Yes | `web_ui.settings` | Points to production settings module |
| `DJANGO_DEBUG` | Yes | `0` | Must be `0` in production (disables debug pages) |
| `DJANGO_SECRET_KEY` | Yes | `c83f...91a2` | Mandatory (min 64-char random hex string) |
| `ALLOWED_HOSTS` | Yes | `monitor-lpse.khansia.co.id,localhost` | Comma-separated allowed domain names |
| `DATABASE_URL` | Yes | `postgresql://user:pass@postgres_host:5432/spse_db` | PostgreSQL connection string |
| `CSRF_TRUSTED_ORIGINS` | Yes | `https://monitor-lpse.khansia.co.id` | Scheme + domain for CSRF protection |
| `SPSE_LOG_LEVEL` | No | `INFO` | Logger verbosity level |
| `SPSE_HEADLESS` | No | `1` | Runs Playwright Chromium in headless mode |

---

## 3. PRE-FLIGHT CHECKLIST

Before executing the migration on the production server, verify all pre-flight conditions:

### 3.1 Environment & System Check Verification
Confirm production configuration passes system check:
```bash
docker compose run --rm web python manage.py check
```
*Expected Output:* `System check identified no issues (0 silenced).`

### 3.2 Database Backup (Mandatory)
Take a full pre-migration snapshot / dump of the production PostgreSQL database:
```bash
# PostgreSQL Database Backup
pg_dump -U ${POSTGRES_USER} -h ${POSTGRES_HOST} -p ${POSTGRES_PORT} -F c -b -v -f /backups/spse_prod_pre_phase6_$(date +%Y%m%d_%H%M%S).dump ${POSTGRES_DB}
```

### 3.3 Pre-Migration Status Inspection
Check current unapplied migrations on production:
```bash
docker compose run --rm web python manage.py showmigrations
```
Expected unapplied migrations for Phase 6B/6C/6.4:
- `companies.0003_alter_companyprofile_npwp_and_more`
- `web.0014_tenderwinner_alamat_tenderwinner_harga_negosiasi_and_more`
- `web.0015_tenderparticipant_company_and_more`
- `ai_match.0002_aimatchresult_fallback_reason_and_more`

---

## 4. MIGRATION EXECUTION PROCEDURE

Execute the Django database migration in non-interactive mode:

```bash
docker compose run --rm web python manage.py migrate --noinput
```

### Expected Output Sequence:
```text
Running migrations:
  Applying companies.0003_alter_companyprofile_npwp_and_more... OK
  Applying web.0014_tenderwinner_alamat_tenderwinner_harga_negosiasi_and_more... OK
  Applying web.0015_tenderparticipant_company_and_more... OK
  Applying ai_match.0002_aimatchresult_fallback_reason_and_more... OK
```

> [!NOTE]
> All Phase 6 migrations (`companies.0003`, `web.0014`, `web.0015`, `ai_match.0002`) are **100% additive** (adding nullable fields, indexed columns, and default values). They do NOT modify or drop existing data tables.
>
> `entrypoint.sh` also automatically executes `python manage.py migrate --noinput` upon container boot.

---

## 5. CONTAINER STARTUP & HEALTH VERIFICATION

### 5.1 Startup Sequence
Start containers in order:
```bash
# 1. Start Gunicorn Web Service Container
docker compose up -d web

# 2. Start Dedicated Scheduler Daemon Container
docker compose up -d scheduler
```

### 5.2 Web Service Health Verification
```bash
curl -s http://localhost:8000/api/crawl-progress/ | grep -q "status" && echo "Web Service OK"
```

### 5.3 Scheduler Daemon Verification
Inspect scheduler logs to confirm active job registration:
```bash
docker logs spse-scheduler --tail 20
```
Expected output:
```text
[SCHEDULER] Starting dedicated single-process scheduler daemon...
APScheduler started — hourly crawl + pipeline jobs registered
[SCHEDULER] Daemon active. Running jobs: ['hourly_crawl', 'intelligence_pipeline_tick']
```

---

## 6. ROLLBACK & RECOVERY STRATEGY

Since Phase 6 migrations are strictly additive (adding nullable fields and ForeignKeys), the rollback procedure is low-risk:

### 6.1 Django Target Rollback (Soft Rollback)
If application issues arise post-migration without schema corruption, unapply migrations in strict reverse-dependency order:
```bash
# 1. Unapply ai_match Phase 6 migration
docker compose run --rm web python manage.py migrate ai_match 0001

# 2. Unapply web Phase 6 migrations (web.0015 and web.0014)
docker compose run --rm web python manage.py migrate web 0013

# 3. Unapply companies Phase 6 migration (companies.0003)
docker compose run --rm web python manage.py migrate companies 0002
```

### 6.2 Full Database Restore (Hard Rollback)
If database corruption occurs:
```bash
# 1. Stop application containers
docker compose stop web scheduler

# 2. Restore PostgreSQL database dump
pg_restore -U ${POSTGRES_USER} -h ${POSTGRES_HOST} -d ${POSTGRES_DB} --clean --if-exists /backups/spse_prod_pre_phase6_*.dump

# 3. Restart application containers
docker compose up -d
```

---

## 7. SIGN-OFF GATE

| Checklist Item | Verified By | Date | Status |
| -------------- | ----------- | ---- | ------ |
| Pre-flight Backup | DevOps Team | Pending Prod Deploy | [ ] PASS |
| Migration Execution | Superadmin | Pending Prod Deploy | [ ] PASS |
| Web Container (`spse-web`) | Lead Engineer | Pending Prod Deploy | [ ] PASS |
| Scheduler Container (`spse-scheduler`) | Lead Engineer | Pending Prod Deploy | [ ] PASS |
| Smoke Tests | QA / Dev Team | Pending Prod Deploy | [ ] PASS |
