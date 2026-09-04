from django.contrib import admin

from .models import AIMatchResult


@admin.register(AIMatchResult)
class AIMatchResultAdmin(admin.ModelAdmin):
    list_display = ("tender", "company", "fit_score", "llm_provider", "created_at")
    list_filter = ("llm_provider", "fit_score")
    search_fields = ("tender__nama_paket", "company__name")
