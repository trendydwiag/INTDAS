# Phase 5 — Implementation Report: Competitive Intelligence Data Foundation

**Report date:** 2026-08-31
**Audit basis:** Actual source code, parser implementation, migration state, and full test-suite execution in `/Users/trendy/scrap_lpse`. No SPSE field was assumed; every claimed source field is backed by the existing parser structure (the `/peserta` page rows `[No, Nama, NPWP, ...]` acknowledged in `detail.py:489`) or is reported as NOT-demonstrably-parseable. No winner/participant value was fabricated.

---

## 1. Status

```
READY FOR REVIEW
```

**Alasan:** Fase foundation data diimplementasikan, direct dari audit aktual: 2 model baru (`TenderParticipant`, `TenderWinner`), enrichment parser `/peserta` (nama + NPWP), perubahan gate crawler untuk mempertahankan tender yang sudah ditetapkan pemenang, store persisten idempoten yang diwire ke 3 jalur penyimpanan, dan update minimal lapisan Phase 4. Full suite **325/325 PASS** (baseline 296 + 29 tes baru). Migration `0013` dibuat dan di-apply ke dev SQLite bersama `0012`. Ini **FOR REVIEW**, bukan "Approved".

---

## 2. Keputusan User (disepakati sebelum implementasi)

1. **Winner — ditunda.** Bangun model `TenderWinner` + kontrak persisten idempoten, tetapi TIDAK build parser winner (HTML halaman winner tidak demonstrably parseable dari repo → melanggar rule 11 bila di-asumsikan). Dilaporkan sebagai `NOT DEMONSTRABLY PARSEABLE`.
2. **Participant — ekstrak NAMA + NPWP.** Perkuat parser `/peserta` yang sudah ada untuk menangkap `Nama` (kolom 1) dan `NPWP` (kolom 2) ke model `TenderParticipant` yang dinormalisasi. Identity eksternal saja; TIDAK auto-link ke `CompanyProfile`.
3. **Gate — pertahankan tender yang sudah ditetapkan.** Ubah gate Stage-2 agar tender pada tahap `penetapan pemenang` / `kontrak` / `selesai` / `penandatanganan` / `rekomendasi` DIPERTAHANKAN (tidak di-drop), agar data participant + winner masa depan bisa tertangkap; tender aborted (`pembatalan`/`batal`/`gagal`) tetap di-skip.

---

## 3. STEP 1 — Hasil Audit (mandatory, sebelum desain)

```
SOURCE DATA AVAILABLE
- TenderResult fields sertagai baseline (nama_paket, instansi, hps, tahap, dll).
- /peserta page columns (per existing parser comment "(No, Nama, NPWP, ...)"): count
  sudah ditangkap; kolom NAME dan NPWP posisinya diakui parser tapi belum diekstrak.

SOURCE DATA NOT AVAILABLE (demonstrably, dari repo ini)
- Winner / pemenang identity & winning value: TIDAK ada parser, TIDAK ada sampel HTML,
  TIDAK ada penanganan URL /pemenang di repo. Struktur halaman winner tidak bisa
  dibuktikan dari isi repo.
- nilai_kontrak pernah di-parse di discovery (TenderPackage.nilai_kontrak) tapi DI-DROP
  di save path — tidak pernah dipersisten.

CURRENT MODEL LIMITATIONS
- Tidak ada TenderParticipant / TenderWinner.
- Tidak ada relasi TenderResult <-> CompanyProfile untuk partisipasi/kemenangan.
- Tender yang sudah selesai/ditetapkan di-eksklusi gate Stage-2.

REQUIRED NEW DATA
- participant name + stable identity (npwp) per tender.
- winner identity + winning value per tender (foundation only — captured later).
- provenance (source_url, source_type, source_fetched_at).
- kunci unique idempotensi.

PROPOSED MODEL
- TenderParticipant: tender FK, name, npwp, provenance; unique (tender, name).
- TenderWinner: OneToOne(tender); data foundation only (tidak di-populate).
- No auto-attach ke CompanyProfile.

CRAWLER CHANGES REQUIRED
- Perkuat ekstraksi /peserta yang ADA untuk menangkap nama+NPWP (source yang terbukti).
- Winner capture: ditunda (HTML tidak demonstrably parseable).

BACKFILL POSSIBILITY
- Nama participant: TIDAK bisa backfill dari DB lama (hanya count yang disimpan).
- Tidak ada winner/participant detail historis untuk di-backfill.

RISKS
- Parser winner tanpa HTML terverifikasi = resiko fabrikasi aturan parse (rule 11).
- Auto-link participant ke CompanyProfile by name = entity resolution yang tidak aman.
- Duplikasi jika kunci unique salah → dihindari dengan unique (tender, name) + upsert.
```

