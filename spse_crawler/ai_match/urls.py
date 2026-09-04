from django.urls import path

from . import views

app_name = "ai_match"

urlpatterns = [
    path("api/match/run/", views.api_match_run, name="api_match_run"),
    path("api/match/results/", views.api_match_results, name="api_match_results"),
    path("api/match/results/by-tender/", views.api_match_results_by_tender, name="api_match_results_by_tender"),
]
