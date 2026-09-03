# PHASE 6C — COMPANY IDENTITY, ENTITY RESOLUTION & COMPANY INTELLIGENCE — IMPLEMENTATION REPORT

Date: 2026-08-31
Status: **READY FOR REVIEW**

## Decision Gate

**IDENTITY RESOLUTION = CONSERVATIVE EXACT-ONLY.**

Phase 6C turns persisted `TenderParticipant` (Phase 5) and `TenderWinner`
(Phase 6B) rows into a **company-level identity graph** — `Tender → Participant
→ CompanyProfile` and `Tender → Winner → CompanyProfile` — and adds read-only
historical company intelligence. The defining constraint, honored everywhere:

> `"PT ABC" == CompanyProfile("PT ABC")` by name alone is FORBIDDEN. Name
> similarity is never used to auto-link or merge identity.

Automatic linking happens ONLY on an **exact, normalized identity key** (NPWP,
or another verified official identifier such as NIB). Everything weaker is either
a named **candidate** (recorded, never linked) or **unresolved**.

## Executive Summary

`READY FOR REVIEW`

Phase 6C delivered:

1. **`services/entity_resolution.py`** (new) — deterministic NPWP normalization
   (distinct from fiscal validation), an explicit match-status vocabulary
   (`EXACT_NPWP`, `EXACT_OFFICIAL_IDENTIFIER`, `CANDIDATE_ONLY`,
   `IDENTITY_CONFLICT`, `UNRESOLVED`), a `ResolutionResult` dataclass, and a
   `resolve_company_identity(name, npwp, nib)` resolver that never returns a bare
   `CompanyProfile`.
2. **Schema** — `TenderParticipant.company` + `resolution_status`;
   `TenderWinner.company` + `resolution_status`; `CompanyProfile.npwp` got a
   `db_index` (index; **no** uniqueness constraint — see Migration section).
3. **Stores** — `sync_tender_participants` and `sync_tender_winner` now resolve
   each row conservatively and set `company`/`resolution_status` **only on an
   exact auto-link**, never clobbering existing links, never replacing the
   source-derived name.
4. **Intelligence API** — `get_company_intelligence()` and `get_tender_winner()`
   upgraded (non-breaking) to compute real metrics from the linked rows with
   honest coverage metadata.
5. **Tests** — new `services/test_entity_resolution.py` (27 tests) plus updates
   to 4 legacy tests that encoded the old "no FK to CompanyProfile" behavior.
   Full suite: **386 passed**.

No fuzzy matching, no automatic/historical merge, no destructive company
changes, no deletion of duplicate CompanyProfile rows, no DB reset, no
production deployment, no Phase 7/AI work.

---

## 1. NPWP Normalization (distinct from validation)

New `normalize_npwp(raw)` in `services/entity_resolution.py`:

- **Deterministic digit extraction** — returns the digits of the value
  (e.g. `"01.234.567.8-901.000"` → `"012345678901000"`).
- **`None` for unusable values**:
  - empty / `None` / whitespace
  - alphabetic content (an NPWP is numeric; letters are noise)
  - **masked values** containing `*`, `x`, `X`, `#` — this is how SPSE masks
    NPWP (`00*5**7****42**0` from the real `/peserta` pages). Masked values are
    **never matched**.
- **Explicit non-claim**: this is normalization ONLY. It does **not** validate
  that the NPWP is a real Indonesian tax identifier (no fiscal checksum /
  algorithm). The code and docs make this distinction explicit.

Empty/null/malformed values can never match a CompanyProfile (callers rely on
the resolver, which treats a `None` normalized NPWP as "no usable identity").

## 2. Entity Resolution

`resolve_company_identity(name=None, npwp=None, nib=None) -> ResolutionResult`.

Resolution priority:

| # | Key | Outcome |
|---|-----|---------|
| 1 | exact NIB (standard-form `._normalize_identifier`) to ONE company | `EXACT_OFFICIAL_IDENTIFIER` — **auto-link** |
| 1b | NIB matches multiple companies | `IDENTITY_CONFLICT` — never link |
| 2 | exact normalized NPWP to ONE company | `EXACT_NPWP` — **auto-link** |
| 2b | NPWP matches multiple companies (duplicate NPWP) | `IDENTITY_CONFLICT` — never link |
| 3 | NPWP present but unmatched / name-only | `CANDIDATE_ONLY` (if a name matches exactly) — **never link** |
| 3 | name provided, no match | `UNRESOLVED` |
| 4 | empty name AND empty/unusable NPWP/NIB | `UNRESOLVED` |