---

## 4. Scope (berdasarkan implementasi aktual)

Phase 5 mencakup:
- Model `TenderParticipant` + `TenderWinner` (foundation) di `web/models.py:61` & `:101`.
- Enrichment parser `/peserta`: `_fetch_peserta_data` (satu fetch → count + participants) dan `_parse_participants` (nama+NPWP) — `detail.py:498` & `:531`.
- Gate crawler: `_TAHAP_AWARDED_KEYWORDS` + `is_retained_tahap` (`detail.py:278` & `:322`) — tender ditetapkan dipertahankan, aborted tetap di-skip.
- Store persisten idempoten `services/tender_participant_store.py:38` → diwire ke 3 jalur penyimpanan (`web/scheduler.py`, `web/views.py`, `cli.py` recap).
- Update minimal lapisan Phase 4: `get_tender_competition` (`competitive_intelligence.py:119`) kini mengembalikan nama participant nyata; URL/API contract tidak berubah.
- UI dashboard: `loadCompetitionIntelligence()` menampilkan daftar nama participant.
- Migration `0013` (dev/test), 29 tes baru, docs.

Phase 5 **tidak** mencakup / tidak dilakukan: winner parser (ditunda, lihat §2 & §8), auto-link ke `CompanyProfile`, backfill historis, deployment, `.env`/Caddy/HTTPS/production, perubahan auth/authz/CSRF/Radar/Opportunity/AI-Match.

---

## 5. Files Changed

| File | Perubahan |
| --- | --- |
| `spse_crawler/web/models.py` | Add `TenderParticipant` (unique `(tender,name)`), `TenderWinner` (`OneToOne(tender)`) |
| `spse_crawler/web/migrations/0013_tenderwinner_tenderparticipant_and_more.py` | NEW — CreateModel x2 + UniqueConstraint (di-apply ke dev bersama `0012`) |
| `spse_crawler/models/tender.py` | `TenderDetail.participants: list[dict]` field (`:182`) |
| `spse_crawler/parsers/detail.py` | `_TAHAP_AWARDED_KEYWORDS`, `is_retained_tahap`, gate update, `_fetch_peserta_data`, `_parse_participants` |
| `spse_crawler/services/tender_participant_store.py` | NEW — `sync_tender_participants()` idempoten, upsert-only |
| `spse_crawler/services/competitive_intelligence.py` | `get_tender_competition` mengembalikan nama participant (minimal, contract sama) |
| `spse_crawler/web/scheduler.py` | Wire participant sync setelah save |
| `spse_crawler/web/views.py` | Wire participant sync di `_execute_crawl`; update docstring competition view |
| `spse_crawler/cli.py` | Wire participant sync di `_recrawl_details_async` |
| `spse_crawler/web/templates/dashboard.html` | Render daftar nama participant di panel competition |
| `spse_crawler/services/test_tender_participants.py` | NEW — 26 tes |
| `spse_crawler/services/test_competitive_intelligence.py` | Update query-count + 3 tes enrichment |
| `docs/PROJECT_CONTEXT.md` | Tambahkan baris Phase 5, model, store, enrichment (additive) |

---

## 6. Model Baru

