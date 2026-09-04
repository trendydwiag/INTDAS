# PHASE 6B — WINNER SOURCE VERIFICATION & WINNER INTELLIGENCE FOUNDATION — IMPLEMENTATION REPORT

Date: 2026-08-31
Status: **READY FOR REVIEW**

## Decision Gate

**WINNER SOURCE = VERIFIED (CASE A).**

The awarded-winner source was traced end-to-end via live inspection of actual
SPSE behavior (not inferred, not assumed from naming convention, not fabricated):

- The real crawler detail URL is
  `https://spse.inaproc.id/{kode}/lelang/{id}/pengumumanlelang`.
- The actual detail HTML navigation exposes a real link to
  `/{kode}/evaluasi/{id_lelang}/pemenang` ("Pemenang") — discovered browser/network
  evidence, present in captured real HTML.
- That `/pemenang` page was fetched live with the existing Playwright crawler and
  contains a real winner table:
  `Nama Pemenang | Alamat | NPWP | Harga Penawaran | Harga Terkoreksi | Harga Negosiasi`.
- Structure reproduced across 3 independent samples (`jabarprov/10158980000`,
  `jabarprov/10158160000`, `atrbpn/10140240000`).

Because the source is verified, Phase 6B implemented a **minimal winner vertical
slice**: a winner parser, a persistence service, and crawler integration to
capture only verified fields for retained awarded tenders.

## Executive Summary

`READY FOR REVIEW`

Phase 6B (the successor to the accepted Phase 6 audit) verified — using actual
live SPSE evidence — that the awarded winner is real and reproducible on the
page `/{kode}/evaluasi/{id_lelang}/pemenang`. No `/pemenang`, `/api/winner`,
`/winner`, or `/detail/winner` endpoint existed in code before this phase; the
winner was **discovered** from the real detail HTML navigation, then captured.

A minimal vertical slice was then delivered:

1. **Winner parser** (`DetailParser._parse_winner` + `_parse_money`) reads the
   verified `/pemenang` table into identity + three distinct price fields.
2. **Persistence** (`sync_tender_winner`, new service) upserts the winner into
   `TenderWinner` idempotently, never fabricating and never destroying history.
3. **Crawler integration** fetches the winner page **only for retained
   awarded/completed tenders** in both crawl paths (views.py + scheduler.py).
4. **Schema** extended `TenderWinner` with `npwp`, `alamat`,
   `harga_penawaran`, `harga_terkoreksi`, `harga_negosiasi` (one migration).

No CompanyProfile/entity linking (deferred to Phase 6C by design). No AI/fuzzy
matching, no dashboard, no deploy, no DB reset.

---

## Source Reconnaissance

### Methodology (hard rules honored)

- NO invented endpoint structure — the winner page was found by inspecting the
  actual detail HTML nav links and following them live.
- NO invented selectors — every selector is derived from the real captured page.
- NO synthetic HTML treated as truth — fixtures are built from the real captured
  structure and cross-validated across multiple live samples.
- NO participant-as-winner inference and NO winner-from-ordering inference — the
  winner table is an explicit, separate source.
- Value semantics are preserved distinctly and never collapsed unknowingly.

### Tender samples inspected (real, from DB + live)

| Kode | ID Lelang | Nama Paket | Tahap |
|---|---|---|---|
| jabarprov | 10158980000 | Pemeliharaan Berkala Jalan Cimerak - Cibuntu | Pengumuman Pemenang |
| jabarprov | 10158160000 | Revitalisasi Kios Nanjungsari | Pengumuman Pemenang |
| atrbpn | 10140240000 | Penyusunan Materi Teknis dan Ranperkada RDTR | (awarded) |
| gunungmaskab | 10156794000 | (awarded) | Pengumuman Pemenang (fetch timed out — transient, not structural) |

### Where the winner lives

The detail page (`/pengumumanlelang`) contains **no** winner identity/value; it
only lists navigation links. Following the real "Pemenang" link yields:

```
/{kode}/evaluasi/{id_lelang}/pemenang
```

Page **meta table** (Pagu, HPS) plus a **nested winner table**:

```
Nama Pemenang | Alamat | NPWP | Harga Penawaran | Harga Terkoreksi | Harga Negosiasi
```

Live captured example (`jabarprov/10158980000`):

| Field | Value |
|---|---|
| Nama Pemenang | CV. Athalla Putra Kusma |
| Alamat | Lingkungan Jelat RT 02 RW 04 ... - Banjar (Kota) - Jawa Barat |
| NPWP | 00*5**7****42**0 (masked by SPSE) |
| Harga Penawaran | Rp. 855.800.811,31 |
| Harga Terkoreksi | Rp. 855.800.811,31 |
| Harga Negosiasi | Rp. 855.301.311,32 |

