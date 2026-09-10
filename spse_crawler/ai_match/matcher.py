import hashlib
import json
import os
import re
from datetime import timedelta

from loguru import logger
from django.utils import timezone as dj_timezone

from .providers import get_provider, FallbackProvider
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from spse_crawler.services.tender_status import is_submittable_tender

MATCHER_VERSION: str = "v0.2"


def _compute_qualification_hash(qualifications: list[dict]) -> str:
    """Compute SHA256 hash of qualification data for cache invalidation."""
    raw = json.dumps(qualifications, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _build_cache_key(tender_id: str, company_id: int, qual_hash: str, version: str = MATCHER_VERSION) -> str:
    """Build a unique cache key for a match result including engine version."""
    raw = f"{tender_id}:{company_id}:{qual_hash}:{version}"
    return hashlib.sha256(raw.encode()).hexdigest()


def load_qualifications_as_json(company) -> tuple[list[dict], str]:
    """Load company qualifications as structured JSON list.

    Returns (qualifications_list, qualification_hash).
    """
    from spse_crawler.companies.models import CompanyQualification

    quals = CompanyQualification.objects.filter(company=company, status="active")
    items = []
    for q in quals:
        item = {
            "category": q.get_category_display(),
            "category_raw": q.category,
            "name": q.name,
            "number": q.number,
            "kbli_code": q.kbli_code,
            "kbli_codes": q.kbli_codes or [],
            "details": q.details or {},
            "klasifikasi_usaha": q.get_klasifikasi_usaha_display() if q.klasifikasi_usaha else "",
            "value_amount": q.value_amount,
            "project_name": q.project_name,
            "client_name": q.client_name,
            "project_year": q.project_year,
            "valid_from": q.valid_from.isoformat() if q.valid_from else None,
            "valid_until": q.valid_until.isoformat() if q.valid_until else None,
            "status": q.status,
        }
        items.append(item)
    return items, _compute_qualification_hash(items)


def fetch_requirement_text(tender) -> str:
    """Get requirement text for a tender.

    Priority:
      1. Use stored syarat_kualifikasi / requirement_text from DB
      2. Fallback: construct from available tender fields
    """
    # Use stored text if available
    for fld in ("syarat_kualifikasi", "requirement_text"):
        val = getattr(tender, fld, "") or ""
        if len(val.strip()) > 20:
            return val.strip()

    # Fallback: build from available tender data
    parts = [tender.nama_paket]
    if tender.kbli_code:
        parts.append(f"KBLI: {tender.kbli_code} — {tender.kbli_description}")
    if tender.jenis_pengadaan:
        parts.append(f"Jenis pengadaan: {tender.jenis_pengadaan}")
    if tender.tahap_saat_ini:
        parts.append(f"Tahap saat ini: {tender.tahap_saat_ini}")
    parts.append(f"HPS: Rp {tender.hps:,}")
    return "\n".join(parts)


def _enforce_deterministic_hard_gates(
    result: dict, tender, company, qualifications: list[dict]
) -> dict:
    """Deterministic domain guard: Hard Gate cannot be overridden by LLM or score.

    Verifies:
      1. KBLI: Tender's KBLI code must be among company's active KBLIs.
      2. SBU: If tender explicitly requires SBU, company must have active SBU.
      3. NIB / Izin: Company must have valid NIB or active izin_usaha.
    """
    company_kblis = company.kbli_codes_from_quals if hasattr(company, "kbli_codes_from_quals") else set()
    has_sbu = any(
        q.get("category_raw") == "sbu"
        or "sbu" in str(q.get("name", "")).lower()
        or "sertifikat badan usaha" in str(q.get("name", "")).lower()
        for q in qualifications
    )
    has_izin = any(
        q.get("category_raw") == "izin_usaha"
        or "izin" in str(q.get("name", "")).lower()
        or "nib" in str(q.get("name", "")).lower()
        for q in qualifications
    )
    has_nib = bool(company.nib and len(str(company.nib).strip()) >= 5)

    # If company has no qualifications and no NIB, status is NOT_READY
    if not qualifications and not has_nib:
        result["eligibility_status"] = "NOT_READY"
        result["mandatory_passed"] = False
        result["fit_score"] = 0
        result["summary"] = "Profil kualifikasi perusahaan belum diisi."
        result["blockers"] = ["Tidak ada data kualifikasi perusahaan."]
        result["criteria"] = []
        return result

    blockers = list(result.get("blockers") or [])
    criteria = list(result.get("criteria") or [])

    req_text_lower = (fetch_requirement_text(tender) or "").lower()

    # 1. KBLI Gate
    tender_kbli = (tender.kbli_code or "").strip()
    if tender_kbli:
        if tender_kbli not in company_kblis:
            msg = f"KBLI mismatch: Tender mensyaratkan KBLI {tender_kbli}, perusahaan tidak memilikinya."
            if not any("kbli mismatch" in b.lower() for b in blockers):
                blockers.append(msg)
            # Update criteria item
            found = False
            for c in criteria:
                if c.get("category") == "KBLI" or "kbli" in str(c.get("requirement", "")).lower():
                    c["status"] = "fail"
                    c["mandatory"] = True
                    c["evidence"] = f"KBLI perusahaan: {', '.join(sorted(company_kblis)) or 'tidak ada'}"
                    c["action"] = f"Tambahkan KBLI {tender_kbli} pada profil perusahaan jika bidang usaha sesuai."
                    found = True
                    break
            if not found:
                criteria.insert(0, {
                    "requirement": f"Kesesuaian KBLI ({tender_kbli})",
                    "category": "KBLI",
                    "mandatory": True,
                    "status": "fail",
                    "evidence": f"KBLI perusahaan: {', '.join(sorted(company_kblis)) or 'tidak ada'}",
                    "action": f"Tambahkan KBLI {tender_kbli} pada profil perusahaan jika bidang usaha sesuai.",
                })

    # 2. SBU Gate
    requires_sbu = bool(re.search(r'\b(sbu|sertifikat badan usaha)\b', req_text_lower))
    if requires_sbu and not has_sbu:
        msg = "SBU tidak ditemukan pada profil perusahaan."
        if not any("sbu" in b.lower() for b in blockers):
            blockers.append(msg)
        found = False
        for c in criteria:
            if c.get("category") == "SBU" or "sbu" in str(c.get("requirement", "")).lower():
                c["status"] = "fail"
                c["mandatory"] = True
                c["evidence"] = "Tidak ada dokumen SBU aktif."
                c["action"] = "Unggah dokumen SBU yang sesuai."
                found = True
                break
        if not found:
            criteria.insert(1 if criteria else 0, {
                "requirement": "Kepemilikan SBU (Sertifikat Badan Usaha) aktif",
                "category": "SBU",
                "mandatory": True,
                "status": "fail",
                "evidence": "Tidak ada dokumen SBU aktif.",
                "action": "Unggah dokumen SBU yang sesuai.",
            })

    # 3. NIB / Izin Gate
    if not has_nib and not has_izin:
        msg = "NIB / Izin Usaha tidak ditemukan pada profil perusahaan."
        if not any("izin" in b.lower() or "nib" in b.lower() for b in blockers):
            blockers.append(msg)

    # Re-evaluate eligibility status based on hard blockers
    if blockers:
        result["eligibility_status"] = "NOT_ELIGIBLE"
        result["mandatory_passed"] = False
        result["blockers"] = blockers
        # Cap fit_score when mandatory fails (max 25)
        result["fit_score"] = min(25, result.get("fit_score", 0))
        result["summary"] = f"Tidak memenuhi syarat mutlak — {len(blockers)} kendala fatal: {'; '.join(blockers[:2])}"
    elif any(c.get("mandatory") and c.get("status") == "needs_action" for c in criteria):
        result["eligibility_status"] = "CONDITIONALLY_ELIGIBLE"
        result["mandatory_passed"] = True
    else:
        result["eligibility_status"] = "ELIGIBLE"
        result["mandatory_passed"] = True

    result["criteria"] = criteria
    return result


def parse_llm_response(raw: str) -> dict:
    """Parse and validate LLM JSON response.

    Returns a validated dict conforming to the EligibilityResult contract.
    Falls back to NOT_READY with score 0 if parsing fails.
    """
    fallback = {
        "eligibility_status": "NOT_READY",
        "mandatory_passed": False,
        "fit_score": 0,
        "blockers": ["Gagal mem-parse respons AI."],
        "missing_requirements": [],
        "recommended_actions": ["Jalankan analisis ulang."],
        "summary": "Gagal mem-parse respons AI.",
        "criteria": [],
    }

    if not raw:
        return fallback

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        elif lines[0].strip().startswith("```"):
            lines = lines[1:]
        cleaned = "\n".join(lines)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return fallback
        else:
            return fallback

    # Validate and normalize
    fit_score = int(data.get("fit_score", 0))
    fit_score = max(0, min(100, fit_score))

    raw_eligibility = str(data.get("eligibility_status", "")).upper()
    if raw_eligibility in ("ELIGIBLE", "CONDITIONALLY_ELIGIBLE", "NOT_ELIGIBLE", "NOT_READY", "NOT_APPLICABLE"):
        eligibility_status = raw_eligibility
    else:
        eligibility_status = "ELIGIBLE" if fit_score >= 70 else "CONDITIONALLY_ELIGIBLE"

    mandatory_passed = bool(data.get("mandatory_passed", eligibility_status != "NOT_ELIGIBLE"))
    blockers = [str(b) for b in data.get("blockers", []) if b]
    missing_requirements = [str(m) for m in data.get("missing_requirements", []) if m]
    recommended_actions = [str(a) for a in data.get("recommended_actions", []) if a]
    summary = str(data.get("summary", ""))

    criteria = data.get("criteria", [])
    if not isinstance(criteria, list):
        criteria = []

    validated_criteria = []
    for c in criteria:
        if not isinstance(c, dict):
            continue
        req_text = str(c.get("requirement", "")).strip()
        if not req_text:
            continue
        status = c.get("status", "needs_action")
        if status not in ("pass", "needs_action", "fail"):
            status = "needs_action"
        validated_criteria.append({
            "requirement": req_text,
            "category": str(c.get("category", "LEGAL")).upper(),
            "mandatory": bool(c.get("mandatory", False)),
            "status": status,
            "evidence": str(c.get("evidence", "")),
            "action": c.get("action"),
        })

    return {
        "eligibility_status": eligibility_status,
        "mandatory_passed": mandatory_passed,
        "fit_score": fit_score,
        "blockers": blockers,
        "missing_requirements": missing_requirements,
        "recommended_actions": recommended_actions,
        "summary": summary,
        "criteria": validated_criteria,
    }


def run_match(tender_id: int, company_id: int, force: bool = False) -> dict:
    """Run AI qualification matching & eligibility check for a tender against a company.

    Returns the match result dict conforming to the EligibilityResult contract.
    Caches results by default (keyed by tender, company, qual_hash, matcher_version).
    Enforces:
      1. Active Submittable Gate: only 'pengumuman prakualifikasi' tenders can be matched.
      2. Deterministic Hard Gates: KBLI, SBU, NIB/Izin cannot be bypassed by LLM or score.
    """
    from spse_crawler.web.models import TenderResult
    from spse_crawler.companies.models import CompanyProfile
    from .models import AIMatchResult

    try:
        tender = TenderResult.objects.get(id=tender_id)
    except TenderResult.DoesNotExist:
        return {
            "fit_score": 0,
            "eligibility_status": "NOT_READY",
            "mandatory_passed": False,
            "blockers": ["Tender tidak ditemukan."],
            "missing_requirements": [],
            "recommended_actions": [],
            "summary": "Tender tidak ditemukan.",
            "criteria": [],
        }

    try:
        company = CompanyProfile.objects.get(id=company_id)
    except CompanyProfile.DoesNotExist:
        return {
            "fit_score": 0,
            "eligibility_status": "NOT_READY",
            "mandatory_passed": False,
            "blockers": ["Perusahaan tidak ditemukan."],
            "missing_requirements": [],
            "recommended_actions": [],
            "summary": "Perusahaan tidak ditemukan.",
            "criteria": [],
        }

    # -------------------------------------------------------------------------
    # HARD GATE: Active Submittable Tender Gate
    # -------------------------------------------------------------------------
    if not is_submittable_tender(tender):
        return {
            "fit_score": 0,
            "eligibility_status": "NOT_APPLICABLE",
            "mandatory_passed": False,
            "blockers": [
                f"Tender tidak aktif / submittable. Tahap saat ini: '{tender.tahap_saat_ini}'."
            ],
            "missing_requirements": [],
            "recommended_actions": [],
            "summary": (
                f"Tender bukan berstatus Pengumuman Prakualifikasi (saat ini: '{tender.tahap_saat_ini}'). "
                "Hanya tender Pengumuman Prakualifikasi yang dapat diproses."
            ),
            "criteria": [],
            "is_submittable": False,
        }

    # Load qualifications
    qualifications, qual_hash = load_qualifications_as_json(company)
    cache_key = _build_cache_key(str(tender_id), company_id, qual_hash, MATCHER_VERSION)

    # Check cache (unless forced)
    if not force:
        cached = AIMatchResult.objects.filter(
            tender_id=tender_id,
            company_id=company_id,
            cache_key=cache_key,
        ).first()
        if cached:
            return {
                "fit_score": cached.fit_score,
                "eligibility_status": getattr(cached, "eligibility_status", "ELIGIBLE"),
                "mandatory_passed": getattr(cached, "mandatory_passed", True),
                "blockers": getattr(cached, "blockers", []),
                "missing_requirements": getattr(cached, "missing_requirements", []),
                "recommended_actions": getattr(cached, "recommended_actions", []),
                "summary": cached.summary,
                "criteria": cached.criteria_json,
                "matcher_version": getattr(cached, "matcher_version", MATCHER_VERSION),
                "cached": True,
            }

    # Fetch requirement text
    requirement_text = fetch_requirement_text(tender)

    # Build user prompt
    user_prompt = USER_PROMPT_TEMPLATE.format(
        requirement_text=requirement_text,
        company_name=company.name,
        company_nib=company.nib,
        company_npwp=company.npwp,
        modal_disetor=company.modal_disetor,
        penghasilan_tahunan=company.penghasilan_tahunan,
        address=company.address,
        qualifications_json=json.dumps(qualifications, indent=2, ensure_ascii=False),
    )

    # Call Provider
    provider = get_provider()
    is_fallback = getattr(provider, "is_fallback", False)
    fallback_reason = getattr(provider, "fallback_reason", "")

    try:
        raw_response = provider.chat(SYSTEM_PROMPT, user_prompt)
        result = parse_llm_response(raw_response)
    except Exception as exc:
        logger.error("[AI] LLM call failed: {}", exc)
        is_fallback = True
        fallback_reason = f"LLM call failed: {exc}"
        try:
            from .providers import RuleBasedProvider
            rule_provider = RuleBasedProvider()
            raw_fallback = rule_provider.chat(SYSTEM_PROMPT, user_prompt)
            result = parse_llm_response(raw_fallback)
        except Exception as fb_exc:
            logger.error("[AI] RuleBased fallback also failed: {}", fb_exc)
            result = {
                "fit_score": 0,
                "eligibility_status": "NOT_READY",
                "mandatory_passed": False,
                "blockers": [f"AI analysis failed: {exc}"],
                "missing_requirements": [],
                "recommended_actions": ["Coba jalankan analisis ulang."],
                "summary": f"AI analysis failed: {exc}",
                "criteria": [],
            }

    # Enforce Deterministic Hard Gates
    result = _enforce_deterministic_hard_gates(result, tender, company, qualifications)

    result["is_fallback"] = is_fallback
    result["fallback_reason"] = fallback_reason
    result["matcher_version"] = MATCHER_VERSION

    # Cache / Persist the result
    try:
        AIMatchResult.objects.filter(tender_id=tender_id, company_id=company_id).delete()

        AIMatchResult.objects.create(
            tender_id=tender_id,
            company_id=company_id,
            fit_score=result["fit_score"],
            eligibility_status=result["eligibility_status"],
            mandatory_passed=result["mandatory_passed"],
            blockers=result["blockers"],
            missing_requirements=result["missing_requirements"],
            recommended_actions=result["recommended_actions"],
            matcher_version=MATCHER_VERSION,
            summary=result["summary"],
            criteria_json=result["criteria"],
            llm_provider=provider.name(),
            llm_model=os.environ.get("AI_MODEL", ""),
            is_fallback=is_fallback,
            fallback_reason=fallback_reason,
            cache_key=cache_key,
        )

        # Persist ai_score to TenderResult for backward compatibility
        tender.ai_score = result["fit_score"]
        tender.ai_analysis_json = {
            "fit_score": result["fit_score"],
            "eligibility_status": result["eligibility_status"],
            "mandatory_passed": result["mandatory_passed"],
            "blockers": result["blockers"],
            "missing_requirements": result["missing_requirements"],
            "recommended_actions": result["recommended_actions"],
            "summary": result["summary"],
            "criteria": result["criteria"],
            "llm_provider": provider.name(),
            "is_fallback": is_fallback,
            "fallback_reason": fallback_reason,
            "matcher_version": MATCHER_VERSION,
        }
        tender.save(update_fields=["ai_score", "ai_analysis_json"])
    except Exception as exc:
        logger.error("[AI] Failed to cache match result: {}", exc)

    return result