Only `EXACT_NPWP` and `EXACT_OFFICIAL_IDENTIFIER` are in `AUTO_LINK_STATUSES`.
`CANDIDATE_ONLY` deliberately carries `company=None` — the matched candidates are
exposed for humans/AI to consider later, but never auto-selected.

### Conservative application (`apply_resolution_to`)

`apply_resolution_to(obj, result)` sets `company` + `resolution_status` ONLY for
auto-linkable exact matches. For weaker outcomes it records `resolution_status`
for provenance ONLY when the row is **not already linked** — so a re-crawl can
never downgrade/clobber an existing exact link, and never invents identity.

### Identity-conflict / duplicate handling

- Two CompanyProfiles sharing the same non-empty NPWP → `IDENTITY_CONFLICT`;
  the resolver **never arbitrarily picks one**. (Handled at the application
  layer — see the Migration section for why no DB-level uniqueness is added.)
- Same company name but a *different* NPWP → a same-name candidate that is
  **never merged** (never auto-linked).
- Winner identity is sourced from the verified Phase 6B `/pemenang` page. It is
  **never inferred from participant ordering**.

## 3. Schema & Migration

### `web` app (`web/models.py`)
- `TenderParticipant`: added `company`
  (`ForeignKey("companies.CompanyProfile", on_delete=SET_NULL, null=True,
  related_name="participations")`) and `resolution_status` (CharField(40), blank,
  default `""`). Added an index on `company`.
- `TenderWinner`: added `company`
  (`ForeignKey(..., related_name="wins")`) and `resolution_status`; added an
  index on `company`.
- Both docstrings updated to reflect conservative Phase 6C linking.

### `companies` app (`companies/models.py`)
- `CompanyProfile.npwp`: added `db_index=True` (indexing for the normalization
  scan / future lookups). **No uniqueness constraint was added.**

### Migration decision (documented reason)

Migrations **were** required (new additive fields/indices):
- `companies/0003_alter_companyprofile_npwp_and_more.py` — `npwp` `db_index`.
- `web/0015_tenderparticipant_company_and_more.py` — `company` + `resolution_status`
  on both models + indices.

**Why NO partial-unique constraint on `CompanyProfile.npwp`:** an earlier draft
added one, then was reverted. Phase 6C must *represent* and *flag* the
"duplicate NPWP → integrity conflict" case (a required behavior), which a
hard DB-unique constraint would make impossible to even store. The conflict is
therefore enforced and surfaced at the resolution layer
(`IDENTITY_CONFLICT`, never arbitrary pick) rather than rejected by the schema.
This preserves the audit finding that a partial-unique index would be safe on
today's data but is intentionally **not** the chosen mechanism.

Migrations are **additive** and are **NOT applied to production** (consistent
with prior phases).

## 4. Store Integration (participant + winner)

`services/tender_participant_store.py::sync_tender_participants` and
`services/tender_winner_store.py::sync_tender_winner` now, after the existing
idempotent upsert, call `resolve_company_identity(...)` with the row's
source name + NPWP and `apply_resolution_to(obj, result)`. Behavior preserved:
- Upsert only; history never wiped on re-crawl.
- Original source-derived `name`/`npwp` are **never replaced** by the
  CompanyProfile name.
- Exact matches link; name-only/conflict do not.

Both call sites (`web/views.py:_execute_crawl`,
`web/scheduler.py:_do_crawl`) already invoke these stores, so linking is
automatically active for all newly synced participant/winner rows without any
crawler changes.

## 5. Intelligence API (non-breaking)

`services/competitive_intelligence.py`:

### `get_company_intelligence(company_id)`
Now computes from the linked rows:
- `statistics.total_participation` — count of `company.participations`.
- `statistics.total_wins` — count of `company.wins`.
- `statistics.total_winning_value` — sum of captured winning values.
- `statistics.win_rate` — `wins / participations`, with `win_rate_basis` noting
  it reflects only the crawled/retained subset.
- `recent_tenders` / `recent_wins` — real rows (tender id, name, instansi, HPS,
  etc.).
- `institution_frequency` / `location_frequency` / `kbli_frequency` — from the
  observed participation-tender set.
