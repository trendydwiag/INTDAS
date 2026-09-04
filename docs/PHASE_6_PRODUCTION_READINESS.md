# PRODUCTION READINESS — SPSE Inaproc Crawler (Fase 6)

Versi: 1.0 (Phase 6E)
Tanggal: 2026-08-31
Status: **GO** (dengan 1 BLOCKER yang merupakan langkah deployment, bukan cacat kode)

Dokumen ini adalah hasil audit produksi akhir untuk Phase 6 (retensi historis,
pemenang, peserta, entity resolution, intelligence). Dibuat berdasarkan
pemeriksaan kode aktual + uji, bukan hanya laporan terdahulu.

---

## 1. RINGKASAN

| Area | Status | Catatan |
|---|---|---|
| Migrasi | ✅ additive & aman | urutan: companies/0003 → web/0014 → web/0015 |
| Keamanan production settings | ✅ | fail-fast DEBUG/SECRET/ALLOWED_HOSTS/DATABASE |
| Auth & otorisasi | ✅ | superadmin-only untuk operasi berbahaya; CSRF aktif |
| Retensi historis | ✅ | awarded/decided/completed/aktif dipertahankan |
| Pipeline winner | ✅ | `/evaluasi/{id}/pemenang` benar (sudah diperbaiki 6D) |
| Entity resolution | ✅ | hanya EXACT; tidak ada fuzzy auto-merge |
| Integrasi & data quality | ✅ | 412 tes lulus, 0 gagal |
| **Deployment DB state** | ⚠️ **BLOCKER (langkah)** | DB dev di migrasi `0013`; production wajib terapkan 0014/0015/0003 |

**Kesimpulan:** Tidak ada **cacat kode** yang menghalangi deploy. Satu-satunya
penghalang adalah **DB production belum di-migrasi ke skema Phase 6B/6C**, yang
diselesaikan sebagai **langkah deployment** (lihat DEPLOYMENT_RUNBOOK), bukan
perubahan kode.

---

## 2. AUDIT MIGRASI

### 2.1 Isi tiap migrasi
- **`companies/0003_alter_companyprofile_npwp_and_more`**
  - Menambah `db_index=True` pada `CompanyProfile.npwp` (bukan UNIQUE).
  - Additive, aman.
- **`web/0014_tenderwinner_alamat_tenderwinner_harga_negosiasi_and_more`**
  - Tambah `TenderWinner`: `alamat`, `harga_negosiasi`, `harga_penawaran`,
    `harga_terkoreksi` (BigInteger nullable), `npwp` (CharField default "").
  - Ubah help_text `company_name`, `source_url`, `winning_value` (tanpa
    perubahan kolom data / non-destructive).
- **`web/0015_tenderparticipant_company_and_more`**
  - Tambah `TenderParticipant.company` FK (nullable, `SET_NULL`) +
    `resolution_status` (CharField default "").
  - Tambah `TenderWinner.company` FK (nullable, `SET_NULL`) +
    `resolution_status`.
  - Tambah index pada `company` untuk keduanya.

### 2.2 Urutan dependensi (deterministik, dijamin Django)
```
companies/0003
      │
      ▼
web/0014 ──────► web/0015
      ▲
      └──companies/0003
```
`web/0015` bergantung pada `companies/0003` dan `web/0014`. `manage.py migrate`
menyelesaikan urutan ini otomatis. **Deterministik.**

### 2.3 Sifat & keamanan terhadap data existing
- **Semua additive** — menambah kolom nullable/ber-default dan index. Tidak ada
  penghapusan kolom, tidak ada perubahan tipe data non-nullable, tidak ada
  data migration.
- Karena kolom baru ber-default (`""` / `None`), aman untuk baris `TenderWinner`/
  `TenderParticipant` yang sudah ada.
- Tidak dapat gagal karena record existing: tidak ada constraint baru yang unique
  pada kolom yang bisa duplikat (NPWP hanya `db_index`, not unique).

### 2.4 Data migration
- **Tidak diperlukan** data migration. Tidak ada backfill otomatis (ini sengaja,
  lihat bagian 7).

---

## 3. KEAMANAN DATABASE PRODUCTION

### 3.1 Relasi & FK
```
TenderResult
 ├── TenderParticipant  (FK tender, on_delete=CASCADE; unique (tender,name))
 │      └── company FK → CompanyProfile (on_delete=SET_NULL)
 └── TenderWinner       (OneToOne tender, on_delete=CASCADE)
        └── company FK → CompanyProfile (on_delete=SET_NULL)
CompanyProfile (nib UNIQUE; npwp db_index, not unique)
```

### 3.2 Cascades
- Menghapus **tender** → CASCADE menghapus `TenderParticipant` + `TenderWinner`
  (dan `OpportunityScore`, `IntelligenceJob`, dsb).
