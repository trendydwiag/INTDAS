# PLAYBOOK / RUNBOOK DEPLOYMENT PRODUCTION — SPSE Inaproc Crawler

Versi: 1.0 (Phase 6E)
Tanggal: 2026-08-31
Status: Final

Runbook ini berisi urutan deployment production yang **berbasis arsitektur
nyata** di repository (Docker + `entrypoint.sh`, atau manual Ubuntu + gunicorn),
dengan **hanya perintah yang benar-benar ada**. Seluruh perintah dikonfirmasi
dari `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `deploy.sh`,
`gunicorn.conf.py`, dan `web_ui.settings.py`.

> Sebelum mulai, pastikan Anda adalah **Superadmin** yang berwenang dan telah
> membaca `SOP_SUPERADMIN.md` serta `SOP_OPERASIONAL.md`.

---

## 0. ARSITEKTUR RUNTIME (ringkas)

- App Django: `web_ui.wsgi:application` (gunicorn, lihat `gunicorn.conf.py`).
- Entry (container): `entrypoint.sh` → migrasi + seed + collectstatic → `exec "$@"`.
- Scheduler dalam-proses (APScheduler) menyala di dalam proses web (`web/scheduler.py`).
- DB production: PostgreSQL via `DATABASE_URL` (wajib saat `DJANGO_DEBUG=0`;
  SQLite hanya dev).
- Reverse proxy: Nginx (lihat `nginx.conf`), TLS via certbot.

---

## 1. SEBELUM DEPLOYMENT — BACKUP

### 1.1 Backup database production (PostgreSQL)
```bash
# Catat nama DB & user dari DATABASE_URL (contoh: spse_crawler / spse_user)
# Backup dump lengkap ke file berstempel waktu:
pg_dump -U spse_user -h HOST -p 5432 -Fc spse_crawler > \
  /backup/spse_crawler_$(date +%Y%m%d_%H%M%S).dump
```

### 1.2 Backend SQLite (dev / bila masih memakai sqlite fallback)
```bash
cp db.sqlite3 db.sqlite3.backup.$(date +%Y%m%d_%H%M%S)
```

### 1.3 Verifikasi backup
```bash
# Untuk dump PostgreSQL — pastikan file terbaca & berisi:
ls -lh /backup/spse_crawler_*.dump
pg_restore -l /backup/spse_crawler_*.dump >/dev/null && echo "Dump OK"

# Sebaiknya: uji restore ke DB terpisah sekali sebelum deploy besar.
pg_restore -U spse_user -h HOST -d spse_crawler_verify /backup/spse_crawler_*.dump
```

### 1.4 Backup aplikasi & catat versi
```bash
# Catat git commit/versi yang sedang berjalan SEBELUM deploy:
git rev-parse HEAD
git describe --tags 2>/dev/null || echo "no tag"

# Catat state migrasi saat ini:
python manage.py showmigrations 2>/dev/null | tail -20
# (atau via docker: docker compose exec crawler python manage.py showmigrations)
```

➡️ Simpan catatan (commit + daftar migrasi lama) di file nota deployment.

---

## 2. DEPLOY KODE

### Opsi A: Docker (rekomendasi; `docker-compose.yml` + `Dockerfile`)
```bash
cd /opt/spse-crawler          # atau direktori proyek

# Tampilkan perubahan sebelum deploy (opsional)
git status
git diff --stat HEAD

git pull origin <branch>      # atau: git fetch && git checkout <commit>

# Build image baru
docker compose build

# Keluarkan container lama terlebih dahulu (jika memakai compose)
docker compose down
docker compose up -d
```

> `entrypoint.sh` otomatis menjalankan migrasi + seed + collectstatic saat
> container start, lalu `exec` perintah dari `docker-compose.yml`.

### Opsi B: Manual Ubuntu (lihat `deploy.sh` / `DEPLOYMENT.md`)
```bash
cd /opt/spse-crawler
git pull origin <branch>

# Update dependencies (bila ada perubahan requirements)
venv/bin/pip install -r requirements.txt
venv/bin/playwright install --with-deps chromium   # bila browser berubah

