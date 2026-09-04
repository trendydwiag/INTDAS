# SOP SUPERADMIN — SPSE Inaproc Crawler & Intelligence Dashboard

Versi: 1.0 (Phase 6E)
Tanggal: 2026-08-31
Status: Final

SOP ini khusus untuk peran **`superadmin`** — satu-satunya peran yang dapat
menjalankan operasi sistem, user, crawler, scheduler, purge, KBLI, dan migrasi.

> **Bacaan wajib:** mulailah dari `SOP_OPERASIONAL.md` untuk pemahaman peran,
> matriks fitur, dan prosedur insiden. Dokumen ini berisi prosedur mendetail dan
> penanda **AMAN** vs **BERISIKO / DESTRUKTIF**.

---

## 1. LOGIN SUPERADMIN

1. Buka `/login/`.
2. Masukkan email superadmin dan password.
3. Klik **Login**.
4. Pastikan Anda diarahkan ke Dashboard.
5. Verifikasi peran tampil sebagai Superadmin.

> Akun superadmin dibuat melalui `python manage.py seed_data` (idempoten) atau
> Django admin. Jangan membuat akun superadmin kedua tanpa dokumentasi.

---

## 2. MANAJEMEN USER & ROLE

Superadmin mengelola user melalui **Django admin** (`/admin/`) karena di sana
model `accounts.User` terdaftar.

1. Buka `/admin/`.
2. Masuk ke bagian **Users** (model `User`).
3. Pilih user yang akan diubah.
4. Atur:
   - `email` (unik),
   - `role` (superadmin / company_admin / submitter),
   - `company` (perusahaan, untuk company_admin/submitter),
   - `is_active`, `is_staff`, `is_superuser`.
5. Klik **Save**.

### Verifikasi
- User dapat login dengan email + password.
- Role yang dipasang sesuai (company_admin harus punya `company`).
- Peran submitter/company_admin **tidak boleh** punya `is_superuser`.

### Keamanan
- Hanya beri `is_staff`/`is_superuser` pada account yang benar.
- Segera reset password bila akun diduga bocor: pilih user → "password" → set
  password baru → **Save**.

---

## 3. KONFIGURASI SISTEM (ENVIRONMENT)

Konfigurasi sistem ada di variabel environment (`.env` / env service). Superadmin
bertanggung jawab memastikan nilai production aman:

| Variabel | Wajib production | Catatan |
|---|---|---|
| `DJANGO_DEBUG` | `0` | DEBUG=true berbahaya (membeberkan stack trace) |
| `DJANGO_SECRET_KEY` | Ya | fail-fast bila kosong; jangan pakai kunci dev |
| `ALLOWED_HOSTS` | Ya | fail-fast bila kosong |
| `CSRF_TRUSTED_ORIGINS` | Ya | contoh `https://monitor-lpse.khansia.co.id` |
| `DATABASE_URL` | Ya (PostgreSQL) | fail-fast bila kosong (SQLite hanya untuk dev) |

1. Cek `.env` / environment service.
2. Pastikan `DJANGO_DEBUG=0`.
3. Pastikan `DJANGO_SECRET_KEY` bukan nilai dev.
4. Pastikan `ALLOWED_HOSTS` dan `CSRF_TRUSTED_ORIGINS` berisi domain benar.
5. Pastikan `DATABASE_URL` mengarah ke PostgreSQL production.
6. **JANGAN** pernah menulis nilai rahasia di log atau dokumen.

---

## 4. KONFIGURASI CRAWLER

Crawler dikonfigurasi lewat settings/`.env` (bukan UI). Nilai yang relevan:

- `SPSE_LOG_LEVEL=INFO` (jangan DEBUG di production untuk mengurangi beban log).
- `SPSE_HEADLESS=1`.

Pemeriksaan rutin:
1. Buka `/api/status/` (GET) untuk melihat status scheduler.
2. Buka `/api/crawl-progress/` untuk melihat progres crawl.
3. Bandingkan dengan log `[SCHEDULER]`.

---

## 5. CRAWL MANUAL

Operasi ini **AMAN** terhadap data (upsert-only; tidak menghapus).

### 5.1 Mulai crawl via UI/API
1. Login sebagai superadmin.
2. Buka endpoint `api/start-crawl/` (POST) dengan parameter:
   - `instansi` (mis. `all` atau `jakarta`),
   - `workers` (mis. `1`–`5`).
3. Kirim header `X-CSRFToken` (diambil dari cookie) — wajib karena endpoint
   superadmin + CSRF.
4. Periksa respons:
   - `200` → crawl mulai.
   - `409` → crawl sudah berjalan (jangan ulangi).
5. Pantau `/api/crawl-progress/`.

