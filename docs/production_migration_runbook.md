# PRODUCTION MIGRATION RUNBOOK — SPSE INAPROC CRAWLER

**Document Version:** 2.0 (Phase 6.4 Deployment Hardened)  
**Target Environment:** Production PostgreSQL Server + Docker Compose / Systemd  

---

> [!IMPORTANT]
> **HARD RULE:** Do NOT execute production migrations during Phase 6.4. This document defines the exact step-by-step procedure to be executed by the DevOps / Superadmin team during the planned production maintenance window.

---

## 1. PRODUCTION ARCHITECTURE SPECIFICATION

In Phase 6.4, the architecture was hardened to separate Web Request Handling from Background Scheduling:

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
   - Command: `gunicorn -c gunicorn.conf.py web_ui.wsgi:application`
   - Workers: `GUNICORN_WORKERS=4`
   - Responsibilities: Serves HTTP/HTTPS user interface and REST API requests ONLY.
   - Scheduler Responsibility: **DISABLED / REMOVED** from WSGI request loop.

2. **Scheduler Daemon Container (`spse-scheduler`):**
   - Command: `python manage.py run_scheduler`
   - Workers: **1 single-process daemon instance**
   - Responsibilities: Executes periodic hourly SPSE crawl and 10-minute PD-1 intelligence pipeline ticks (`run_cycle()`).
   - Lifecycle: Runs independently of web worker recycling; handles `SIGTERM` / `SIGINT` gracefully.

---

## 2. PRE-FLIGHT CHECKLIST

Before executing the migration on the production server, verify all pre-flight conditions:

### 2.1 Environment Verification
Confirm that production environment variables are properly set:
```bash
echo "Settings Module : ${DJANGO_SETTINGS_MODULE}"
echo "Debug Mode      : ${DJANGO_DEBUG}"
echo "Allowed Hosts   : ${ALLOWED_HOSTS}"
echo "Database URL    : ${DATABASE_URL:0:25}..."
```
* Requirement: `DJANGO_DEBUG` must be set to `0` or `False`.
* Requirement: `DATABASE_URL` must point to the production PostgreSQL instance.

### 2.2 Database Backup (Mandatory)
Take a full pre-migration snapshot / dump of the production PostgreSQL database:
```bash
# PostgreSQL Database Backup
pg_dump -U ${POSTGRES_USER} -h ${POSTGRES_HOST} -p ${POSTGRES_PORT} -F c -b -v -f /backups/spse_prod_pre_phase6_$(date +%Y%m%d_%H%M%S).dump ${POSTGRES_DB}
```

### 2.3 Pre-Migration Status Inspection
Check current migration status on production:
```bash
python manage.py showmigrations
```
Expected unapplied migrations for Phase 6B/6C/6.4:
- `companies.0003_alter_companyprofile_npwp_and_more`
- `web.0014_tenderwinner_alamat_tenderwinner_harga_negosiasi_and_more`
- `web.0015_tenderparticipant_company_and_more`
- `ai_match.0002_aimatchresult_fallback_reason_and_more`

### 2.4 Django System Check
Verify that system configuration contains 0 errors:
```bash
python manage.py check
```

---

## 3. MIGRATION EXECUTION PROCEDURE

Execute the Django database migration in non-interactive mode:

```bash
python manage.py migrate --noinput
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

---

## 4. CONTAINER STARTUP & HEALTH VERIFICATION

### 4.1 Startup Sequence
Start containers in order:
```bash
# 1. Start Gunicorn Web Service
docker compose up -d web

# 2. Start Dedicated Scheduler Daemon
docker compose up -d scheduler
```

### 4.2 Web Service Health Verification
```bash
curl -s http://localhost:8000/api/crawl-progress/ | grep -q "status" && echo "Web Service OK"
```

### 4.3 Scheduler Daemon Verification
Inspect scheduler logs to confirm active job registration:
```bash
docker logs spse-scheduler --tail 20
```
Expected output:
```text
[SCHEDULER] Starting dedicated single-process scheduler daemon...
APScheduler started — hourly crawl + pipeline jobs registered
[SCHEDULER] Daemon active. Running jobs: ['intelligence_pipeline_tick', 'hourly_crawl']
```

---

## 5. ROLLBACK & RECOVERY STRATEGY

Since Phase 6 migrations are strictly additive (adding nullable fields and ForeignKeys), the rollback procedure is low-risk:

### 5.1 Django Target Rollback (Soft Rollback)
If application issues arise post-migration without schema corruption:
```bash
python manage.py migrate ai_match 0001
python manage.py migrate web 0013
python manage.py migrate companies 0002
```

### 5.2 Full Database Restore (Hard Rollback)
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

## 6. SIGN-OFF GATE

| Checklist Item | Verified By | Date | Status |
| -------------- | ----------- | ---- | ------ |
| Pre-flight Backup | DevOps Team | Pending Prod Deploy | [ ] PASS |
| Migration Execution | Superadmin | Pending Prod Deploy | [ ] PASS |
| Web Container (`spse-web`) | Lead Engineer | Pending Prod Deploy | [ ] PASS |
| Scheduler Container (`spse-scheduler`) | Lead Engineer | Pending Prod Deploy | [ ] PASS |
| Smoke Tests | QA / Dev Team | Pending Prod Deploy | [ ] PASS |