# Set env production (DJANGO_DEBUG=0, DATABASE_URL, dsb) sebelum lanjut
```

---

## 3. JALANKAN MIGRASI (URUTAN EKSAT)

Migrasi Phase 6 yang perlu diterapkan pada DB production:

```
companies/0003_alter_companyprofile_npwp_and_more   (non-PK, non-unique)
web/0014_tenderwinner_alamat_tenderwinner_harga_negosiasi_and_more
web/0015_tenderparticipant_company_and_more
```

Semua **additive** (menambah kolom nullable/ber-default, menambah index).
`web/0015` bergantung pada `companies/0003` DAN `web/0014`; Django menyelesaikan
urutan otomatis lewat graph dependensi.

```bash
# Docker
docker compose exec crawler python manage.py migrate --noinput

# Manual
venv/bin/python manage.py migrate --noinput
```

**Verifikasi:**
```bash
docker compose exec crawler python manage.py showmigrations
docker compose exec crawler python manage.py check
docker compose exec crawler python manage.py makemigrations --check
```
`makemigrations --check` harus mengeluarkan **No changes detected**.

---

## 4. KONFIGURASI ENVIRONMENT PRODUCTION

Pastikan (ini diverifikasi `web_ui/settings.py`; gagal → startup crash):
```bash
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<kunci aman>
ALLOWED_HOSTS=monitor-lpse.khansia.co.id
CSRF_TRUSTED_ORIGINS=https://monitor-lpse.khansia.co.id
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
SPSE_LOG_LEVEL=INFO
SPSE_HEADLESS=1
```

Jangan set `DJANGO_DEBUG=1` di production.

---

## 5. CHECK SISTEM

```bash
docker compose exec crawler python manage.py check
```
Harus: **System check identified no issues (0 silenced).**

```bash
docker compose exec crawler python manage.py makemigrations --check
```
Harus: **No changes detected.**

---

## 6. RESTART LAYANAN

```bash
# Docker: container sudah start via `up -d`; restart eksplisit bila perlu
docker compose restart crawler

# Manual (systemd), jika ada unit
sudo systemctl restart spse-crawler
sudo journalctl -u spse-crawler -f
```

> Scheduler APScheduler menyala di dalam proses web. Saat proses web restart,
> scheduler ikut restart. Pastikan hanya **satu** replika yang menjalankan
> scheduler (multi-replika akan memicu eksekusi crawl ganda; lock dalam-proses
> hanya mencegah dua job di proses yang sama).

---

## 7. VERIFIKASI SCHEDULER

```bash
# Status scheduler (tanpa auth, read-only ringan)
curl -s http://localhost:8000/api/status/ | python3 -m json.tool
curl -s http://localhost:8000/api/crawl-progress/ | python3 -m json.tool
```
Pastikan scheduler `running` sesuai konfigurasi.

---

## 8. SMOKE TEST

### 8.1 Health / halaman
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/     # 200
```

### 8.2 API dasar (DB)
```bash
curl -s "http://localhost:8000/api/results/?limit=5" | head -c 400
```

### 8.3 Auth
- `/login/` menampilkan form.
- Login superadmin berhasil.

---

## 9. VERIFIKASI PIPELINE WINNER

1. Pastikan migrasi `web/0014`+`0015` sudah applied (kolom `harga_*`, `npwp`,
   `company`, `resolution_status` ada di `TenderWinner`).
2. Trigger crawl/tunggu crawl otomatis.
3. Periksa log untuk `WINNER_STORED` (info) dan `ENTITY_AUTO_LINK` (info).
4. Cek data pemenang:
   ```bash
   # contoh via Django shell
   docker compose exec crawler python -c "
   import os
   os.environ.setdefault('DJANGO_SETTINGS_MODULE','web_ui.settings')
   import django; django.setup()
   from spse_crawler.web.models import TenderWinner
   print('winners=', TenderWinner.objects.count())
   print(TenderWinner.objects.values('company_name','resolution_status')[:5])
   "
   ```
5. Pastikan pemenang lama tidak hilang (idempotent; tidak ada penghapusan).

---

## 10. VERIFIKASI INTELLIGENCE PERUSAHAAN

```bash
# Auth login dulu (cookie/sesi superadmin), lalu:
curl -b cookies.txt \
  "http://localhost:8000/api/intelligence/company/<company_id>/" \
  | python3 -m json.tool
```
Periksa: `total_participation`, `total_wins`, `win_rate`, `coverage`
(`participation_coverage`, `winner_coverage`, `winner_coverage_basis`), dan
`data_available`.

