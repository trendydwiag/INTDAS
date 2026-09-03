from functools import wraps

from django.http import JsonResponse
from django.shortcuts import redirect


def role_required(*roles):
    """Decorator: require user to be authenticated and have one of the specified roles.

    Usage:
        @role_required("superadmin", "company_admin")
        def my_view(request): ...

    If request is AJAX (X-Requested-With header or Accept: application/json),
    returns 403 JSON. Otherwise redirects to login page.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if _is_ajax(request):
                    return JsonResponse({"error": "Authentication required"}, status=401)
                return redirect("accounts:login")

            if roles and request.user.role not in roles:
                if _is_ajax(request):
                    return JsonResponse({"error": "Insufficient permissions"}, status=403)
                return redirect("web:dashboard")

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def _is_ajax(request) -> bool:
    """Check if request is AJAX/API call."""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
        or request.path.startswith("/api/")
    )
