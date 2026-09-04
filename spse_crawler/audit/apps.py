import json

from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spse_crawler.audit"
    verbose_name = "Audit Log"

    def ready(self):
        import spse_crawler.audit.signals  # noqa: F401
