from django.apps import AppConfig


class WebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spse_crawler.web"
    verbose_name = "SPSE Crawler"

    def ready(self):
        import spse_crawler.web.signals  # noqa: F401
