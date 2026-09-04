# SOP OPERASIONAL — SPSE Inaproc Crawler & Intelligence Dashboard

Versi: 1.0 (Phase 6E)
Tanggal: 2026-08-31
Status: Final — READY FOR PRODUCTION AWAITING DEPLOYMENT

Dokumen ini adalah **Standar Operasional Prosedur (SOP)** untuk operator manusia
aplikasi SPSE Inaproc Crawler & Intelligence Dashboard. Tulis dengan bahasa
Indonesia yang sederhana namun presisi. Seluruh nama fitur, menu, tombol, dan
tampilan yang disebutkan adalah **nyata dan telah diverifikasi dari kode**.

> Catatan penting: sistem ini adalah **Scraper Multi-Tenant + Dashboard
> Intelligence** untuk memantau tender SPSE (inaproc), menangkap data peserta dan
> pemenang, serta menampilkan intelligence perusahaan. Data yang ditampilkan
> **berasal dari halaman SPSE publik** (peserta, pemenang) dan **tidak boleh
> diubah manual** oleh operator kecuali dengan prosedur yang disetujui.

---

## 1. PERAN (ROLE) YANG ADA

Sistem ini hanya memiliki **tiga peran** yang benar-benar diterapkan di dalam
kode (`spse_crawler/accounts/models.py`):

| Peran (role) | Nama di Sistem | Tujuan |
|---|---|---|
| `superadmin` | **Superadmin** | Mengelola sistem, user, KBLI, crawler, scheduler, purge, migrasi |
| `company_admin` | **Admin Perusahaan** (company_admin) | Mengelola profil & kualifikasi perusahaannya, memonitor tender & intelligence, menyetujui/melihat submission |
| `submitter` | **Admin Submitter** (submitter) | Menginput/memperbarui status submission untuk perusahaannya saja |

**TIDAK ada** peran bernama "Admin", "Operator", "Analyst", atau "Viewer" di
dalam sistem. Jika Anda mencari peran seperti itu, gunakan peran yang paling
sesuai di atas. Peran `company_admin` dan `submitter` bersama-sama menutupi
fungsi "operator analis" harian untuk sebuah perusahaan.

Fitur **baca** (dashboard tender, intelligence tender, intelligence perusahaan,
data pemenang, kompetisi) dapat diakses oleh `company_admin` dan `submitter`
untuk **perusahaan mereka sendiri**. Superadmin dapat mengakses semua perusahaan.

---

## 2. HALAMAN & MENU YANG ADA (VERIFIKASI KODE)

| Area | Alamat (URL) | Siapa yang boleh |
|---|---|---|
| Login | `/login/` | Semua |
| Dashboard tender | `/` | Boleh tanpa login (mode publik); akan lebih lengkap setelah login |
| Admin Django | `/admin/` | Superadmin (staff) |
| Lengkapi profil perusahaan | melalui API `api/company/*` dan `api/company/*/qualifications/*` | company_admin (perusahaan sendiri), superadmin (semua) |
| Submission status | `api/submission/*` | submitter/company_admin (perusahaan sendiri), superadmin (semua) |
| AI match result | `api/match/*` | Perusahaan sendiri |
| Intelligence perusahaan | `api/intelligence/company/<id>/` | superadmin semua; company_admin/submitter hanya perusahaan sendiri (403 selain itu) |
| Intelligence pemenang tender | `api/intelligence/tender/<id>/winner/` | Semua yang login (data global/tender) |
| Intelligence kompetisi tender | `api/intelligence/tender/<id>/competition/` | Semua yang login (data global/tender) |
| Watchlist | `api/watchlist/*` | Harus login; scoped per perusahaan |
| Crawl: mulai otomatis | `api/start-crawl/` | **Superadmin saja** |
| Crawl: trigger manual | `api/trigger/` | **Superadmin saja** |
| Scheduler: on/off | `api/status/toggle/` | **Superadmin saja** |
| Purge records | `api/flush-records/` | **Superadmin saja** |
| KBLI CRUD | `api/kbli/*/create|update|delete` | **Superadmin saja** |
| Download CSV/Excel | `api/download/csv/`, `api/download/excel/` | Publik (tidak berisi NPWP) |
| Status / progress crawl | `api/status/`, `api/crawl-progress/` | Publik (read-only ringan) |

