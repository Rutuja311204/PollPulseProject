from django.urls import path

from . import views

app_name = "adminpanel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("users/", views.users, name="users"),
    path("users/<int:user_id>/", views.user_detail, name="user_detail"),
    path("users/<int:user_id>/status/", views.user_status, name="user_status"),
    path("users/<int:user_id>/role/", views.user_role, name="user_role"),
    path("surveys/", views.surveys, name="surveys"),
    path("surveys/<str:survey_id>/", views.survey_detail, name="survey_detail"),
    path("surveys/<str:survey_id>/moderate/", views.survey_moderate, name="survey_moderate"),
    path("audit/", views.audit_log, name="audit"),
    path("settings/", views.platform_settings, name="settings"),
]