### `TenderParticipant` (`web/models.py:61`)
- `tender` FK → `TenderResult` (CASCADE), `related_name="participants"`.
- `name` (CharField 300, db_index), `npwp` (CharField 50, blank).
- Provenance: `source_url`, `source_type` (default `"peserta_page"`), `source_fetched_at`.
- `created_at`/`updated_at`.
- **Unique:** `(tender, name)` → `uniq_tender_participant_name` (kunci idempotensi).
- Index pada `tender` dan `npwp`.
- **Identity eksternal** — tidak ada FK ke `CompanyProfile`; tanpa fuzzy matching.

### `TenderWinner` (`web/models.py:101`)
- `tender` OneToOne → `TenderResult` (CASCADE), `related_name="winner"`.
- `company_name` (blank), `winning_value` (BigInteger nullable), provenance (`source_type` default `"pemenang_page"`).
- **Data foundation only — TIDAK di-populate** (tidak ada parser; HTML winner tidak demonstrably parseable). Tidak pernah diffabrikasi.

---

## 7. Crawler Enrichment & Gate

### Gate Stage-2 (`detail.py`)
- `_TAHAP_REJECT_KEYWORDS` dipecah menjadi `_TAHAP_ABORTED_KEYWORDS` (pembatalan/batal/gagal → tetap di-skip) dan `_TAHAP_AWARDED_KEYWORDS` (selesai/tender selesai/penandatanganan/kontrak/penetapan pemenang/rekomendasi → dipertahankan).
- `is_eligible_tahap` tetap (semantik prakualifikasi tidak berubah).
- `is_retained_tahap(tahap)` baru: True untuk awarded/completed, False untuk aborted & unknown.
- `scrape_detail` skip hanya jika `not eligible AND not retained` (`detail.py:701`). Tender awarded kini dipertahankan (radar/status tetap mengecualikan terminal via queryset-nya sendiri → tidak berdampak).

### Peserta page (`detail.py`)
- `_fetch_peserta_data(url, referer, kode)` → fetch `/peserta` **sekali**, return `(count, participants)` — tidak ada fetch ganda.
- `_parse_participants(html)` membaca baris `[No, Nama, NPWP, ...]`, hanya menyimpan nama non-empty (junk-avoidance). Nilai dianggap eksternal.

---

## 8. Winner — Status Jujur (NOT DEMONSTRABLY PARSEABLE)

Per keputusan user (item 1) dan rule 11, Phase 5 **tidak membuat parser winner** karena:
- Tidak ada sampel HTML halaman winner (`/pemenang` atau sejenis) di repo.
- Tidak ada penanganan URL / parser winner yang sudah ada sebagai dasar yang terbukti.
- Membuat aturan parse berdasarkan asumsi = berisiko fabrikasi.

`TenderWinner` ada sebagai **foundation** (skema + kontrak persisten idempoten siap), sengaja **tidak di-populate**. Tidak ada `TenderWinner` row yang dibuat oleh crawl. Ini dilaporkan sebagai `NOT DEMONSTRABLY PARSEABLE`, bukan `NOT AVAILABLE` global — data menang pada SPSE nyata bisa tertangkap di fase depan setelah struktur HTML diverifikasi.

---

## 9. Idempotensi & Backward Compatibility

- **Idempoten:** `sync_tender_participants()` upsert pada `(tender, name)`. Crawl pertama → N record; crawl kedua → tetap N (tanpa duplikat); enrichment update `npwp`/provenance; jika data tidak tersedia di re-crawl → tidak menghapus record lama (upsert-only).
- **Backward compatible:** `TenderResult` & semua model endpoints/views yang ada TIDAK diubah (tidak ada field dihapus). `get_tender_competition` mengubah isi `participants` menjadi nyata saat ada, tapi `participants=[]` saat tak ada data → tes lama tetap lulus. Parser count (`_parse_peserta_count`) tetap dipertahankan.
- **No destructive historical change:** tidak ada data dihapus; hanya menambah tabel baru.

---

## 10. Security

- Tidak ada endpoint baru, tidak ada perubahan auth/authz/CSRF. Endpoint Phase 4 (`company`/`winner`/`competition`) tetap get-only, company-isolated, superadmin-only, tanpa `@csrf_exempt`. 
- Uji security/regression yang sudah ada tetap lulus di full suite (325/325); tidak ada perubahan perilaku otentikasi.

---

