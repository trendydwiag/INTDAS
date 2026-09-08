import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import CompanyProfile, CompanyQualification


def _require_auth(request):
    """Return None if authenticated, else JsonResponse 401."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    return None


def _can_access_company(user, company_id: int) -> bool:
    """Check if user can access/modify a company. Superadmin always yes."""
    if user.role == "superadmin":
        return True
    return bool(user.company_id and user.company_id == company_id)


@require_GET
def api_company_list(request):
    """Return all company profiles (filtered by role)."""
    err = _require_auth(request)
    if err:
        return err
    user = request.user
    if user.role == "company_admin" and user.company_id:
        companies = CompanyProfile.objects.filter(id=user.company_id)
    elif user.role == "submitter" and user.company_id:
        companies = CompanyProfile.objects.filter(id=user.company_id)
    else:
        companies = CompanyProfile.objects.all()

    items = []
    for c in companies:
        items.append({
            "id": c.id,
            "name": c.name,
            "nib": c.nib,
            "npwp": c.npwp,
            "address": c.address,
            "phone": c.phone,
            "email": c.email,
            "contact_person": c.contact_person,
            "modal_disetor": c.modal_disetor,
            "penghasilan_tahunan": c.penghasilan_tahunan,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat() if c.created_at else "",
        })
    return JsonResponse({"items": items})


@require_POST
def api_company_create(request):
    """Create a new company profile. Only superadmin."""
    err = _require_auth(request)
    if err:
        return err
    user = request.user
    if user.role not in ("superadmin", "company_admin"):
        return JsonResponse({"error": "Tidak ada akses"}, status=403)

    body = _parse_body(request)
    name = (body.get("name") or "").strip()
    nib = (body.get("nib") or "").strip()

    if not name or not nib:
        return JsonResponse({"status": "error", "message": "name dan nib wajib diisi"}, status=400)

    if CompanyProfile.objects.filter(nib=nib).exists():
        return JsonResponse({"status": "error", "message": f"NIB {nib} sudah terdaftar"}, status=409)

    company = CompanyProfile.objects.create(
        name=name,
        nib=nib,
        npwp=(body.get("npwp") or "").strip(),
        address=(body.get("address") or "").strip(),
        phone=(body.get("phone") or "").strip(),
        email=(body.get("email") or "").strip(),
        contact_person=(body.get("contact_person") or "").strip(),
        modal_disetor=_safe_int(body.get("modal_disetor")),
        penghasilan_tahunan=_safe_int(body.get("penghasilan_tahunan")),
    )
    return JsonResponse({"status": "success", "id": company.id, "name": company.name})


@require_POST
def api_company_update(request, company_id):
    """Update an existing company profile."""
    err = _require_auth(request)
    if err:
        return err
    if not _can_access_company(request.user, company_id):
        return JsonResponse({"error": "Tidak ada akses ke perusahaan ini"}, status=403)

    try:
        company = CompanyProfile.objects.get(id=company_id)
    except CompanyProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Perusahaan tidak ditemukan"}, status=404)

    body = _parse_body(request)
    for field in ("name", "nib", "npwp", "address", "phone", "email", "contact_person"):
        if field in body:
            setattr(company, field, (body[field] or "").strip())
    for field in ("modal_disetor", "penghasilan_tahunan"):
        if field in body:
            setattr(company, field, _safe_int(body[field]))
    if "is_active" in body:
        val = body["is_active"]
        company.is_active = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes")
    company.save()
    return JsonResponse({"status": "success", "id": company.id, "name": company.name})


@require_POST
def api_company_delete(request, company_id):
    """Delete a company profile. Superadmin only."""
    err = _require_auth(request)
    if err:
        return err
    if request.user.role != "superadmin":
        return JsonResponse({"error": "Hanya superadmin yang dapat menghapus perusahaan"}, status=403)

    try:
        company = CompanyProfile.objects.get(id=company_id)
    except CompanyProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Perusahaan tidak ditemukan"}, status=404)
    company.delete()
    return JsonResponse({"status": "success", "id": company_id})


@require_GET
def api_qualification_list(request, company_id):
    """Return all qualifications for a company."""
    err = _require_auth(request)
    if err:
        return err
    if not _can_access_company(request.user, company_id):
        return JsonResponse({"error": "Tidak ada akses ke perusahaan ini"}, status=403)

    try:
        company = CompanyProfile.objects.get(id=company_id)
    except CompanyProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Perusahaan tidak ditemukan"}, status=404)

    quals = CompanyQualification.objects.filter(company=company)
    items = []
    for q in quals:
        items.append({
            "id": q.id,
            "category": q.category,
            "category_display": q.get_category_display(),
            "name": q.name,
            "number": q.number,
            "kbli_code": q.kbli_code,
            "kbli_codes": q.kbli_codes or [],
            "details": q.details or {},
            "klasifikasi_usaha": q.klasifikasi_usaha,
            "value_amount": q.value_amount,
            "project_name": q.project_name,
            "client_name": q.client_name,
            "project_year": q.project_year,
            "valid_from": q.valid_from.isoformat() if q.valid_from else None,
            "valid_until": q.valid_until.isoformat() if q.valid_until else None,
            "status": q.status,
        })
    return JsonResponse({"company": company.name, "items": items})


@require_POST
def api_qualification_create(request, company_id):
    """Create a new qualification for a company."""
    err = _require_auth(request)
    if err:
        return err
    if not _can_access_company(request.user, company_id):
        return JsonResponse({"error": "Tidak ada akses ke perusahaan ini"}, status=403)

    try:
        company = CompanyProfile.objects.get(id=company_id)
    except CompanyProfile.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Perusahaan tidak ditemukan"}, status=404)

    body = _parse_body(request)
    name = (body.get("name") or "").strip()
    category = (body.get("category") or "").strip()

    if not name or not category:
        return JsonResponse({"status": "error", "message": "name dan category wajib diisi"}, status=400)

    valid_cats = [c[0] for c in CompanyQualification.CATEGORY_CHOICES]
    if category not in valid_cats:
        return JsonResponse({"status": "error", "message": f"category tidak valid: {category}"}, status=400)

    kbli_codes_raw = body.get("kbli_codes")
    if isinstance(kbli_codes_raw, list):
        kbli_codes = [str(c).strip() for c in kbli_codes_raw if str(c).strip()]
    else:
        single = (body.get("kbli_code") or "").strip()
        kbli_codes = [single] if single else []

    details_raw = body.get("details")
    details = details_raw if isinstance(details_raw, dict) else {}

    qual = CompanyQualification.objects.create(
        company=company,
        category=category,
        name=name,
        number=(body.get("number") or "").strip(),
        kbli_code=(body.get("kbli_code") or "").strip(),
        kbli_codes=kbli_codes,
        details=details,
        klasifikasi_usaha=(body.get("klasifikasi_usaha") or "").strip(),
        value_amount=_safe_int(body.get("value_amount")),
        project_name=(body.get("project_name") or "").strip(),
        client_name=(body.get("client_name") or "").strip(),
        project_year=_safe_int(body.get("project_year")),
        status=(body.get("status") or "active").strip(),
    )
    return JsonResponse({"status": "success", "id": qual.id, "name": qual.name})


@require_POST
def api_qualification_update(request, company_id, qual_id):
    """Update a qualification."""
    err = _require_auth(request)
    if err:
        return err
    if not _can_access_company(request.user, company_id):
        return JsonResponse({"error": "Tidak ada akses ke perusahaan ini"}, status=403)

    try:
        qual = CompanyQualification.objects.get(id=qual_id, company_id=company_id)
    except CompanyQualification.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Kualifikasi tidak ditemukan"}, status=404)

    body = _parse_body(request)
    for field in ("name", "number", "kbli_code", "klasifikasi_usaha", "project_name", "client_name", "status", "category"):
        if field in body:
            setattr(qual, field, (body[field] or "").strip())
    if "kbli_codes" in body:
        raw = body["kbli_codes"]
        qual.kbli_codes = [str(c).strip() for c in raw if str(c).strip()] if isinstance(raw, list) else []
    if "details" in body:
        raw = body["details"]
        qual.details = raw if isinstance(raw, dict) else {}
    if "value_amount" in body:
        qual.value_amount = _safe_int(body["value_amount"])
    if "project_year" in body:
        qual.project_year = _safe_int(body["project_year"])
    if "valid_from" in body:
        qual.valid_from = body["valid_from"] or None
    if "valid_until" in body:
        qual.valid_until = body["valid_until"] or None
    qual.save()
    return JsonResponse({"status": "success", "id": qual.id, "name": qual.name})


@require_POST
def api_qualification_delete(request, company_id, qual_id):
    """Delete a qualification."""
    err = _require_auth(request)
    if err:
        return err
    if not _can_access_company(request.user, company_id):
        return JsonResponse({"error": "Tidak ada akses ke perusahaan ini"}, status=403)

    try:
        qual = CompanyQualification.objects.get(id=qual_id, company_id=company_id)
    except CompanyQualification.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Kualifikasi tidak ditemukan"}, status=404)
    qual.delete()
    return JsonResponse({"status": "success", "id": qual_id})


def _parse_body(request):
    try:
        return json.loads(request.body)
    except (ValueError, TypeError):
        return dict(request.POST)


def _safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