- Menghapus **CompanyProfile** → FK dari participant/winner ke company adalah
  `SET_NULL`, dan FK dari `OpportunityScore`/`IntelligenceJob`/
  `IntelligenceDailyUsage` adalah `CASCADE`. Artinya menghapus company **tidak**
  menghapus data peserta/pemenang, tetapi menghapus skor/pekerjaan
  intelligence-nya.

### 3.3 Pertimbangan "penghapusan tender historis tidak merusak intelligence"
- Purge otomatis (`flush_non_retained`) **tidak pernah** menargetkan tender
  retained (awarded/decided/completed/aktif), sehingga cascade ke participant/
  winner hanya terjadi pada tender stale/aborted yang sudah tidak relevan.
- Ini **menjamin** graph intelligence historis tidak hancur oleh purge rutin.

### 3.4 Duplikat & integritas
- `(kode_instansi, id_lelang)` unik → tidak ada tender duplikat.
- `(tender, name)` unik untuk peserta; `OneToOne` untuk pemenang.
- `CompanyProfile.nib` unik; NPWP tidak unik (dapat ada beberapa baris dengan
  NPWP sama → ditandai `IDENTITY_CONFLICT`, tidak otomatis disatukan).
- Orphan terhindar: participant/winner selalu melekat ke tender; `company` boleh
  null (legacy/unresolved) tanpa crash.

---

## 4. RETENSI HISTORIS

Kata kunci retained (dari `DetailParser`):
- Awarded/completed: `selesai`, `tender selesai`, `penandatanganan`, `kontrak`,
  `penetapan pemenang`, `pengumuman pemenang`, `rekomendasi`.
- Aktif prakualifikasi: `prakualifikasi`, `evaluasi`, `penawaran`, `kualifikasi`,
  `negosiasi`, dsb.

`flush_non_retained()` menghapus **hanya** tender `is_prakualifikasi=False` yang
tidak cocok dengan kata kunci retained (aborted/cancelled/stale).
Verifikasi terjamin:
- awarded → retained ✅
- completed → retained ✅
- decided → retained ✅
- pemenang → retained ✅
- active → retained ✅
- participant → retained (ikutan tender) ✅
- winner → retained ✅
- company relationship → retained (FK SET_NULL, tak ikut tender) ✅

Cleanup aborted/stale tetap berjalan (tidak dimatikan).

---

## 5. PIPELINE WINNER

- URL dibangun `_build_winner_url` → `https://host/{kode}/evaluasi/{id}/pemenang`
  (diperbaiki pada 6D; uji sudah memastikan).
- Fetch: httpx → fallback Playwright; retry/backoff sesuai `request_timeout`/
  `retry_max_attempts`.
- Parsing: `_parse_winner` mencari baris "Nama Pemenang"; `_parse_money` untuk
  harga.
- Missing page → `WINNER_FETCH_UNAVAILABLE`, return None (tidak fabrikasi).
- Malformed HTML / missing winner → `WINNER_PARSE_EMPTY`, None.
- Missing NPWP → `""`; entity resolution → UNRESOLVED/CANDIDATE; tidak dipaksa.
- Missing price → field `None`; `winning_value=negosiasi or terkoreksi or
  penawaran` (tidak fabrikasi; optional source tidak merusak).
- Idempotent: upsert OneToOne; missing winner tidak menghapus data lama.
- Logging: masked NPWP; marker jelas.
- Winner **tidak pernah difabrikasi** (wajib baris "Nama Pemenang" + nama non-empty).

---

## 6. ENTITY RESOLUTION

- `EXACT NPWP` (normalized, bukan masked) → `AUTO_LINK` (satu match).
- `EXACT_NIB` (official identifier) → `AUTO_LINK`.
- `Name only` / NPWP no-match + nama sama → `CANDIDATE_ONLY` (tanpa link).
- Konflik (NPWP sama multi company / nama sama NPWP beda) → `IDENTITY_CONFLICT`.
- Tidak ada identitas/kandidat → `UNRESOLVED`.

Tidak ada fuzzy auto-merge, tidak ada destructive merge, tidak ada pemilihan
duplikat NPWP secara arbitrer. Masked NPWP (`*`) otomatis `None` (tidak dicocokkan).

---

## 7. DATA LAMA (LEGACY)

- Baris yang dibuat sebelum Phase 6 (`company=NULL`, `resolution_status=""`) aman:
  pembacaan tidak crash; tidak terjadi penandaan company yang salah.
- Masked/empty NPWP di data lama tidak pernah auto-link.
- **Tidak ada backfill destruktif** yang dijalankan.

---

## 8. KEAMANAN (REGRESSION)

