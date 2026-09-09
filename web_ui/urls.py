"""URL configuration for web_ui project."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url="/static/favicon.ico", permanent=True)),
    path("admin/", admin.site.urls),
    # v0.0.2 — accounts (auth)
    path("", include("spse_crawler.accounts.urls")),
    # v0.0.2 — companies
    path("", include("spse_crawler.companies.urls")),
    # v0.0.2 — AI match
    path("", include("spse_crawler.ai_match.urls")),
    # v0.0.2 — submissions
    path("", include("spse_crawler.submissions.urls")),
    # v0.0.2 — audit
    path("", include("spse_crawler.audit.urls")),
    # v0.0.1 — web dashboard (MUST be last to avoid catching other app URLs)
    path("", include("spse_crawler.web.urls")),
]
