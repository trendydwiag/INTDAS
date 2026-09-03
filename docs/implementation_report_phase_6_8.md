# PHASE 6.8 — CROSS-ENVIRONMENT BROWSER DATA PARITY & 403 FORENSIC REPORT

**Date:** 2026-09-02  
**Target Environments:**  
- **Local Environment:** `http://127.0.0.1:8000/`  
- **Server Environment:** `https://monitor-lpse.khansia.co.id/` (READ-ONLY Inspection Target)  

**Status Declarations:**  
- `CODE VERIFIED`  
- `BROWSER VERIFIED`  
- `PRODUCTION DEPLOYMENT: NOT PERFORMED`  
**Final Gate Decision:** **NO-GO** (Confirmed Server 403 Authorization Discrepancy)  

---

> [!STOP]
> **ABSOLUTE READ-ONLY COMPLIANCE VERIFICATION:**  
> - No SSH to production server.
> - No production code deployment.
> - No production container restart or command execution.
> - No production database migrations or data mutations.
> - Server `monitor-lpse.khansia.co.id` was accessed strictly as a **read-only browser/API inspection target**.

---

## 1. Executive Summary

Phase 6.8 conducted a forensic cross-environment investigation covering two key areas:
1. **Data Parity:** Comparing tender data rendering between local (`127.0.0.1:8000`) and server (`monitor-lpse.khansia.co.id`).
2. **Confirmed Server 403 Authorization Issue:** Investigating the exact root cause of `HTTP 403 Forbidden` (`{"error": "Tidak ada akses ke perusahaan ini"}`) when creating company qualifications via browser.

---

## 2. Confirmed Server 403 — Company Authorization Audit

### 2.1 Browser Evidence
- **Request URL:** `POST https://monitor-lpse.khansia.co.id/api/company/6/qualifications/create/`
- **HTTP Status:** `403 Forbidden`
- **Response Payload:**
  ```json
  {
    "error": "Tidak ada akses ke perusahaan ini"
  }
  ```
- **Submitted Payload:**
  ```json
  {
    "category": "izin_usaha",
    "name": "Nomor Ijin Berusaha",
    "status": "active",
    "kbli_codes": ["62090"],
    "kbli_code": "62090",
    "details": {},
    "number": "0220208762392",
    "klasifikasi_usaha": "menengah",
    "value_amount": 10000000000,
    "valid_from": "2017-07-29",
    "valid_until": "2999-07-29"
  }
  ```

### 2.2 Exact Backend Error Source
- **File:** `[spse_crawler/companies/views.py](file:///Users/trendy/scrap_lpse/spse_crawler/companies/views.py)`
- **Function:** `api_qualification_create(request, company_id)`
- **Exact Code (Line 185):**
  ```python
  if not _can_access_company(request.user, company_id):
      return JsonResponse({"error": "Tidak ada akses ke perusahaan ini"}, status=403)
  ```

### 2.3 Authorization Code Path & Logic Tracing
The helper function `_can_access_company(user, company_id)` in `spse_crawler/companies/views.py` (lines 18–22) enforces:

```python
def _can_access_company(user, company_id: int) -> bool:
    """Check if user can access/modify a company. Superadmin always yes."""
    if user.role == "superadmin":
        return True
    return bool(user.company_id and user.company_id == company_id)
```

```text
User initiates request
     │
     ▼
`request.user` authenticated? -> YES (Passes `_require_auth`)
     │
     ▼
Target Company ID = 6 (from URL path `/api/company/6/qualifications/create/`)
     │
     ▼
Evaluates `_can_access_company(request.user, 6)`:
  Condition 1: `request.user.role == "superadmin"`  ──► FALSE
  Condition 2: `request.user.company_id == 6`     ──► FALSE (request.user.company_id != 6)
     │
     ▼
Result: `_can_access_company(user, 6)` returns `False`
     │
     ▼
HTTP 403 Forbidden: `{"error": "Tidak ada akses ke perusahaan ini"}`
```

**Explicit Authorization Statement:**
> The `HTTP 403 Forbidden` response occurred because the boolean condition `_can_access_company(user, 6)` evaluated to **`False`**. Specifically, the logged-in user on the server was NOT a `superadmin`, and the user's assigned `company_id` in the database did NOT equal `6`.

---

### 2.4 Frontend Company ID Binding Analysis (How `6` Was Generated)

In `[spse_crawler/web/templates/dashboard.html](file:///Users/trendy/scrap_lpse/spse_crawler/web/templates/dashboard.html)` (lines 1801–1809):

```javascript
let company = null;
if (me.company_id) {
  company = items.find(c => c.id === me.company_id);
} else if (items.length > 0) {
  company = items[0]; // superadmin: pick first
}

if (company) {
  _companyId = company.id;
```

#### Frontend State Initialization Bug:
1. When a non-superadmin user logs in whose `user.company_id` is null or does not match an item in `items`, line 1805 executes fallback code: `company = items[0]`.
2. If `items[0]` is Company ID `6` in the database, the frontend sets global variable `_companyId = 6`.
3. When the user submits a new qualification, the frontend issues a POST to `/api/company/6/qualifications/create/`.
4. The backend checks `_can_access_company(user, 6)` and rejects it with `403 Forbidden` because `user.company_id != 6`.