Semua endpoint crawler/purge/KBLI mewajibkan **CSRF** dan **superadmin**
(decorator `require_superadmin` + `@require_POST`).

---

## 3. MATRIKS FITUR PER PERAN

| Fitur | Superadmin | Admin Perusahaan (company_admin) | Admin Submitter (submitter) |
|---|---|---|---|
| Login | Boleh | Boleh | Boleh |
| Dashboard tender (baca) | Boleh | Boleh | Boleh |
| Lihat intelligence perusahaan sendiri | Boleh | Boleh | Boleh |
| Lihat intelligence perusahaan lain | Boleh | **Tidak boleh** (403) | **Tidak boleh** (403) |
| Lihat pemenang/kompetisi tendar | Boleh | Boleh | Boleh |
| Kelola profil & kualifikasi perusahaan sendiri | Boleh | Boleh | **Tidak boleh** |
| Input/update status submission (own company) | Boleh | Boleh | Boleh |
| Setujui / lihat semua submission perusahaannya | Boleh | Boleh | Baca saja |
| Watchlist (own company) | Boleh | Boleh | Boleh |
| Kelola user & role | Boleh | Tidak boleh | Tidak boleh |
| Kelola KBLI (create/update/delete) | Boleh | Tidak boleh | Tidak boleh |
| Mulai / trigger crawl | Boleh | Tidak boleh | Tidak boleh |
| Toggle scheduler | Boleh | Tidak boleh | Tidak boleh |
| Purge records | Boleh | Tidak boleh | Tidak boleh |
| Migrasi / deploy / backup | Boleh | Tidak boleh | Tidak boleh |

---

## 4. SOP LOGIN & LOGOUT

### 4.1 Login
1. Buka alamat aplikasi di browser (mis. `https://monitor-lpse.khansia.co.id`).
2. Klik halaman login `/login/`.
3. Masukkan **Email** pada kolom "Email".
4. Masukkan **Password**.
5. Klik tombol **Login**.
6. Periksa: Anda diarahkan ke Dashboard.
7. Jika muncul pesan "Email atau password salah":
   - Pastikan email sesuai; periksa peran aktif.
   - Jika lupa password, hubungi **Superadmin**; jangan membuat akun sendiri.

> Keamanan: jangan pernah berbagi akun. Jangan simpan password di catatan publik.

### 4.2 Logout
1. Klik tombol **Logout** di menu (POST `/logout/`).
2. Pastikan kembali ke halaman login.
3. Jika memakai komputer bersama, tutup browser sepenuhnya.

---

## 5. POLA KERJA HARIAN (SEMUA PERAN) — Hari Kerja

Waktu disesuaikan dengan jadwal scheduler (crawl otomatis tiap **1 jam**, pipeline
intelligence tiap **10 menit**). Saran mulai pukul 08.00 WIB, lalu cek kedua kalinya
setelah jadwal crawl.

```
08:00
 ↓
1. Cek kesehatan sistem (halaman Dashboard termuat normal).
2. Cek status scheduler (indikator "running").
3. Cek crawl terakhir sukses/tidak (jika ada indikator job).
4. Cek error crawl pada log (Superadmin).
5. Cek tender baru (Dashboard, tab Aktif / Prakualifikasi).
6. Cek data peserta masuk (bagi yang memantau kompetisi).
7. Cek data pemenang masuk (bagi yang memantau pemenang).
8. Cek ketersediaan intelligence perusahaan.
9. Tinjau anomali (tender mencurigakan, data kosong).
10. Catat insiden bila ada (lihat SOP Insiden).
```

Jika muncul anomali, ikuti **SOP Insiden** (bagian 8) — jangan langsung ubah data.

---

## 6. SOP PERAN ADMIN PERUSAHAAN (company_admin)

Tujuan: mengelola profil & kualifikasi perusahaan sendiri, dan memonitor
intelligence & hasil kompetisi.

