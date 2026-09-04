import json

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.views.decorators.http import require_GET, require_POST

User = get_user_model()


def login_view(request):
    """Render login page or handle login POST."""
    next_url = request.POST.get("next") or request.GET.get("next") or "web:dashboard"
    if request.user.is_authenticated:
        return redirect(next_url)

    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            return redirect(next_url)
        error = "Email atau password salah."

    return render(request, "login.html", {"error": error, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    """Log out the current user."""
    logout(request)
    return redirect("web:dashboard")


@require_GET
def api_auth_me(request):
    """Return current user info as JSON. Returns anonymous state if not logged in."""
    if not request.user.is_authenticated:
        return JsonResponse({
            "authenticated": False,
            "role": None,
            "email": None,
            "full_name": None,
            "company": None,
        })

    user = request.user
    return JsonResponse({
        "authenticated": True,
        "role": user.role,
        "email": user.email,
        "full_name": user.get_full_name() or user.username,
        "company": user.company_name,
        "company_id": user.company_id,
        "is_superadmin": user.is_superadmin,
        "is_company_admin": user.is_company_admin,
        "is_submitter": user.is_submitter,
    })
