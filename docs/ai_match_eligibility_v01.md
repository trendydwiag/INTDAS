# Phase 1 AI Match & Eligibility Engine Specification (v0.2)

## 1. Overview & Business Objectives

Phase 1 transforms the AI Match feature from a generic semantic similarity scorer into a rigorous, two-layer **Eligibility Engine + Soft Technical Fit Engine**.

### Core Responsibilities
- **Eligibility Engine (Hard Gate)**: Answers *"Does this company legally and administratively qualify to submit a bid for this tender without immediate disqualification (gugur)?"*
- **Technical Fit Engine (Soft Gate)**: Answers *"Given that the company meets the hard requirements, how well do its technical capabilities, track record, and experience match the scope of work?"*

---

## 2. Canonical Status Normalization & Active Gate

### 2.1 The Canonical Business Rule
Secara bisnis dan operasional SPSE Inaproc, tender yang dapat disubmit penawaran kualifikasinya berada pada tahap:
```text
Pengumuman Prakualifikasi
```

### 2.2 Status Normalization Helper
Centralized module: [`spse_crawler/services/tender_status.py`](file:///Users/trendy/scrap_lpse/spse_crawler/services/tender_status.py)

```python
CANONICAL_ACTIVE_STATUS = "pengumuman prakualifikasi"

def normalize_tahap(tahap: Any) -> str:
    """Safely normalizes raw SPSE tahap string by:
    1. Handling None / non-string gracefully
    2. Lowercasing and stripping whitespace
    3. Removing SPSE DataTables presentation artifact '[...]'
    4. Collapsing consecutive whitespace
    """
    ...

def is_submittable_tender(tender_or_tahap: Any) -> bool:
    """Strict equality against CANONICAL_ACTIVE_STATUS.
    No fuzzy matching (no substring or startswith).
    """
    ...
```

### 2.3 Subsystem Gate Behavior
- **AI Match**: If `not is_submittable_tender(tender)`, manual run returns `eligibility_status="NOT_APPLICABLE"` without error. Pipeline skips cleanly.
- **Opportunity Score**: If `not is_submittable_tender(tender)`, new score calculations are skipped (`not_ready` with clear explanation) without fabricating 0 scores. Historical records remain intact.
- **Tender Radar**: Displays only active, actionable tenders where `is_submittable_tender(tender) == True`. Transitioned tenders are excluded from current view immediately while preserving historical database records.
- **Intelligence Pipeline**: Enqueue and executor guards prevent non-submittable tenders from entering processing queue.

---

## 3. Two-Layer Architecture: Hard Gate vs Soft Fit

```
                    ┌───────────────────────────────┐
                    │      Tender Qualification     │
                    │        Requirement Text       │
                    └───────────────┬───────────────┘
                                    │
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │             LAYER 1: DETERMINISTIC HARD GATE           │
       │                                                        │
       │  1. Active Tender Gate: is_submittable_tender()        │
       │  2. KBLI Gate: tender.kbli_code in company.kblis       │
       │  3. SBU Gate: requires_sbu -> company has active SBU   │
       │  4. Business License Gate: valid NIB / Izin Usaha      │
       │  5. Tax/Financial Gate: valid NPWP                     │
       └────────────────────────────┬───────────────────────────┘
                                    │
                  ┌─────────────────┴─────────────────┐
                  ▼                                   ▼
        [ANY HARD GATE FAILS]               [ALL HARD GATES PASS]
                  │                                   │
                  ▼                                   ▼
        eligibility_status:                 eligibility_status:
           NOT_ELIGIBLE                        ELIGIBLE / CONDITIONALLY
        mandatory_passed: False             mandatory_passed: True
        blockers: ["..."]                   blockers: []
        fit_score: CAPPED at <= 25          fit_score: Base 50 + Soft Fit
                                                      │
                                                      ▼
                                    ┌──────────────────────────────────┐
                                    │    LAYER 2: SOFT TECHNICAL FIT   │
                                    │                                  │
                                    │  1. TF-IDF Cosine Similarity     │
                                    │  2. Keyword Domain Overlap       │
                                    │  3. Experience & Portfolio Match │
                                    └──────────────────────────────────┘
```

---

## 4. Eligibility Status Taxonomy

1. **`ELIGIBLE`**:
   - All mandatory requirements pass with verified evidence in company qualifications.
   - `mandatory_passed = True`, `blockers = []`.
   - `fit_score` ranges between **50% and 100%** (base 50% legal compliance + soft technical score).

2. **`CONDITIONALLY_ELIGIBLE`**:
   - No mandatory requirement definitively fails, but one or more requirements need verification, renewal, or administrative document upload (`status: "needs_action"`).
   - `mandatory_passed = True`, `blockers = []`.
   - `fit_score` ranges between **35% and 75%**.

3. **`NOT_ELIGIBLE`**:
   - At least one mandatory requirement definitively fails (KBLI mismatch, SBU missing/expired, NIB/Izin missing).
   - `mandatory_passed = False`, `blockers = [...]`.
   - `fit_score` is strictly **capped at <= 25%** so that high semantic similarity can NEVER override disqualification.

4. **`NOT_READY`**:
   - Company profile lacks qualifications or NIB.
   - `mandatory_passed = False`, `fit_score = 0`.

5. **`NOT_APPLICABLE`**:
   - Tender is not in `Pengumuman Prakualifikasi` stage.

---

## 5. Defense-in-Depth: Rule-Based & LLM Independence

Neither LLM hallucinations nor TF-IDF text similarity can override legal requirements:
- `RuleBasedProvider`: Parses qualifications JSON deterministically to check exact KBLI sets, active SBU records, and valid NIBs. TF-IDF is strictly used for soft technical relevance.
- `_enforce_deterministic_hard_gates()` in `matcher.py`: Acts as a second-line defense-in-depth gate after provider execution to guarantee that even if an external LLM hallucinates compliance, the application layer deterministically enforces disqualification.

---

## 6. Cache Invalidation & Backward Compatibility

- **Cache Key**: `hashlib.sha256(f"{tender_id}:{company_id}:{qual_hash}:{MATCHER_VERSION}".encode()).hexdigest()`
- **Version Tag**: `MATCHER_VERSION = "v0.2"`. Upgrading to v0.2 automatically invalidates stale v0.1 cached results without mutating database tables.
- **Model Fields**: Additive fields (`eligibility_status`, `mandatory_passed`, `blockers`, `missing_requirements`, `recommended_actions`, `matcher_version`) have safe defaults. Existing fields (`fit_score`, `summary`, `criteria_json`, `llm_provider`, `is_fallback`, `cache_key`) remain 100% backward compatible.