- `coverage` — `winner_coverage` (= win_rate) + `participation_coverage` +
  a `winner_coverage_basis` that **honestly documents the limitation** ("fraction
  of captured participations that resulted in a captured win; coverage is limited
  to tenders where winner data was actually persisted").

When no rows exist, `total_participation`/`total_wins`/`win_rate` are `None` and
`data_available=False` (the previous `NOT_AVAILABLE` contract is preserved — no
fabricated zeros). The API response contract key shape is unchanged.

### `get_tender_winner(tender_id)`
Now returns `status: "ok"` with the winner dict (company_name, npwp, alamat,
winning_value, harga_* prices, `resolution_status`, `resolved_company_id`,
source_url) when a `TenderWinner` row exists; keeps `not_available` (winner
None) when none is persisted, and `not_found` for a missing tender. Contract
compatible.

## 6. Data Safety (honored)

Automatic fuzzy merge — **NO**; destructive company merge — **NO**; deleting
duplicate CompanyProfile rows — **NO**; changing historical participant/winner
source values — **NO**; inventing identity — **NO**; DB reset — **NO**;
production deployment — **NO**.

## 7. Tests

New `spse_crawler/services/test_entity_resolution.py` (27 tests):
- **NPWP**: digit extraction, empty/null, masked (unusable), alphabetics,
  different-values-differ.
- **Entity resolution**: exact NPWP → auto match; exact NIB → auto match;
  name-only → CANDIDATE_ONLY (never linked); name-only no company → UNRESOLVED;
  no identity → UNRESOLVED; duplicate CompanyProfile NPWP → IDENTITY_CONFLICT
  (never arbitrary pick); same name + different NPWP → not linked; masked NPWP
  never matches; `apply_resolution_to` exact-link and no-clobber-by-weaker.
- **Participant linking**: exact links, name-only doesn't link, no match
  unresolved, source name preserved.
- **Winner linking**: exact links, name-only doesn't link, source name preserved.
- **Intelligence**: no-data → not_available; participation/wins/win-rate/history;
  institution/KBLI frequency + coverage limitation; upgraded `get_tender_winner`
  real-data and `not_available` paths.

Legacy tests updated (encoded the old "no FK to CompanyProfile" behavior):
- `test_tender_participants.py::test_no_auto_link_to_company`
- `test_tender_winner.py::test_no_auto_link_to_company`
- `test_competitive_intelligence.py::test_company_intelligence_queries` (3)
- `test_competitive_intelligence.py::test_winner_queries` (2)

## 8. Validation

- `pytest` — **386 passed** (baseline 359 → 386; +27 new, 4 legacy updated).
- `python manage.py check` — **0 errors**.
- `python manage.py makemigrations --check` — **0 pending**.

## 9. Files Changed

- `spse_crawler/services/entity_resolution.py` — **NEW**: resolution + NPWP.
- `spse_crawler/services/tender_participant_store.py` — resolve + link.
- `spse_crawler/services/tender_winner_store.py` — resolve + link.
- `spse_crawler/services/competitive_intelligence.py` — intelligence upgrade.
- `spse_crawler/web/models.py` — `TenderParticipant`/`TenderWinner` fields.
- `spse_crawler/companies/models.py` — `npwp` index + docstring context.
- `spse_crawler/web/migrations/0015_*.py`, `companies/migrations/0003_*.py` —
  additive migrations (not applied to prod).
- `spse_crawler/services/test_entity_resolution.py` — **NEW** tests.
- `spse_crawler/services/test_tender_participants.py`,
  `test_tender_winner.py`, `test_competitive_intelligence.py` — legacy updates.

## 10. Remaining Risks / Limitations

- **Coverage is honest, not complete**: win-rate and winner coverage reflect only
  the crawled/retained participation+winner subset, never the full universe. The
  API documents this in `data_limitation`/`coverage.winner_coverage_basis`.
- **Company corpus is small** (current DB: ~2 CompanyProfiles): most
  participant/winner rows will be `UNRESOLVED` until tenant CompanyProfiles are
  created. The `CANDIDATE_ONLY` status is the intended bridge: candidates are
  surfaced for a human/AI to confirm, never auto-linked.
- **`TenderWinner` population depends on prior Phase 6B winner capture**; where
  absent, winner metrics are `None`/`not_available` by design.
- **Duplicate-NPWP integrity is enforced at the resolver layer**, not the DB (see
  Migration section) — the resolver always reports `IDENTITY_CONFLICT` and never
  picks arbitrarily.

## 11. HARD STOP

Phase 6C is complete and `READY FOR REVIEW`. Work **stops here** — Phase 7 (AI /
win-probability / predictive analytics) and any deployment are explicitly out of
scope and NOT started.
