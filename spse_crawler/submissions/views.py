import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import TenderSubmissionStatus
from spse_crawler.web.models import TenderResult
from spse_crawler.companies.models import CompanyProfile


@require_GET
def api_submission_list(request):
    """Return submission statuses for the user's company."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    qs = TenderSubmissionStatus.objects.select_related("tender", "company", "updated_by")

    if user.is_superadmin:
        company_id = request.GET.get("company_id")
        if company_id:
            qs = qs.filter(company_id=company_id)
    elif user.company_id:
        qs = qs.filter(company_id=user.company_id)
    else:
        return JsonResponse({"items": []})

    # Optional tender_id filter
    tender_id = request.GET.get("tender_id")
    if tender_id:
        qs = qs.filter(tender_id=tender_id)

    items = []
    for s in qs[:200]:
        items.append({
            "id": s.id,
            "tender_id": s.tender_id,
            "tender_name": s.tender.nama_paket[:80] if s.tender else "",
            "tender_hps": s.tender.hps if s.tender else 0,
            "company_id": s.company_id,
            "company_name": s.company.name if s.company else "",
            "status": s.status,
            "status_display": s.get_status_display(),
            "notes": s.notes,
            "updated_by": s.updated_by.get_full_name() or s.updated_by.username if s.updated_by else "",
            "updated_at": s.updated_at.isoformat() if s.updated_at else "",
        })
    return JsonResponse({"items": items})


@require_POST
def api_submission_update(request):
    """Update submission status for a tender.

    POST params:
        tender_id: int (required)
        status: str (required) — belum_diproses | cocok_diproses | sudah_submit | tidak_cocok
        notes: str (optional)
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        body = dict(request.POST)

    tender_id = body.get("tender_id")
    status = (body.get("status") or "").strip()
    notes = (body.get("notes") or "").strip()

    if not tender_id or not status:
        return JsonResponse({"error": "tender_id dan status wajib diisi"}, status=400)

    valid_statuses = [c[0] for c in TenderSubmissionStatus.STATUS_CHOICES]
    if status not in valid_statuses:
        return JsonResponse({"error": f"Status tidak valid: {status}"}, status=400)

    # Determine company
    company_id = body.get("company_id")
    if user.is_superadmin and company_id:
        pass  # superadmin can specify
    elif user.company_id:
        company_id = user.company_id
    else:
        return JsonResponse({"error": "Tidak ada perusahaan terkait dengan akun Anda"}, status=400)

    # Verify tender exists
    try:
        tender = TenderResult.objects.get(id=tender_id)
    except TenderResult.DoesNotExist:
        return JsonResponse({"error": "Tender tidak ditemukan"}, status=404)

    # Verify company exists
    try:
        company = CompanyProfile.objects.get(id=company_id)
    except CompanyProfile.DoesNotExist:
        return JsonResponse({"error": "Perusahaan tidak ditemukan"}, status=404)

    # Only company_admin, submitter, or superadmin can update
    if not user.is_superadmin and user.role not in ("company_admin", "submitter"):
        return JsonResponse({"error": "Anda tidak memiliki akses untuk mengubah status"}, status=403)

    # Create or update
    sub, created = TenderSubmissionStatus.objects.update_or_create(
        tender=tender,
        company=company,
        defaults={
            "status": status,
            "notes": notes,
            "updated_by": user,
        },
    )

    return JsonResponse({
        "status": "success",
        "created": created,
        "submission": {
            "id": sub.id,
            "tender_id": sub.tender_id,
            "company_id": sub.company_id,
            "status": sub.status,
            "status_display": sub.get_status_display(),
            "notes": sub.notes,
        },
    })


@require_GET
def api_submission_by_tender(request):
    """Return submission statuses for all companies on a specific tender (superadmin)."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    tender_id = request.GET.get("tender_id")
    if not tender_id:
        return JsonResponse({"error": "tender_id wajib diisi"}, status=400)

    qs = TenderSubmissionStatus.objects.filter(tender_id=tender_id).select_related("company")
    items = []
    for s in qs:
        items.append({
            "id": s.id,
            "company_id": s.company_id,
            "company_name": s.company.name if s.company else "",
            "status": s.status,
            "status_display": s.get_status_display(),
            "notes": s.notes,
            "updated_at": s.updated_at.isoformat() if s.updated_at else "",
        })
    return JsonResponse({"items": items})
