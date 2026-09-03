from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("api/audit-log/", views.api_audit_log, name="api_audit_log"),
]
