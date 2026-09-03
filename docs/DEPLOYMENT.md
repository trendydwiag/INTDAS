# ============================================
# SPSE Inaproc Crawler — Deployment Guide
# v0.0.4 — Multi-Tenant Scraper & Intelligence Dashboard
# ============================================

## Daftar Isi

1. [Persyaratan Sistem](#1-persyaratan-sistem)
2. [Quick Start (Docker)](#2-quick-start-docker)
3. [Manual Deployment (Ubuntu/Debian)](#3-manual-deployment-ubuntudebian)
4. [Seed Data](#4-seed-data)
5. [Konfigurasi Environment](#5-konfigurasi-environment)
6. [Nginx Reverse Proxy + SSL](#6-nginx-reverse-proxy--ssl)
7. [Systemd Service Management](#7-systemd-service-management)
8. [Backup & Restore Database](#8-backup--restore-database)
9. [Monitoring & Troubleshooting](#9-monitoring--troubleshooting)
10. [Crawler Commands](#10-crawler-commands)

---

## 1. Persyaratan Sistem

| Komponen | Minimum | Recommended |
|----------|---------|-------------|
| OS | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 LTS |
| CPU | 2 core | 4 core |
| RAM | 2 GB | 4 GB |
| Disk | 10 GB | 20 GB |
| Python | 3.10+ | 3.12 |
| Network | Akses internet ke spse.inaproc.id | Static IP |

---

## 2. Quick Start (Docker)

```bash
# 1. Clone / upload project
cd spse-crawler

# 2. Generate Django secret key
export DJANGO_SECRET_KEY=$(openssl rand -hex 32)
echo "DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}" > .env

# 3. Build and run
docker compose up -d --build

# 4. Run migrations
docker compose exec crawler python manage.py migrate --noinput

# 5. Seed production data (users, companies, qualifications, KBLI)
docker compose exec crawler python manage.py seed_data

# 6. Collect static files
docker compose exec crawler python manage.py collectstatic --noinput

# 7. Check status
docker compose ps
docker compose logs -f crawler
```

### Access

| URL | Description |
|-----|-------------|
| `http://localhost:8000` | Dashboard |
| `http://localhost:8000/admin` | Django Admin |

---

## 3. Manual Deployment (Ubuntu/Debian)

### Option A: One-Command Setup

```bash
chmod +x deploy.sh
sudo ./deploy.sh
```

### Option B: Step-by-Step

```bash
# 1. Install system dependencies
sudo apt update && sudo apt install -y \
    python3 python3-venv python3-pip \
    nginx git curl \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    fonts-liberation

# 2. Create service user
sudo useradd -r -m -s /bin/bash crawler

# 3. Deploy application
sudo -u crawler git clone <your-repo> /opt/spse-crawler

# 4. Setup Python environment
cd /opt/spse-crawler
sudo -u crawler python3 -m venv venv
sudo -u crawler venv/bin/pip install --upgrade pip
sudo -u crawler venv/bin/pip install -r requirements.txt
sudo -u crawler venv/bin/playwright install --with-deps chromium

# 5. Django setup
sudo -u crawler venv/bin/python manage.py migrate --noinput
sudo -u crawler venv/bin/python manage.py seed_data
sudo -u crawler venv/bin/python manage.py collectstatic --noinput

# 6. Start
sudo -u crawler venv/bin/gunicorn web_ui.wsgi:application --config gunicorn.conf.py
```

---

## 4. Seed Data

The project ships with a management command that creates all production data:

```bash
python manage.py seed_data
```

This creates:

### Users

| Email | Password | Role | Company |
|-------|----------|------|---------|
| `superadmin@spse.test` | `admin123` | superadmin | — |
| `admin@example.com` | `admin123` | superadmin | — |
| `admin@khansia.co.id` | `admin123` | company_admin | PT Khatulistiwa Nusantara Indonesia |
| `budhi@khansia.co.id` | `submit123` | submitter | PT Khatulistiwa Nusantara Indonesia |
| `admin@siberinsan.co.id` | `admin123` | company_admin | PT Solusi Informatika Bersama |
| `submitter@siberinsan.co.id` | `submit123` | submitter | PT Solusi Informatika Bersama |

### Companies

| Company | NIB | Modal Disetor | Penghasilan Tahunan |
|---------|-----|---------------|---------------------|
| PT Khatulistiwa Nusantara Indonesia | 0220208762392 | Rp 1 Miliar | Rp 4 Miliar |
| PT Solusi Informatika Bersama | NIB-PT002 | Rp 10 Miliar | Rp 55 Miliar |

### Qualifications (PT Khatulistiwa Nusantara Indonesia)

- **NIB 2020** — KBLI: 46100, 46511, 46512, 46521, 46523, 58200, 62019, 62029, 62090
- **Pengalaman Kerja**: I-SURE System (PT Telkom, 2026)
- **SDM**: Praba (PM, S2, 7 tahun), Trendy (S1, 7 tahun, Scrum)

### KBLI Master

Kode aktif: `62019`, `62090`, `62029` (target IT scoring) + 6 kode pendukung.

### Re-seed

```bash
python manage.py seed_data --flush   # Delete all, then re-create
```

---

## 5. Konfigurasi Environment

Buat file `.env`:

```bash
# Django
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
DJANGO_DEBUG=0

# PostgreSQL (optional — defaults to SQLite)
DATABASE_URL=postgres://spse_user:secret@localhost:5432/spse_crawler

# Crawler
SPSE_LOG_LEVEL=INFO
SPSE_HEADLESS=1

# AI Scoring (optional — falls back to rule-based)
AI_PROVIDER=openai
AI_BASE_URL=https://ai.sumopod.com/v1
AI_API_KEY=your-api-key
AI_MODEL=MiniMax-M2.7-highspeed
```

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | *(dev key)* | **Wajib diubah** |
| `DJANGO_DEBUG` | `1` | `0` untuk production |
| `DATABASE_URL` | *(empty, uses SQLite)* | PostgreSQL connection string |
| `SPSE_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |
| `SPSE_HEADLESS` | `1` | `1` = Playwright headless |
| `AI_PROVIDER` | `rule_based` | openai / gemini / anthropic / rule_based |

---

## 6. Nginx Reverse Proxy + SSL

```bash
# Copy nginx config
sudo cp nginx.conf /etc/nginx/sites-available/spse-crawler
sudo ln -sf /etc/nginx/sites-available/spse-crawler /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-available/spse-crawler  # edit server_name
sudo nginx -t && sudo systemctl reload nginx

# Enable HTTPS
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_DOMAIN
```

---

## 7. Systemd Service Management

```bash
sudo systemctl status spse-crawler
sudo systemctl restart spse-crawler
sudo journalctl -u spse-crawler -f
```

---

## 8. Backup & Restore

```bash
# Backup SQLite
cp /opt/spse-crawler/db.sqlite3 /opt/spse-crawler/db.sqlite3.backup.$(date +%Y%m%d)

# Restore
sudo systemctl stop spse-crawler
cp /path/to/backup.sqlite3 /opt/spse-crawler/db.sqlite3
sudo systemctl start spse-crawler
```

---

## 9. Monitoring & Troubleshooting

```bash
# Health check
curl -s http://localhost:8000/api/crawl-progress/ | python3 -m json.tool

# Common fix: restart if SQLite locked
sudo systemctl restart spse-crawler

# Re-install Playwright if browser crashes
venv/bin/playwright install --with-deps chromium
```

---

## 10. Crawler Commands

```bash
# Full crawl (all 600 instansi)
python cli.py crawl --all

# Crawl specific instansi
python cli.py crawl --instansi jakarta

# Multi-worker (4 workers)
python cli.py crawl --instansi jakarta --workers 4

# Recrawl missing enrichment fields
python cli.py recrawl-missing-details --limit 100 --workers 4

# Recrawl single tender
python cli.py recrawl-tender 2678 --instansi jakarta

# List configured instansi
python cli.py list-instansi
```
