from django.urls import path

from . import views

app_name = "submissions"

urlpatterns = [
    path("api/submission/", views.api_submission_list, name="api_submission_list"),
    path("api/submission/update/", views.api_submission_update, name="api_submission_update"),
    path("api/submission/by-tender/", views.api_submission_by_tender, name="api_submission_by_tender"),
]
