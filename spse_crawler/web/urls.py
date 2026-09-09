from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/results/", views.api_results, name="api_results"),
    path("api/tenders/<int:pk>/detail/", views.api_tender_detail, name="api_tender_detail"),
    path("api/tenders/<int:pk>/resync/", views.api_tender_resync, name="api_tender_resync"),
    path("api/filter-counts/", views.api_filter_counts, name="api_filter_counts"),
    path("api/start-crawl/", views.api_start_crawl, name="api_start_crawl"),
    path("api/crawl-delta/", views.api_crawl_delta, name="api_crawl_delta"),
    path("api/status/", views.api_status, name="api_status"),
    path("api/crawl-progress/", views.api_crawl_progress, name="api_crawl_progress"),
    path("api/status/toggle/", views.api_toggle_scheduler, name="api_toggle_scheduler"),
    path("api/trigger/", views.api_trigger_crawl, name="api_trigger_crawl"),
    path("api/download/csv/", views.api_download_csv, name="api_download_csv"),
    path("api/download/excel/", views.api_download_excel, name="api_download_excel"),
    path("api/flush-records/", views.api_flush_records, name="api_flush_records"),
    path("api/kbli/", views.api_kbli_list, name="api_kbli_list"),
    path("api/kbli/create/", views.api_kbli_create, name="api_kbli_create"),
    path("api/kbli/<str:code_id>/update/", views.api_kbli_update, name="api_kbli_update"),
    path("api/kbli/<str:code_id>/delete/", views.api_kbli_delete, name="api_kbli_delete"),
    # Watchlist
    path("api/watchlist/", views.api_watchlist_list, name="api_watchlist_list"),
    path("api/watchlist/add/", views.api_watchlist_add, name="api_watchlist_add"),
    path("api/watchlist/remove/", views.api_watchlist_remove, name="api_watchlist_remove"),
    # Opportunity Score
    path("api/opportunity/<int:tender_id>/", views.api_opportunity_score, name="api_opportunity_score"),
    # Recommended Tenders
    path("api/recommended/", views.api_recommended, name="api_recommended"),
    # Tender Radar
    path("api/radar/", views.api_radar, name="api_radar"),
    # Pipeline Summary
    path("api/pipeline/", views.api_pipeline_summary, name="api_pipeline_summary"),
    # Company Completion
    path("api/company/completion/", views.api_company_completion, name="api_company_completion"),
    # Report Summary
    path("reports/", views.report_summary_page, name="report_summary_page"),
    path("api/reports/summary/", views.api_report_summary, name="api_report_summary"),
    # Intelligence Pipeline
    path("api/intelligence/status/", views.api_intelligence_status, name="api_intelligence_status"),
    path("api/intelligence/reprocess/", views.api_intelligence_reprocess, name="api_intelligence_reprocess"),
    # Competitive & Winner Intelligence (Phase 4)
    path("api/intelligence/company/<int:company_id>/", views.api_intelligence_company, name="api_intelligence_company"),
    path("api/intelligence/tender/<int:tender_id>/winner/", views.api_intelligence_tender_winner, name="api_intelligence_tender_winner"),
    path("api/intelligence/tender/<int:tender_id>/competition/", views.api_intelligence_tender_competition, name="api_intelligence_tender_competition"),
]
