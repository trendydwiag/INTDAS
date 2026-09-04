from django.contrib import admin

from .models import CompanyProfile, CompanyQualification


class CompanyQualificationInline(admin.TabularInline):
    model = CompanyQualification
    extra = 0
    fields = ("category", "name", "number", "kbli_code", "status", "valid_until")


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "nib", "npwp", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "nib", "npwp")
    inlines = [CompanyQualificationInline]


@admin.register(CompanyQualification)
class CompanyQualificationAdmin(admin.ModelAdmin):
    list_display = ("company", "category", "name", "number", "status", "valid_until")
    list_filter = ("category", "status")
    search_fields = ("name", "number", "company__name")
