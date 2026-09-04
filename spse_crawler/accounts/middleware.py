from django.contrib import admin
from django.contrib.auth.middleware import AuthenticationMiddleware


class RoleMiddleware:
    """Attach user role and company to request for all views.

    Works alongside Django's AuthenticationMiddleware to provide
    role-based context throughout the request lifecycle.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            request.user_role = request.user.role
            request.user_company = request.user.company
        else:
            request.user_role = None
            request.user_company = None

        response = self.get_response(request)
        return response