### Source Verification Matrix

| Evidence | Found? | Actual URL | Contains Winner? | Identity? | Value? | Confidence |
|---|---|---|---|---|---|---|
| Detail HTML (`/pengumumanlelang`) | Yes | crawler detail URL | Nav link only | No | No | HIGH |
| Peserta page (`/lelang/{id}/peserta`) | Yes | existing endpoint | No winner | No | No | HIGH |
| **Pemenang page (`/evaluasi/{id}/pemenang`)** | **Yes (via detail nav)** | live-fetched | **Yes** | **Yes (Nama, Alamat, NPWP)** | **Yes (Penawaran/Terkoreksi/Negosiasi)** | **HIGH** |
| Pemenang Berkontrak (`/evaluasi/{id}/pemenangberkontrak`) | Link present | in nav | (not implemented — beyond minimal slice) | — | — | MEDIUM |
| AJAX/API | No | — | — | — | — | N/A |
| Embedded JSON | No | — | — | — | — | N/A |

**Verdict:** winner identity (Nama Pemenang / Alamat / NPWP) and value (three
distinct prices) are VERIFIED and reproducible from the actual `/pemenang` page.
`Harga Negosiasi` is the final negotiated price → the strongest "winning value".

---

## Implementation

### 1. Winner parser — `spse_crawler/parsers/detail.py`

Added `_parse_winner(html)`, `_parse_money(raw)`, and `_fetch_winner(url, ...)`.

- `_parse_winner` locates the `<tr>` whose **direct child** `<th>` is
  `Nama Pemenang` (using `selectolax` `.iter()` over direct children so nested
  tables never leak into the match), then reads the following `<tr>`'s direct
  `<td>` cells: `Nama | Alamat | NPWP | Penawaran | Terkoreksi | Negosiasi`.
- `_parse_money` parses `Rp. 855.800.811,31` into integer rupiah
  (856800811-equivalent truncation), matching the discovery HPS parser semantics:
  `.` = thousands separator, `,` = decimal sen fraction, truncated to integer.
- `_fetch_winner` builds `/{kode}/evaluasi/{id}/pemenang` and reuses the same
  httpx→Playwright fallback as the peserta/jadwal pages.

No participant/ordering inference; record is only returned when a winner
**company name** is present in the source.

### 2. Parsed object — `spse_crawler/models/tender.py`

`TenderDetail` gains:
```python
winner: dict | None = Field(default=None, ...)      # {company_name, npwp, alamat,
                                                     #  harga_penawaran, harga_terkoreksi,
                                                     #  harga_negosiasi, winning_value}
winner_source_url: str = Field(default="", ...)      # /evaluasi/{id}/pemenang
```

### 3. Persistence — `spse_crawler/services/tender_winner_store.py` (new)

```python
def sync_tender_winner(tender, winner, source_url="", source_fetched_at=None) -> bool:
    if not winner: return False
    company = (winner.get("company_name") or "").strip()
    if not company: return False
    TenderWinner.objects.update_or_create(
        tender=tender,
        defaults={...npwp, alamat, harga_penawaran, harga_terkoreksi,
                  harga_negosiasi, winning_value, source_url, source_fetched_at},
    )
    return True
```

- Idempotent: same `tender` updates in place (OneToOne), no duplicate rows.
- Never fabricates: empty/missing winner or empty company name → no-op (`False`).
- Never destroys history: temporararily-unavailable winner page on a re-crawl
  leaves the existing winner row intact.
- External identity only — NO CompanyProfile linking (deferred to Phase 6C).

### 4. Crawler integration — `scrape_detail` + both crawl paths

In `DetailParser.scrape_detail`, after the peserta fetch, **only** when
`is_retained_tahap(tahap)` is true (awarded/completed), the winner page is
fetched (non-fatal on failure) and assigned to `detail.winner` +
`detail.winner_source_url`. Non-awarded tenders never fetch the winner page.

Winner persistence is wired after the participant sync in both crawl entry
points when `detail.winner` is present:
- `spse_crawler/web/views.py` (`_execute_crawl`) — `_sync_winner`
- `spse_crawler/web/scheduler.py` (`_do_crawl`) — `_sync_winner`

### 5. Schema — `spse_crawler/web/models.py`

`TenderWinner` extended with verified fields (identity is external, documented
as such; no auto-link):