### 5.2 Trigger crawl otomatis (scheduler)
1. `/api/trigger/` (POST, superadmin) → memicu job `hourly_crawl` segera.
2. Respons `200` → terjadwal.
3. Pantau log.

---

## 6. MONITORING SCHEDULER

1. `api/status/` (GET) → status scheduler.
2. `api/status/toggle/` (POST) → nyalakan/matikan scheduler.
3. Periksa: apakah ada indikasi "Crawl already in progress" di log (lock ganda
   dicegah otomatis).

---

## 7. MONITORING DATA HISTORIS & INTELLIGENCE

1. Periksa jumlah `TenderResult`, `TenderParticipant`, `TenderWinner`,
   `CompanyProfile`.
2. Periksa bahwa tender awarded/decided/completed **tetap tersimpan**
   (retensi historis) dan **tidak terhapus** oleh purge otomatis.
3. Periksa log `[PURGE] non_retained` untuk memastikan hanya record stale yang
   dihapus.
4. Periksa log entity resolution (`ENTITY_AUTO_LINK`, `ENTITY_ID_CONFLICT`,
   `ENTITY_CANDIDATE`, `ENTITY_UNRESOLVED`) untuk kewajaran.

---

## 8. OPERASI PURGE (HAPUS RECORD)

**⚠️ BERISIKO / DESTRUKTIF — lakukan dengan sangat hati-hati.**

Endpoint: `api/flush-records/` (POST, **superadmin only**, CSRF wajib).
Parameter `mode`:

| Mode | Apa yang dihapus | Risiko |
|---|---|---|
| `non_prakualifikasi` | Semua tender `is_prakualifikasi=False` | **BERISIKO** — bisa menghapus banyak data |
| `inactive` | Tender dengan tahap selesai/kontrak/batal/gagal | **BERISIKO** — menghapus riwayat |
| `all` | **Seluruh** basis data | **SANGAT DESTRUKTIF** — reset total |
| (purge otomatis scheduler) `flush_non_retained` | Hanya record non-prakualifikasi yang TIDAK retained (aborted/stale) | **AMAN** — dipakai otomatis tiap crawl |

### Kapan boleh digunakan
- `non_prakualifikasi` / `inactive` / `all` **HANYA** setelah persetujuan dan
  setelah **backup** database diverifikasi.
- Jangan gunakan `all` untuk operasi rutin.

### Data yang bisa dihapus
Menghapus `TenderResult` akan **meng-`CASCADE`** ke `TenderParticipant`,
`TenderWinner`, `OpportunityScore`, `IntelligenceJob`, oll terkait. `CompanyProfile`
berdiri sendiri dan tidak ikut terhapus (FK `SET_NULL`).

### Mengapa purge otomatis aman
`flush_non_retained()` hanya menghapus tender `is_prakualifikasi=False` yang
tidak cocok dengan kata kunci retained (aktif/awarded/decided/completed). Tender
**awarded, decided, completed, dan aktif selalu dipertahankan** sehingga
intelligence graph (peserta/pemenang/company) tidak hancur. **Karena itu,
jangan pernah mengganti purge otomatis dengan mode `inactive`/`all` untuk tugas
rutin.**

### Prosedur purge manual (jika benar-benar perlu)
1. **Backup** database terlebih dahulu (lihat RUNBOOK).
2. Verifikasi backup dapat dibaca.
3. Login superadmin.
4. POST `api/flush-records/` dengan `mode` yang diinginkan + `X-CSRFToken`.
5. Catat respons `deleted_count` dan `remaining_count`.
6. **Verifikasi** jumlah `CompanyProfile`/`TenderParticipant`/`TenderWinner`
   setelahnya.
7. Catat di log/nota.

---

## 9. MIGRASI & DEPLOYMENT

Migrasi skema **tidak** dilakukan dari UI. Hanya lewat command Django/CI.
Lihat **`DEPLOYMENT_RUNBOOK.md`** untuk urutan lengkap.

Aturan superadmin:
- Selalu **backup** sebelum migrasi.
- Jalankan `python manage.py migrate --noinput` dalam urutan resolusi Django
  (dependensi dijamin otomatis).
- Jalankan `python manage.py check`.
- Jangan pernah `migrate` saat ada proses lama berjalan tanpa koordinasi.
- Jika migrasi gagal: berhenti, jangan paksa; pulihkan sesuai RUNBOOK bagian
  rollback.

---

## 10. BACKUP & PEMULIHAN

Pengoper khusus (Superadmin), detail lengkap di `DEPLOYMENT_RUNBOOK.md`.
Singkat:
1. **Produksi (PostgreSQL):** gunakan `pg_dump` sebelum setiap deploy/migrasi.
2. **Dev (SQLite):** salin file `db.sqlite3`.
3. Simpan backup di lokasi terpisah dan aman.
4. Uji pemulihan pada lingkungan terpisah sebelum mengandalkannya.