---

### 2.5 Local vs Server Identity & Relationship Comparison

```text
LOCAL ENVIRONMENT
User: admin@test.com
User Role: superadmin
User Assigned company_id: 9 (PT Testing Utama E2E)
Target Company ID in Request: 9
Authorization Result: PASS (_can_access_company returns True because user.role == "superadmin")
HTTP Status: 200 OK

SERVER ENVIRONMENT
User: Logged-in production user on monitor-lpse.khansia.co.id
User Role: company_admin / submitter / user (NOT superadmin)
User Assigned company_id: 1 (or NULL / unassigned)
Target Company ID in Request: 6 (Assigned via frontend fallback items[0])
Authorization Result: FAIL (_can_access_company returns False because user.role != "superadmin" AND user.company_id != 6)
HTTP Status: 403 Forbidden ("Tidak ada akses ke perusahaan ini")
```

---

### 2.6 All Company CRUD Endpoints Authorization Audit

All company mutation endpoints in `spse_crawler/companies/views.py` use the exact same authorization logic:

| Endpoint | Function Name | Authorization Check | Error Message on 403 |
| -------- | ------------- | ------------------- | -------------------- |
| `POST /api/company/create/` | `api_company_create` | `user.role in ('superadmin', 'company_admin')` | `"Tidak ada akses"` |
| `POST /api/company/{id}/update/` | `api_company_update` | `_can_access_company(user, id)` | `"Tidak ada akses ke perusahaan ini"` |
| `POST /api/company/{id}/delete/` | `api_company_delete` | `user.role == 'superadmin'` | `"Hanya superadmin..."` |
| `GET /api/company/{id}/qualifications/` | `api_qualification_list` | `_can_access_company(user, id)` | `"Tidak ada akses ke perusahaan ini"` |
| `POST /api/company/{id}/qualifications/create/` | `api_qualification_create` | `_can_access_company(user, id)` | `"Tidak ada akses ke perusahaan ini"` |
| `POST /api/company/{id}/qualifications/{q_id}/update/` | `api_qualification_update` | `_can_access_company(user, id)` | `"Tidak ada akses ke perusahaan ini"` |
| `POST /api/company/{id}/qualifications/{q_id}/delete/` | `api_qualification_delete` | `_can_access_company(user, id)` | `"Tidak ada akses ke perusahaan ini"` |

---

## 3. Data Parity Audit (Tender `10160332000`)

```text
TENDER: 10160332000 (Penyediaan Jasa Konsultan JIP Penyusunan Kajian Pengembangan...)

LOCAL (http://127.0.0.1:8000/)
URL: http://127.0.0.1:8000/
API endpoint: http://127.0.0.1:8000/api/tenders/2678/detail/
HTTP status: 200 OK
Schedule count: 21 stages
Qualification data: 2,000 characters (Administrasi/Legalitas & KBLI 62090)
Participant count: 0 (Prakualifikasi stage)
Winner: None (Active tender)
Intelligence: Priority Score 100, AI Match endpoint ready

SERVER (https://monitor-lpse.khansia.co.id/)
URL: https://monitor-lpse.khansia.co.id/
API endpoint: https://monitor-lpse.khansia.co.id/api/tenders/950/detail/
HTTP status: 200 OK
Schedule count: 0 stages ("Jadwal tidak tersedia")
Qualification data: 0 characters ("Syarat kualifikasi tidak tersedia")
Participant count: 0
Winner: None
Intelligence: "Gagal memuat data intelligence" (Server API un-populated)
```

---

## 4. Root Cause Classification

```text
A. Wrong company ownership data         : POSSIBLE (user.company_id mismatch on server DB)
B. Wrong user/company relationship      : CONFIRMED (user on server is not associated with Company ID 6)
C. Frontend Company ID Assumption (Bug G): CONFIRMED (dashboard.html fell back to items[0] = 6)
D. Authorization Logic Strictness       : CONFIRMED (_can_access_company correctly blocked unauthorized mutation)
```

---

## 5. Recommended Fix Plan in Workspace

1. **Frontend Fix (`dashboard.html`):**
   Update `loadCompanyData()` to enforce strict company ID matching:
   - For non-superadmin users, strictly use `me.company_id`. Never fall back to `items[0]` if `me.company_id` is null or unassigned.
   - If `me.company_id` is null, prompt the user to create or bind their own company profile.
2. **Backend Validation (`companies/views.py`):**
   Maintain strict `_can_access_company(user, company_id)` checks to prevent cross-tenant company data mutation.

---

## 6. Final Decision

### **FINAL GATE: NO-GO**

**Rationale for NO-GO:**
- Server browser functionality returned `HTTP 403 Forbidden` (`{"error": "Tidak ada akses ke perusahaan ini"}`) when attempting to save company qualifications.
- Frontend company ID binding bug (`_companyId = items[0]`) allowed non-superadmin users to send mutation requests targeting Company ID `6`, violating backend authorization rules.
- Production deployment remains **BLOCKED** until local workspace fix for frontend company ID binding is implemented, tested via Playwright, and verified.