```python
npwp = CharField(max_length=50, default="", blank=True)          # may be masked by SPSE
alamat = TextField(default="", blank=True)
harga_penawaran = BigIntegerField(null=True, blank=True)         # Harga Penawaran (IDR)
harga_terkoreksi = BigIntegerField(null=True, blank=True)        # Harga Terkoreksi (IDR)
harga_negosiasi = BigIntegerField(null=True, blank=True)         # Harga Negosiasi (IDR)
winning_value = BigIntegerField(null=True, blank=True)           # = Harga Negosiasi (final)
```

Value semantics are explicitly distinct and documented: `winning_value` is the
final negotiated price (`harga_negosiasi`), not a silent collapse.

---

## Value Semantics (distinct, not collapsed)

| SPSE field | meaning | stored in |
|---|---|---|
| Harga Penawaran | offer price before negotiation | `harga_penawaran` |
| Harga Terkoreksi | corrected/administered price | `harga_terkoreksi` |
| Harga Negosiasi | final negotiated price (agreed value) | `harga_negosiasi` + `winning_value` |

`winning_value` is set to `harga_negosiasi` (falling back to `harga_terkoreksi`
then `harga_penawaran`) and is documented as such — the semantics are known and
preserved, never collapsed without understanding.

---

## Tests

**Result:** `359 passed` (previous baseline 338 + 21 new).

New test file `spse_crawler/services/test_tender_winner.py` (21 tests):
- **Parser:** full winner extraction (identity + all three prices); missing
  identity → `None`; missing values keep identity with NULL prices; missing
  table / malformed / empty → `None`; first-data-row only; money parsing.
- **Pydantic:** `winner`/`winner_source_url` defaults and assignment.
- **Model:** verified fields persist; OneToOne uniqueness; nullable prices;
  cascade delete.
- **Persistence:** create; idempotent re-sync (1 row); update-in-place; no-data
  no-op; empty company no-op; unavailable data does not destroy existing;
  no auto-link to CompanyProfile.

Command:
```
DJANGO_DEBUG=1 DJANGO_SECRET_KEY=testsecret123 python -m pytest --tb=short -q spse_crawler
→ 359 passed in 12.77s
```

## Migration

`Required: YES` (single, additive only — new nullable fields on `TenderWinner`).

```
spse_crawler/web/migrations/0014_tenderwinner_alamat_tenderwinner_harga_negosiasi_and_more.py
```

All new fields are nullable / have defaults, so the migration is purely additive
and safe. The migration is generated in the workspace and **NOT applied to any
production database**. Verification:

```
python manage.py makemigrations --check --dry-run   → No changes detected
python manage.py check                              → System check identified no issues (0 silenced)
```

---

## Provenance

Every `TenderWinner` row records `source_url` (the actual `/evaluasi/{id}/pemenang`
URL), `source_type` (`pemenang_page`), and `source_fetched_at`. Identity is
external (Nama/Alamat/NPWP) and deliberately NOT linked to any tenant
`CompanyProfile` — entity resolution is a later phase (Phase 6C).

---

## Remaining Phase 6 Blockers (unchanged, out of scope for 6B)

- **Entity resolution / CompanyProfile linking:** deferred to Phase 6C — winner
  identity is captured but not matched to a company entity.
- **NPWP-based handling / `pemenangberkontrak` (contract) page:** `Harga
  Negosiasi` is captured as the winner "value"; the separate
  `/pemenangberkontrak` page (contract value) is not implemented in this minimal
  slice.
- **Company intelligence consumers** (`get_company_intelligence()` /
  `get_tender_winner()`) still return honest `null`/`not_available` until
  populated data from live crawls lands — out of scope for 6B.

## Files Changed

- `spse_crawler/parsers/detail.py` — `_parse_winner`, `_parse_money`, `_fetch_winner`, `scrape_detail` winner fetch
- `spse_crawler/models/tender.py` — `TenderDetail.winner` / `winner_source_url`
- `spse_crawler/web/models.py` — `TenderWinner` verified fields
- `spse_crawler/web/migrations/0014_*.py` — added winner fields (NOT applied to prod)
- `spse_crawler/services/tender_winner_store.py` — new `sync_tender_winner` service
- `spse_crawler/web/views.py` — `_sync_winner` in `_execute_crawl`
- `spse_crawler/web/scheduler.py` — `_sync_winner` in `_do_crawl`
- `spse_crawler/services/test_tender_winner.py` — new tests (21)
- `docs/implementation_report_phase_6b.md` — this report

## Git / Deployment

No deployment, no production migration, no destructive data cleanup, no DB reset.
Code + tests + report changes only.

## HARD STOP

Implementation + tests + report complete. **STOP — awaiting user review.**
Do not start Phase 6C.
