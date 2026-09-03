from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import AuditLog


@require_GET
def api_audit_log(request):
    """Return audit log entries filtered by company and date range.

    Query params:
        company_id: int (optional, superadmin only)
        action: str (optional filter by action type)
        days: int (optional, default 30)
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    qs = AuditLog.objects.select_related("user", "company")

    if user.is_superadmin:
        company_id = request.GET.get("company_id")
        if company_id:
            qs = qs.filter(company_id=company_id)
    elif user.company_id:
        qs = qs.filter(company_id=user.company_id)
    else:
        return JsonResponse({"items": []})

    action = request.GET.get("action", "").strip()
    if action:
        qs = qs.filter(action=action)

    try:
        days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        days = 30

    from datetime import timedelta
    from django.utils import timezone as dj_timezone
    cutoff = dj_timezone.now() - timedelta(days=days)
    qs = qs.filter(created_at__gte=cutoff)

    items = []
    for log in qs[:200]:
        items.append({
            "id": log.id,
            "user_email": log.user.email if log.user else "",
            "user_name": (log.user.get_full_name() or log.user.username) if log.user else "",
            "company_name": log.company.name if log.company else "",
            "action": log.action,
            "action_display": log.get_action_display(),
            "target_type": log.target_type,
            "target_id": log.target_id,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else "",
        })
    return JsonResponse({"items": items})