---

## 11. MONITOR LOG

```bash
docker compose logs -f crawler
# Manual:
sudo journalctl -u spse-crawler -f
```
Perhatikan marker:
- `[SCHEDULER]` crawl/pipeline sukses/gagal.
- `WINNER_FETCH_UNAVAILABLE` / `WINNER_PARSE_EMPTY` (debug, wajar bila pemenang
  tak tersedia).
- `WINNER_STORED`, `ENTITY_AUTO_LINK`, `ENTITY_ID_CONFLICT`, `ENTITY_CANDIDATE`,
  `ENTITY_UNRESOLVED`.
- `[PURGE] non_retained` — hanya record stale.

NPWP lengkap **tidak pernah** masuk log; hanya bentuk ter-mask (`11****11`).

---

## 12. DEKLARASI DEPLOYMENT BERHASIL

Declare sukses hanya jika SEMUA terpenuhi:
- [ ] Backup dibuat & diverifikasi.
- [ ] Migrasi applied tanpa error; `makemigrations --check` = No changes.
- [ ] `manage.py check` = 0 issues.
- [ ] Health check 200.
- [ ] Scheduler berjalan.
- [ ] Crawl menghasilkan data; tidak ada error beruntun.
- [ ] Data pemenang & peserta lama tidak hilang.
- [ ] Intelligence perusahaan mengembalikan `data_available` benar.

---

## 13. ROLLBACK

### 13.1 Rollback kode
```bash
git checkout <commit_sebelumnya>

# Docker
docker compose build && docker compose down && docker compose up -d
# Manual
sudo systemctl restart spse-crawler
```

### 13.2 Strategi rollback migrasi
- `web/0014`, `web/0015`, `companies/0003` semuanya **additive** (menambah kolom /
  index). Menghapus kolom baru berisiko bila sudah ada data pada kolom tersebut.
- Jika terjadi kegagalan **tengah migrasi** dan DB tidak konsisten:
  - **JANGAN** paksa `migrate` lagi tanpa pemeriksaan.
  - Pulihkan dari backup (di bawah).
- Jika aplikasi baru dapat berjalan tapi ada bug, prefer **rollback kode** dan
  **biarkan migrasi tetap applied** (kolom baru tidak mengganggu kode lama).
  Ini lebih aman daripada membatalkan migrasi.
- **Kapan TIDAK membalik migrasi:** ketika sudah ada data baru pada kolom yang
  ditambahkan. Membalik (menghapus kolom) akan menghapus data tersebut.

### 13.3 Pemulihan database (restore)
```bash
# PostgreSQL: buang DB lama, buat ulang, restore
# (lakukan hanya saat aplikasi distop / isolated)
docker compose stop crawler        # atau: sudo systemctl stop spse-crawler
dropdb -U spse_user -h HOST spse_crawler
createdb -U spse_user -h HOST -O spse_user spse_crawler
pg_restore -U spse_user -h HOST -d spse_crawler /backup/spse_crawler_TARGET.dump
docker compose start crawler        # atau: sudo systemctl start spse-crawler
```
Setelah restore, jalankan `manage.py migrate --noinput` lalu `manage.py check`
untuk menyelaraskan state `django_migrations` dengan aplikasi.

### 13.4 Melindungi data historis
- Selalu backup sebelum setiap deploy.
- Jangan pernah menjalankan `api/flush-records/` dengan mode `all`/`inactive` di
  production kecuali ada persetujuan dan backup terverifikasi.
- Jangan pernah mem-backfill data lama secara otomatis/destruktif.

---

## 14. VERIFIKASI AKHIR SETELAH ROLLBACK (bila rollback dilakukan)

1. `manage.py check` = 0 issues.
2. `makemigrations --check` = No changes (sesuai versi).
3. Data historis (tender awarded/peserta/pemenang/intelligence) utuh.
4. Scheduler & crawl normal.
5. Login/awth berfungsi.

---

## 15. PERSIAPAN STRUKTUR + SEED (DIMUAT MANUAL)

Jika Anda ingin **membangun database production dari nol secara manual** (tanpa
membawa data dev) — struktur = migrasi, seed = `seed_data` — ikuti ini.

