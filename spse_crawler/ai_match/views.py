import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import AIMatchResult
from .matcher import run_match


@require_POST
@csrf_exempt
def api_match_run(request):
    """Run AI match for a tender against the user's company.

    POST params:
        tender_id: int (required)
        force: bool (optional, default false — bypass cache)

    Returns 503 if AI provider is unavailable (e.g. 401/429 network error).
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        body = dict(request.POST)

    tender_id = body.get("tender_id")
    force = body.get("force", False)
    if isinstance(force, str):
        force = force.lower() in ("1", "true", "yes")

    if not tender_id:
        return JsonResponse({"error": "tender_id wajib diisi"}, status=400)

    # Determine company_id — superadmin can specify, others use their own
    company_id = body.get("company_id")
    if user.is_superadmin and company_id:
        pass  # Use specified company_id
    elif user.company_id:
        company_id = user.company_id
    else:
        return JsonResponse({"error": "Tidak ada perusahaan terkait dengan akun Anda"}, status=400)

    try:
        result = run_match(
            tender_id=int(tender_id),
            company_id=int(company_id),
            force=force,
        )
        # Check if the result indicates a provider error
        if result.get("fit_score", -1) == -1:
            return JsonResponse({
                "error": result.get("summary", "AI provider tidak tersedia"),
                "provider_error": True,
            }, status=503)
        return JsonResponse({"status": "success", "result": result})
    except ConnectionError as exc:
        return JsonResponse({
            "error": "AI provider tidak dapat dihubungi. Silakan coba lagi nanti.",
            "provider_error": True,
        }, status=503)
    except TimeoutError as exc:
        return JsonResponse({
            "error": "AI provider timeout. Silakan coba lagi nanti.",
            "provider_error": True,
        }, status=503)
    except Exception as exc:
        exc_str = str(exc).lower()
        if any(kw in exc_str for kw in ["401", "429", "unauthorized", "rate limit", "quota", "limit exceeded"]):
            return JsonResponse({
                "error": "AI provider tidak tersedia (kuota habis). Gunakan mode offline.",
                "provider_error": True,
            }, status=503)
        return JsonResponse({"error": str(exc)}, status=500)


@require_GET
def api_match_results(request):
    """Return AI match results filtered by company."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    qs = AIMatchResult.objects.select_related("tender", "company")

    if user.is_superadmin:
        company_id = request.GET.get("company_id")
        if company_id:
            qs = qs.filter(company_id=company_id)
    elif user.company_id:
        qs = qs.filter(company_id=user.company_id)
    else:
        return JsonResponse({"items": []})

    tender_id = request.GET.get("tender_id")
    if tender_id:
        qs = qs.filter(tender_id=tender_id)

    items = []
    for m in qs[:100]:
        items.append({
            "id": m.id,
            "tender_id": m.tender_id,
            "tender_name": m.tender.nama_paket[:80] if m.tender else "",
            "company_id": m.company_id,
            "company_name": m.company.name if m.company else "",
            "fit_score": m.fit_score,
            "summary": m.summary,
            "criteria": m.criteria_json,
            "llm_provider": m.llm_provider,
            "llm_model": m.llm_model,
            "created_at": m.created_at.isoformat() if m.created_at else "",
        })
    return JsonResponse({"items": items})


@require_GET
def api_match_results_by_tender(request):
    """Return AI match results for all companies on a specific tender (superadmin only)."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    tender_id = request.GET.get("tender_id")
    if not tender_id:
        return JsonResponse({"error": "tender_id wajib diisi"}, status=400)

    qs = AIMatchResult.objects.filter(tender_id=tender_id).select_related("company")
    items = []
    for m in qs:
        items.append({
            "id": m.id,
            "company_id": m.company_id,
            "company_name": m.company.name if m.company else "",
            "fit_score": m.fit_score,
            "summary": m.summary,
            "criteria": m.criteria_json,
            "llm_provider": m.llm_provider,
            "created_at": m.created_at.isoformat() if m.created_at else "",
        })
    return JsonResponse({"items": items})
