from django.urls import path

from . import views

app_name = "companies"

urlpatterns = [
    path("api/company/", views.api_company_list, name="api_company_list"),
    path("api/company/create/", views.api_company_create, name="api_company_create"),
    path("api/company/<int:company_id>/update/", views.api_company_update, name="api_company_update"),
    path("api/company/<int:company_id>/delete/", views.api_company_delete, name="api_company_delete"),
    path("api/company/<int:company_id>/qualifications/", views.api_qualification_list, name="api_qualification_list"),
    path("api/company/<int:company_id>/qualifications/create/", views.api_qualification_create, name="api_qualification_create"),
    path("api/company/<int:company_id>/qualifications/<int:qual_id>/update/", views.api_qualification_update, name="api_qualification_update"),
    path("api/company/<int:company_id>/qualifications/<int:qual_id>/delete/", views.api_qualification_delete, name="api_qualification_delete"),
]