## 11. Tests — 325/325 PASS

- Baseline sebelum Phase 5: **296/296** (via `pytest`, `DJANGO_DEBUG=1` + secret key).
- Tabel lengkap setelah Phase 5:

| Area | Jumlah |
| --- | --- |
| `test_tender_participants.py` (Phase 5 baru: model, parser, gate, pydantic, idempotensi) | 26 |
| `test_competitive_intelligence.py` (3 enrichment baru + update query-count) | 34 |
| Full suite | **325 PASS** |

`python manage.py makemigrations --check` → **No changes detected**.
`python manage.py check` → **System check identified no issues**.

Migration `0013` di-apply ke dev SQLite bersama `0012` (keduanya sebelumnya belum apply; sekarang `[X]`). Kedua model dibuat via `CreateModel`/`AddConstraint` (native reversible); test DB di-build dari migration setiap run → reversibility terverifikasi pada pendekatan praktis.

---

## 12. Findings / Warnings

- **MEDIUM — Volume data:** Dengan gate baru, crawler kini mempertahankan tender yang sudah ditetapkan (bukan di-drop). Ini meningkatkan jumlah row `TenderResult` di crawl masa depan. `purger.flush_inactive()` / `flush_non_prakualifikasi()` masih bisa menghapusnya jika dijalankan manual (tidak otomatis) — perilaku purge tidak diubah.
- **LOW — peserta_count vs participants:** `get_tender_competition` tetap bergantung pada `peserta_count` untuk status `not_available`; nama participant hanya diisi bila ada row. Ini menjaga contract lama.
- **INFO — Winner:** Data winner akan tersedia setelah parser winner dibangun di fase depan (HTML diverifikasi). Kerangka menyusul; tidak ada winner yang diffabrikasi.

---

## 13. Documentation Consistency Check

- `docs/PROJECT_CONTEXT.md` ditambah baris Phase 5 (status READY), model listing, isolation table, dan section 7.4. Tidak ada perubahan status approval/roadmap (Winner/Competitor tetap PLANNED di PRD/ADR).
- `docs/prd.md`, `docs/adr.md` TIDAK diubah (tidak ada perubahan arsitektur yang mengubah kontrak fitur masa depan; menjaga "no approval/status change").
- Tidak ada update dokumen hanya untuk mengklaim selesai (Phase 5 report ini adalah deliverable resmi yang berbasis audit aktual).

---

## 14. Phase 5 Acceptance Criteria

| Kriteria | Status |
| --- | --- |
| Model participant/winner dibuat + unique/relation/nullable/cascade | ✅ (325 tests incl. model tests) |
| Parser ekstraksi participant (nama+NPWP) + gate awarded retain | ✅ (parser + gate tests) |
| Persistensi idempoten (N → N, enrichment update, unavailable tidak hancurkan) | ✅ (persistence tests) |
| Security re-verified (full regression) | ✅ (325/325) |
| `makemigrations --check` & `manage.py check` bersih | ✅ |
| Migration di-apply dev/test | ✅ (`0013` + `0012`) |
| Tidak fabricate winner/participant | ✅ (winner foundation only, empty) |

---

## 15. Final Verdict

```
READY FOR REVIEW
```

Phase 5 menambahkan fondasi data normalized untuk Competitive Intelligence masa depan: participant name+NPWP nyata dari `/peserta` (dengan provenance + idempotensi), model `TenderWinner` foundation (belum diisi, bukan diffabrikasi), gate crawler yang mempertahankan tender ditetapkan, dan enrich minimal endpoint competition Phase 4. Semua berbasis audit aktual, tanpa field SPSE yang di-invent, tanpa backfill historis, tanpa perubahan deployment/auth. Full suite hijau 325/325. **Stopping here for review — no deploy, no further implementation without explicit direction.**

---

## IMPORTANT RULE — STOP

Phase 5 selesai sebagai implementasi data foundation. **STOP** di sini menunggu review pengguna. Tidak ada deploy; tidak ada perubahan `.env`/Caddy/HTTPS/production; tidak ada fitur baru yang diimplementasikan tanpa arahan eksplisit.
