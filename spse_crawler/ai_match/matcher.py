import hashlib
import json
import os
from datetime import timedelta

from loguru import logger
from django.utils import timezone as dj_timezone

from .providers import get_provider, FallbackProvider
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


def _compute_qualification_hash(qualifications: list[dict]) -> str:
    """Compute SHA256 hash of qualification data for cache invalidation."""
    raw = json.dumps(qualifications, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _build_cache_key(tender_id: str, company_id: int, qual_hash: str) -> str:
    """Build a unique cache key for a match result."""
    raw = f"{tender_id}:{company_id}:{qual_hash}"
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
        }
        items.append(item)
    return items, _compute_qualification_hash(items)


def fetch_requirement_text(tender) -> str:
    """Get requirement text for a tender.

    Priority:
      1. Use stored requirement_text from DB (populated during crawl)
      2. Fallback: construct from available tender fields
    """
    # Use stored text if available
    if tender.requirement_text and len(tender.requirement_text.strip()) > 20:
        return tender.requirement_text

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


async def _fetch_page_text(url: str, settings) -> str:
    """Fetch page via Playwright stealth and extract qualification text."""
    from bs4 import BeautifulSoup
    from spse_crawler.core.browser import PlaywrightEngine

    engine = PlaywrightEngine(settings)
    try:
        await engine.start()
        context = await engine.new_context()
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        html = await page.content()
        await context.close()

        soup = BeautifulSoup(html, "html.parser")
        sections = []
        for h in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
            text = h.get_text(strip=True).lower()
            if any(kw in text for kw in ["persyaratan", "kualifikasi", "syarat"]):
                parent = h.parent
                if parent:
                    sections.append(parent.get_text(separator="\n", strip=True))

        if sections:
            return "\n\n".join(sections[:3])

        body = soup.find("body")
        if body:
            return body.get_text(separator="\n", strip=True)[:5000]
        return ""
    finally:
        await engine.stop()


def parse_llm_response(raw: str) -> dict:
    """Parse and validate LLM JSON response.

    Returns a validated dict with fit_score, summary, and criteria.
    Falls back to score 0 if parsing fails.
    """
    fallback = {
        "fit_score": 0,
        "summary": "Gagal mem-parse respons AI.",
        "criteria": [],
    }

    if not raw:
        return fallback

    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (``` markers)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        elif lines[0].strip().startswith("```"):
            lines = lines[1:]
        cleaned = "\n".join(lines)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON in the response
        import re
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

    summary = str(data.get("summary", ""))
    criteria = data.get("criteria", [])

    if not isinstance(criteria, list):
        criteria = []

    validated_criteria = []
    for c in criteria:
        if not isinstance(c, dict):
            continue
        validated_criteria.append({
            "requirement": str(c.get("requirement", "")),
            "status": c.get("status", "needs_action") if c.get("status") in ("pass", "needs_action", "fail") else "needs_action",
            "evidence": str(c.get("evidence", "")),
            "action": c.get("action"),
        })

    return {
        "fit_score": fit_score,
        "summary": summary,
        "criteria": validated_criteria,
    }


def run_match(tender_id: int, company_id: int, force: bool = False) -> dict:
    """Run AI qualification matching for a tender against a company.

    Returns the match result dict. Caches results by default.
    If LLM is unavailable, returns score 0 without raising errors.
    """
    from spse_crawler.web.models import TenderResult
    from spse_crawler.companies.models import CompanyProfile
    from .models import AIMatchResult

    try:
        tender = TenderResult.objects.get(id=tender_id)
    except TenderResult.DoesNotExist:
        return {"fit_score": 0, "summary": "Tender tidak ditemukan.", "criteria": []}

    try:
        company = CompanyProfile.objects.get(id=company_id)
    except CompanyProfile.DoesNotExist:
        return {"fit_score": 0, "summary": "Perusahaan tidak ditemukan.", "criteria": []}

    # Load qualifications
    qualifications, qual_hash = load_qualifications_as_json(company)
    cache_key = _build_cache_key(str(tender_id), company_id, qual_hash)

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
                "summary": cached.summary,
                "criteria": cached.criteria_json,
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

    # Call LLM
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
        result = {
            "fit_score": 0,
            "summary": f"AI analysis failed: {exc}",
            "criteria": [],
        }

    result["is_fallback"] = is_fallback
    result["fallback_reason"] = fallback_reason

    # Cache the result
    try:
        # Delete old entries for this tender+company
        AIMatchResult.objects.filter(tender_id=tender_id, company_id=company_id).delete()

        AIMatchResult.objects.create(
            tender_id=tender_id,
            company_id=company_id,
            fit_score=result["fit_score"],
            summary=result["summary"],
            criteria_json=result["criteria"],
            llm_provider=provider.name(),
            llm_model=os.environ.get("AI_MODEL", ""),
            is_fallback=is_fallback,
            fallback_reason=fallback_reason,
            cache_key=cache_key,
        )

        # Persist ai_score to TenderResult for table display
        tender.ai_score = result["fit_score"]
        tender.ai_analysis_json = {
            "fit_score": result["fit_score"],
            "summary": result["summary"],
            "criteria": result["criteria"],
            "llm_provider": provider.name(),
            "is_fallback": is_fallback,
            "fallback_reason": fallback_reason,
        }
        tender.save(update_fields=["ai_score", "ai_analysis_json"])
    except Exception as exc:
        logger.error("[AI] Failed to cache match result: {}", exc)

    return result