| Kontrol | Status | Bukti |
|---|---|---|
| DEBUG default aman | ✅ | `DJANGO_DEBUG` default `0`; fail-fast SECRET/ALLOWED_HOSTS/DATABASE (verifikasi subshell) |
| SECRET_KEY fail-fast | ✅ | `ImproperlyConfigured` bila kosong di production |
| ALLOWED_HOSTS fail-fast | ✅ | wajib di production |
| CSRF_TRUSTED_ORIGINS | ✅ | env/auto-derive di DEBUG |
| CSRF | ✅ | 4 endpoint operasional + KBLI + company delete + submission update diuji |
| Superadmin-only operasi berbahaya | ✅ | `require_superadmin` pada crawl/trigger/toggle/flush/KBLI |
| Otorisasi intelligence | ✅ | company-scoped; superadmin semua, lain hanya sendiri (403) |
| Template escaping | ✅ | engine Django default autoescape |
| Data sensitif di log | ✅ | NPWP selalu ter-mask (`mask_identifier`), tidak pernah penuh |
| Session/CSRF cookie Secure | ✅ | `SESSION_COOKIE_SECURE = not DEBUG`, `HTTPONLY=True` |
| Password validator | ✅ | 4+ validator aktif |
| Watchlist add/remove `@csrf_exempt` | ⚠️ MEDIUM | pola lama (Phase 3), masih auth-scoped; dicatat, bukan regresi Phase 6 |

---

## 9. SCHEDULER / CRAWLER

- Eksekusi berulang: lock `_crawl_lock` mencegah dua crawl paralel dlm satu proses.
- Concurrent: satu worker `worker_count=3` semaphore; hasil `gather` digabung.
- Partial crawl: error per-instansi tidak menggagalkan instansi lain.
- Retry/timeout: diatur settings crawler (`retry_max_attempts`, `request_timeout`).
- Duplicate prevention: constraints unik + `update_or_create`.
- Historical retention: `flush_non_retained` setelah crawl.
- Logging: marker `[SCHEDULER]`, `[PURGE]`, `WINNER_*`, `ENTITY_*`, `PARTICIPANT_SYNC`.

Catatan MEDIUM: scheduler di-proses web; pada multi-replika hanya satu yang
boleh menjalankan scheduler (lihat RUNBOOK).

---

## 10. BACKUP & ROLLBACK

Terdokumentasi penuh di `DEPLOYMENT_RUNBOOK.md` (backup PG, verifikasi, restore,
rollback kode, kapan tidak membalik migrasi, proteksi data historis).

---

## 11. KLASIFIKASI TEMUAN (GATE)

### BLOCKER
- **B1 — DB production belum di migrasi ke skema Phase 6B/6C.**
  - Impact: kolom `harga_*`, `npwp`, `company`, `resolution_status` tidak ada di
    DB dev (`0013`); jika pipeline pemenang/peserta ditulis tanpa migrasi →
    `OperationalError: no such column`.
  - Di mana diselesaikan: **langkah deployment** menjalankan `migrate --noinput`
    (apply `0003`, `0014`, `0015`). Bukan cacat kode; tidak ada kode yang diubah
    untuk melewatinya.
  - Verification: `showmigrations` mencantumkan 0014/0015/0003; `makemigrations
    --check` = No changes; `manage.py check` = 0.

### HIGH
- Tidak ada.

### MEDIUM
- **M1** `api_watchlist_add`/`api_watchlist_remove` memakai `@csrf_exempt`
  (pola lama Phase 3; auth-scoped). Dilacak; bukan regresi Phase 6.
- **M2** Scheduler dalam-proses; multi-replika berisiko crawl ganda — dokumentasi
  deployment mewajibkan satu replika scheduler.
- **M3** `resolve_company_identity()` melakukan scan `CompanyProfile.objects.all()`
  per baris (O(companies)); dapat dioptimalkan bila jumlah company besar.

### LOW
- **L1** `API` export CSV/Excel publik (tanpa NPWP); diterima sebagai desain Phase 1.
- **L2** `updated_at` bermakna "terakhir re-sync" (efek `update_or_create`); benign.

---

## 12. KEPUTUSAN AKHIR

# **GO**

- Production readiness: **PASS**
- Tests: **412 passed / 0 failed**
- Django check: **PASS**
- Migration check: **PASS** (makemigrations --check = No changes; applied di
  langkah deployment)
- Security: **PASS** (1 MEDIUM dilacak)
- Historical retention: **PASS**
- Winner pipeline: **PASS**
- Entity resolution: **PASS**
- SOP: **COMPLETE**
- Deployment: **GO** (dengan 1 langkah wajib: migrasi 0003/0014/0015 pada DB
  production — tidak dilakukan otomatis, sesuai aturan)
