from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    """Records all status changes and important actions for traceability."""

    ACTION_CHOICES = [
        ("status_change", "Status Change"),
        ("qualification_edit", "Qualification Edit"),
        ("match_trigger", "Match Trigger"),
        ("user_login", "User Login"),
        ("user_logout", "User Logout"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_logs",
    )
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES, db_index=True)
    target_type = models.CharField(max_length=50, blank=True, default="")
    target_id = models.IntegerField(default=0)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.action}] User#{self.user_id} @ {self.created_at}"
