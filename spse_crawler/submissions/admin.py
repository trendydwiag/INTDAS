from django.contrib import admin

from .models import TenderSubmissionStatus


@admin.register(TenderSubmissionStatus)
class TenderSubmissionStatusAdmin(admin.ModelAdmin):
    list_display = ("tender", "company", "status", "updated_by", "updated_at")
    list_filter = ("status", "company")
    search_fields = ("tender__nama_paket", "company__name")