### 6.1 Memeriksa & melengkapi profil perusahaan
1. Login sebagai company_admin.
2. Buka halaman profil perusahaan / kelola perusahaan (menu perusahaan).
3. Periksa **NIB** sudah terisi dan benar.
4. Periksa **NPWP** perusahaan sudah terisi (tanpa karakter `*`).
5. Periksa **Modal Disetor** dan **Penghasilan Tahunan**.
6. Klik **Simpan** bila ada perubahan.
7. Verifikasi: data tersimpan (tampilan memuat ulang).

> Prosedur ini mewajibkan NPWP yang benar dan tidak ter-mask karena ini kunci
> entity resolution otomatis (lihat bagian 7). NPWP yang berisi `*`/**tidak bisa**
> digunakan untuk pencocokan otomatis.

### 6.2 Mengelola kualifikasi perusahaan
1. Buka bagian Kualifikasi perusahaan.
2. Tambahkan kualifikasi (KBLI, pengalaman, SDM) sesuai data sah perusahaan.
3. Klik **Simpan** setiap kualifikasi.
4. Pastikan KBLI yang relevan aktif.

### 6.3 Memonitor intelligence perusahaan
1. Buka halaman intelligence perusahaan (perusahaan sendiri).
2. Periksa **total_participation** (jumlah partisipasi tertangkap).
3. Periksa **total_wins** (jumlah kemenangan tertangkap).
4. Periksa **win_rate** dan **win_rate_basis**.
5. Periksa bagian **coverage**:
   - `participation_coverage` = jumlah partisipasi tertangkap.
   - `winner_coverage` = sebagian kecil partisipasi yang menghasilkan kemenangan.
   - `winner_coverage_basis` = keterbatasan (hanya data yang memang tertangkap).
6. **Interpretasi yang benar:** win-rate adalah `kemenangan ÷ partisipasi yang
   tertangkap`, **BUKAN** seluruh jagat tender. Jangan melaporkan win-rate sebagai
   angka otoritatif national tanpa menyebut keterbatasan ini.

### 6.4 Menafsirkan status identitas (entity resolution)
Lihat **bagian 7** untuk arti `AUTO_LINK`, `CANDIDATE_ONLY`, `IDENTITY_CONFLICT`,
`UNRESOLVED`.

### 6.5 Apa yang tidak boleh dilakukan Admin Perusahaan
- Tidak boleh melihat perusahaan lain (mendapat 403).
- Tidak boleh menjalankan crawl / purge / mengubah KBLI / mengubah user.
- Tidak boleh mengubah data sumber peserta/pemenang.

---

## 7. SOP IDENTITAS & ENTITY RESOLUTION (PENTING UNTUK SEMUA ANALIS)

Entity resolution mencocokkan peserta/pemenang dari SPSE ke `CompanyProfile`
(sistem). Pencocokan **sangat ketat dan konservatif**:

| Status | Arti | Dampak |
|---|---|---|
| `EXACT_NPWP` | NPWP persis sama dengan satu perusahaan | **AUTO_LINK** (terhubung otomatis) |
| `EXACT_OFFICIAL_IDENTIFIER` | NIB/identifier resmi persis satu perusahaan | **AUTO_LINK** |
| `CANDIDATE_ONLY` | Nama sama tapi identitas belum terbukti | **TIDAK** terhubung otomatis |
| `IDENTITY_CONFLICT` | Lebih dari satu perusahaan dengan key sama / nama sama NPWP beda | **TIDAK** terhubung otomatis |
| `UNRESOLVED` | Tidak ada identitas / tidak ada kandidat | **TIDAK** terhubung otomatis |

**Aturan emas (WAJIB dipahami):**
> **Nama perusahaan saja TIDAK BOLEH dianggap sebagai identitas perusahaan yang
> sama.** Nama mirip (`"PT ABC"` vs `"PT ABC"`) tidak pernah menghasilkan
> terhubung otomatis. Pengelompokan hanya dilakukan jika NPWP/NIB persis cocok.

Praktik analis saat melihat data:
1. Jika `resolution_status = EXACT_NPWP` / `EXACT_OFFICIAL_IDENTIFIER` →
   data terhubung dengan aman.
2. Jika `resolution_status = CANDIDATE_ONLY` atau `IDENTITY_CONFLICT` →
   data **belum terhubung**; ditampilkan sebagai kandidat saja. Jangan dianggap
   sama.
3. Jika Anda melihat dua perusahaan dengan nama sama tetapi NPWP berbeda →
   **keduanya dianggap berbeda** (bukan salah satu).

---

## 8. SOP PENANGANAN INSIDEN

Untuk setiap insiden, ikuti alur: **Gejala → Pemeriksaan → Tindakan → Verifikasi →
Kapan eskalasi**. "Eskalasi" berarti menghubungi Superadmin (untuk non-superadmin).

### 8.1 Crawler berhenti / tidak berjalan
- Gejala: tidak ada tender baru dalam waktu lama; indikator scheduler mati.
- Pemeriksaan: cek status scheduler; cek log `[SCHEDULER]`.
- Tindakan (Superadmin): restart scheduler (`/api/status/toggle/`), atau trigger
  manual `/api/trigger/`; restart service jika perlu.
- Verifikasi: muncul `CrawlJob` baru selesai; tender baru masuk.
- Eskalasi: jika masih macet setelah restart.

### 8.2 SPSE tidak dapat diakses
- Gejala: log berisi error network/Cloudflare pada semua instansi.
- Pemeriksaan: cek koneksi ke `spse.inaproc.id`; cek status luar.
- Tindakan: tunggu; jangan memaksa crawl berulang kali (rate-limit). Crawl berikut
  yang otomatis tetap berjalan sesuai jadwal.
- Verifikasi: crawl berikutnya berhasil.
- Eskalasi: jika SPSE down >1 hari.

### 8.3 Halaman pemenang tidak ditemukan
- Gejala: log `WINNER_FETCH_UNAVAILABLE` / `WINNER_PARSE_EMPTY`.
- Pemeriksaan: apakah tender termasuk tahap awarded (retained)? Apakah URL
  `/evaluasi/{id}/pemenang` valid?
- Tindakan: tidak perlu tindakan; sistem tidak memfabrikasi pemenang. Data lama
  tetap aman (tidak dihapus).
- Verifikasi: pastikan data pemenang lama tidak hilang.
- Eskalasi: jika banyak tender awarded tapi semua gagal → Superadmin.

### 8.4 Peserta tidak muncul
- Gejala: `TenderParticipant` kosong untuk tender yang seharusnya punya peserta.
- Pemeriksaan: cek tahap tender; cek log peserta.
- Tindakan: ulangi crawl tender tersebut (recrawl) bila diizinkan.
- Verifikasi: data peserta muncul.
- Eskalasi: jika berulang.

### 8.5 NPWP kosong
- Gejala: kolom `npwp` kosong (peserta/pemenang).
- Pemeriksaan: SPSE memang tidak selalu menampilkan NPWP, atau menampilkan
  ter-mask (`00*5**...`).
- Tindakan: **jangan mengisi manual**. NPWP ter-mask otomatis `UNRESOLVED` dan
  tidak dicocokkan (aman).
- Verifikasi: pastikan tidak ada pencocokan salah karena NPWP kosong.
- Eskalasi: tidak perlu.

### 8.6 Identity conflict (konflik identitas)
- Gejala: log `ENTITY_ID_CONFLICT`; banyak perusahaan dengan NPWP sama.
- Pemeriksaan: verifikasi NPWP asli para pihak.
- Tindakan: jangan memilih otomatis; sistem tidak terhubung. Perbaiki `CompanyProfile`
  (NPWP salah) bila memang salah — hanya Superadmin dengan prosedur disetujui.
- Verifikasi: status berubah benar setelah perbaikan.
- Eskalasi: Superadmin.

### 8.7 Duplicate company (perusahaan ganda)
- Gejala: dua `CompanyProfile` identik.
- Pemeriksaan: bandingkan NIB/NPWP/nama.
- Tindakan: jangan hapus paksa. Penggabungan (merge) **tidak** dilakukan otomatis.
  Karena `participations`/`wins` memakai `company` dengan `on_delete=SET_NULL`,
  menghapus satu perusahaan **TIDAK** menghapus data peserta/pemenang — hanya
  memutus tautannya.
- Verifikasi: pastikan tidak ada kehilangan data peserta/pemenang.
- Eskalasi: Superadmin.

### 8.8 Database error
- Gejala: 500 / `OperationalError`.
- Pemeriksaan: cek log; cek ruang disk; cek koneksi DB.
- Tindakan (Superadmin): lihat `journalctl`/log; restart service; periksa backup.
- Verifikasi: API normal kembali.
- Eskalasi: jika berulang.

### 8.9 Migration error
- Gejala: `manage.py migrate` gagal; DB tidak konsisten.
- Pemeriksaan: lihat pesan error; bandingkan state `django_migrations`.
- Tindakan: **JANGAN** menekan migrasi paksa; lihat DEPLOYMENT_RUNBOOK bagian
  rollback; pulihkan dari backup bila perlu.
- Verifikasi: `manage.py migrate` selesai; `manage.py check` 0 error.
- Eskalasi: segera.

### 8.10 Scheduler error
- Gejala: log `[SCHEDULER] ... failed`.
- Pemeriksaan: lihat traceback; cek apakah konflik lock (`Crawl already in
  progress`).
- Tindakan (Superadmin): restart scheduler / service; pastikan tidak ada dua
  proses berjalan (lock memblokir eksekusi ganda).
- Verifikasi: scheduler berjalan normal.
- Eskalasi: jika berulang.

---

## 9. SOP KUALITAS DATA

Prinsip: **data sumber tidak boleh "diperbaiki" manual tanpa prosedur disetujui.**
Karena `TenderParticipant.name/npwp` dan `TenderWinner.company_name/npwp/alamat/
harga_*` adalah hasil tangkapan dari SPSE, jangan mengubahnya langsung.

| Situasi | Tindakan yang benar |
|---|---|
| NPWP kosong | Biarkan. Sistem menandai UNRESOLVED; tidak dicocokkan. Jangan isi manual. |
| NPWP berbeda untuk nama sama | Anggap perusahaan berbeda. Jangan samakan. |
| Nama berbeda | Jangan samakan tanpa bukti NPWP/NIB. |
| Duplicate NPWP antar perusahaan | Laporkan ke Superadmin; perbaiki di CompanyProfile jika memang salah. |
| Winner tidak tersedia | Biarkan `not_available`; jangan isi dari sumber lain yang tidak diverifikasi. |
| Data lama tidak lengkap | Biarkan; sistem tidak merusak data lama; backfill hanya dengan prosedur disetujui (tidak otomatis). |

---

## 10. SOP KEAMANAN (UNTUK OPERATOR)

1. **Password:** gunakan password kuat (min. 8 karakter, tidak umum). Jangan
   berbagi.
2. **Logout:** selalu logout saat meninggalkan komputer; utamanya di perangkat
   bersama.
3. **Sesi:** jangan menjaga sesi login pada browser publik.
4. **Hak akses:** hanya gunakan akun dengan hak semestinya. Jangan meminjam akun
   superadmin untuk pekerjaan rutin.
5. **Data sensitif:** NPWP adalah data sensitif. Jangan menyebarkan tangkapan
   layar berisi NPWP lengkap ke publik.
6. **Screenshot/ekspor:** sebelum membagikan tangkapan layar/ekspor, periksa
   bahwa NPWP lengkap tidak terbongkar.
7. **Aktivitas mencurigakan:** laporkan bila melihat endpoint crawl/purge dipakai
   tanpa izin.
8. **Akun diretas:** segera beri tahu Superadmin; Superadmin mereset password dan
   memeriksa log.

---

## 11. KAPAN MENGHUBUNGI SUPERADMIN (UNTUK NON-SUPERADMIN)

Hubungi Superadmin untuk: reset password, melihat/mengelola perusahaan lain,
menjalankan crawl manual, toggle scheduler, purge, mengelola KBLI, perbaikan
profil perusahaan (jika error), dan semua insiden dari bagian 8.

---

## 12. DAFTAR PERIKSA AWAL/MENUTUP SHIFT

**Awal shift:**
- [ ] Dashboard termuat.
- [ ] Scheduler berjalan (jika diizinkan melihat).
- [ ] Crawl terakhir tidak error berkepanjangan.
- [ ] Tidak ada insiden tak terselesaikan dari shift sebelumnya.

**Menutup shift:**
- [ ] Insiden dicatat.
- [ ] Semua sesi dilogout.
- [ ] Nota untuk shift berikutnya bila ada.
