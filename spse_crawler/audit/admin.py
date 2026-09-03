from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("user", "company", "action", "target_type", "target_id", "created_at")
    list_filter = ("action", "company")
    search_fields = ("user__email", "company__name", "old_value", "new_value")
    readonly_fields = ("user", "company", "action", "target_type", "target_id", "old_value", "new_value", "ip_address", "created_at")
