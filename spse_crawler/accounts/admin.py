from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth import get_user_model

User = get_user_model()

# Unregister the built-in auth.User if it was registered by django.contrib.auth
try:
    from django.contrib.auth.models import User as BuiltinUser
    admin.site.unregister(BuiltinUser)
except (admin.sites.NotRegistered, ImportError):
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "username", "first_name", "last_name", "role", "company", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "company")
    search_fields = ("email", "username", "first_name", "last_name")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("SPSE Role", {"fields": ("role", "company")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("SPSE Role", {"fields": ("email", "role", "company")}),
    )