### 15.1 Struktur (skema) — via migrasi
Struktur database sepenuhnya didefinisikan oleh migrasi Django (tidak ada `.sql`
terpisah). Migrasi Phase 6 (`companies/0003`, `web/0014`, `web/0015`) sudah ada
di repo dan **semua additive**. Terapkan pada DB kosong:

```bash
# Docker
docker compose exec crawler python manage.py migrate --noinput

# Manual
venv/bin/python manage.py migrate --noinput
```

Verifikasi struktur:
```bash
python manage.py showmigrations          # pastikan web/0014, web/0015, companies/0003 ter-apply
python manage.py makemigrations --check  # harus: No changes detected
python manage.py check                   # harus: 0 issues
```

### 15.2 Seed (data awal) — `python manage.py seed_data`
Command seed **idempoten** (aman dijalankan ulang; tidak menduplikasi) dan
menghasilkan: 2 perusahaan, kualifikasi, 6 user (2 superadmin, 2 company_admin,
2 submitter), dan 9 KBLI aktive.

```bash
# Docker
docker compose exec crawler python manage.py seed_data

# Manual
venv/bin/python manage.py seed_data
```

**Password seed — sekarang STABIL** (tidak lagi acak setiap run):
- Default terdokumentasi: `SpseSeed!2026` (untuk SEMUA akun seed).
- Override urutan prioritas: `--password <x>` CLI > env `SEED_PASSWORD` >
  env `SPSE_SEED_PASSWORD` > default.
- Contoh menetapkan password kuat:
  ```bash
  export SEED_PASSWORD='GantiSaya!Am@n123'
  python manage.py seed_data
  # atau
  python manage.py seed_data --password 'GantiSaya!Am@n123'
  ```
- **Wajib:** ganti password setelah login pertama di production (jangan biarkan
  `SpseSeed!2026`).

Akun seed (login menggunakan **email**, sesuai `EmailBackend`):

| Email | username | role |
|---|---|---|
| superadmin@spse.test | superadmin | superadmin |
| admin@example.com | admin | superadmin |
| admin@khansia.co.id | admin_pt1 | company_admin |
| budhi@khansia.co.id | submitter_pt1 | submitter |
| admin@siberinsan.co.id | admin_pt2 | company_admin |
| submitter@siberinsan.co.id | submitter_pt2 | submitter |

### 15.3 Alternatif: seed sebagai fixture (`loaddata`)
Jika ingin memuat seed sebagai file, ekspor fixture lalu muat (setelah struktur
ada):

```bash
# Ekspor (satu kali, dari DB yang sudah di-seed):
python manage.py dumpdata \
    accounts.User \
    companies.CompanyProfile \
    companies.CompanyQualification \
    web.KbliMaster \
    --indent 2 > seed.json

# Muat pada DB target (structure harus sudah migrate):
python manage.py loaddata seed.json
```

> Catatan fixture: hash password ikut tersimpan, sehingga login tetap stabil.
> Jalankan `loaddata` setelah `migrate`, pada DB kosong atau DB yang sudah
> konsisten. Jangan gunakan `--flush` tanpa backup.

---

## LAMPIRAN — DAFTAR PERINTAH NYATA YANG DIGUNAKAN

| Fungsi | Perintah |
|---|---|
| Migrasi | `python manage.py migrate --noinput` (atau `docker compose exec crawler ...`) |
| Cek | `python manage.py check` |
| Cek migrasi pending | `python manage.py makemigrations --check` |
| Daftar migrasi | `python manage.py showmigrations` |
| Seed data | `python manage.py seed_data` |
| Collect static | `python manage.py collectstatic --noinput` |
| Jalankan server | `venv/bin/gunicorn web_ui.wsgi:application --config gunicorn.conf.py` |
| Build & up (Docker) | `docker compose build` / `docker compose up -d` |
| Log | `docker compose logs -f crawler` / `journalctl -u spse-crawler -f` |
| Backup PG | `pg_dump -U <user> -h <host> -Fc <db> > file.dump` |
| Restore PG | `pg_restore -U <user> -h <host> -d <db> file.dump` |
| Seed data | `python manage.py seed_data` |
| Seed dgn password | `python manage.py seed_data --password '<x>'` |
| Ekspor seed (fixture) | `python manage.py dumpdata accounts.User companies.CompanyProfile companies.CompanyQualification web.KbliMaster --indent 2 > seed.json` |
| Muat seed (fixture) | `python manage.py loaddata seed.json` |
