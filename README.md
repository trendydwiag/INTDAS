# SPSE Inaproc — Multi-Tenant Scraper & Intelligence Dashboard

> **v0.0.4** — Sistem pemantauan dan manajemen tender pengadaan pemerintah Indonesia
> dari portal SPSE Inaproc, dengan AI scoring, multi-tenant auth, dan workflow submitter.

---

## Fitur Utama

### Crawler & Data
- **Two-stage crawling**: DataTables discovery → Playwright detail scraping
- **600 instansi** pemerintah didukung (pusat & daerah)
- **Enrichment**: jadwal tahapan, syarat kualifikasi, peserta, lokasi pekerjaan
- **Multi-worker parallel**: hingga N worker Playwright secara concurrent
- **Anti-bot stealth**: playwright-stealth + session warming + human simulation
- **IT priority scoring**: KBLI 62xxx (100) → keyword IT (80) → exclusion (0)

### AI & Intelligence
- **Tingkat Kecocokan**: rule-based scoring (TF-IDF + keyword matching) — zero cost
- **Optional LLM**: OpenAI-compatible (Sumopod), Gemini free tier, Anthropic
- **Persistent AI scores**: tersimpan di DB, tidak hilang saat reload
- **3-layer filtering**: Stage 1 skip non-eligible → Stage 2 reject non-prakualifikasi → orchestrator

### Multi-Tenant Auth & RBAC
- **3 roles**: superadmin, company_admin, submitter
- **Multi-KBLI**: perusahaan bisa punya banyak kode KBLI
- **Company profile**: NIB, NPWP, modal, penghasilan, kualifikasi
- **Dynamic qualifications**: izin usaha, SBU, pengalaman kerja, SDM, sertifikasi

### Tender Submission Workflow
- **Status tracking**: belum_diproses → sudah_submit → menang/gagal
- **Submitter workflow**: button langsung di modal detail tender
- **Audit log**: setiap perubahan status tercatat

### Web Dashboard
- **Responsive UI**: desktop, tablet, mobile
- **2-column modal**: info umum + jadwal | syarat + AI + submitter
- **Faceted search**: filter HPS, jenis pengadaan, KBLI, tahap — counters cross-filtered
- **Report Summary**: omset potensi, omset submitted, omset menang, win rate
- **Dashboard admin**: KBLI CRUD, flush records, scheduler control

---

## Tech Stack

| Komponen | Teknologi |
|----------|-----------|
| Backend | Python 3.12, Django 5.2 |
| Crawler | Playwright + httpx + selectolax |
| Anti-Bot | playwright-stealth v2.0.3 |
| Database | PostgreSQL 15+ (prod) / SQLite (dev) |
| AI | OpenAI SDK / google-generativeai / anthropic SDK |
| Scheduler | APScheduler 3.10 |
| CLI | Typer + Rich |
| WSGI | Gunicorn |
| Proxy | Nginx |
| Container | Docker + Docker Compose |

---

## Quick Start

### Docker

```bash
export DJANGO_SECRET_KEY=$(openssl rand -hex 32)
echo "DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}" > .env
docker compose up -d --build
docker compose exec crawler python manage.py migrate --noinput
docker compose exec crawler python manage.py seed_data
```

### Manual

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r spse_crawler/requirements.txt
playwright install --with-deps chromium
python manage.py migrate --noinput
python manage.py seed_data
python manage.py runserver
```

---

## Akun Default

| Email | Password | Role |
|-------|----------|------|
| `superadmin@spse.test` | `admin123` | Superadmin |
| `admin@khansia.co.id` | `admin123` | Admin Perusahaan |
| `budhi@khansia.co.id` | `submit123` | Submitter |
| `admin@siberinsan.co.id` | `admin123` | Admin Perusahaan |
| `submitter@siberinsan.co.id` | `submit123` | Submitter |

Lihat [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) untuk panduan lengkap.

---

## Struktur Proyek

```
spse_crawler/
├── accounts/         # User model, auth, RBAC
├── ai_match/         # AI qualification matching
├── audit/            # Audit log
├── companies/        # Company profile & qualifications
├── config/           # Pydantic settings, 600 instansi codes
├── core/             # SpseHttpClient, PlaywrightEngine, stealth
├── models/           # Pydantic scrape schemas
├── parsers/          # Discovery (DataTables) + Detail (Playwright)
├── services/         # Priority scorer, progress tracker, purger
├── storage/          # CSV/Excel exporter
├── submissions/      # Tender submission workflow
├── tasks/            # Placeholder
├── web/              # Django views, templates, scheduler, management commands
├── cli.py            # Typer CLI (crawl, recrawl, list-instansi)
└── main.py           # Crawl entrypoint
```

---

## CLI Reference

```bash
# Full crawl (all 600 instansi)
python cli.py crawl --all

# Specific instansi, 4 workers
python cli.py crawl --instansi jakarta --workers 4

# Recrawl missing enrichment fields
python cli.py recrawl-missing-details --limit 100 --workers 4

# Recrawl single tender
python cli.py recrawl-tender 2678 --instansi jakarta
```

---

## Lisensi

Internal use only — PT Khatulistiwa Nusantara Indonesia & PT Solusi Informatika Bersama.
