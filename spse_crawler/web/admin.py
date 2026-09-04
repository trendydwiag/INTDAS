from django.contrib import admin

from .models import CrawlJob, KbliMaster, TenderResult


@admin.register(KbliMaster)
class KbliMasterAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(TenderResult)
class TenderResultAdmin(admin.ModelAdmin):
    list_display = (
        "kode_instansi",
        "id_lelang",
        "nama_paket",
        "instansi",
        "hps",
        "tahap_saat_ini",
        "is_prakualifikasi",
        "scraped_at",
    )
    list_filter = ("is_prakualifikasi", "kode_instansi")
    search_fields = ("id_lelang", "nama_paket", "instansi")


@admin.register(CrawlJob)
class CrawlJobAdmin(admin.ModelAdmin):
    list_display = ("id", "instansi_input", "status", "total_packages", "total_details", "started_at", "finished_at")
    list_filter = ("status",)
