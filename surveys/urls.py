"""Creator and respondent page routes."""
from django.urls import path

from .views import insights_views, manage, public

app_name = "surveys"

urlpatterns = [
    path("dashboard/", manage.dashboard, name="dashboard"),
    path("surveys/", manage.survey_list, name="list"),
    path("surveys/new/", manage.survey_create, name="create"),
    path("surveys/<str:survey_id>/", manage.survey_overview, name="overview"),
    path("surveys/<str:survey_id>/build/", manage.survey_builder, name="builder"),
    path("surveys/<str:survey_id>/preview/", public.preview, name="preview"),
    path("surveys/<str:survey_id>/settings/", manage.survey_settings, name="settings"),
    path("surveys/<str:survey_id>/collaborators/add/", manage.collaborator_add, name="collaborator_add"),
    path("surveys/<str:survey_id>/collaborators/<int:user_id>/remove/", manage.collaborator_remove, name="collaborator_remove"),
    path("surveys/<str:survey_id>/publish/", manage.survey_publish, name="publish"),
    path("surveys/<str:survey_id>/close/", manage.survey_close, name="close"),
    path("surveys/<str:survey_id>/archive/", manage.survey_archive, name="archive"),
    path("surveys/<str:survey_id>/restore/", manage.survey_restore, name="restore"),
    path("surveys/<str:survey_id>/duplicate/", manage.survey_duplicate, name="duplicate"),
    path("surveys/<str:survey_id>/delete/", manage.survey_delete, name="delete"),
    path("surveys/<str:survey_id>/analytics/", insights_views.analytics_page, name="analytics"),
    path("surveys/<str:survey_id>/responses/", insights_views.responses_page, name="responses"),
    path("surveys/<str:survey_id>/reports/", insights_views.reports_page, name="reports"),
    path("surveys/<str:survey_id>/reports/print/", insights_views.print_report, name="print_report"),
    path("surveys/<str:survey_id>/export/responses.csv", insights_views.export_responses_view, name="export_responses"),
    path("surveys/<str:survey_id>/export/analytics.csv", insights_views.export_analytics_view, name="export_analytics"),
    path("reports/", insights_views.reports_index, name="reports_index"),
    path("compare/", insights_views.compare_page, name="compare"),
    # Public (respondents) — identified by an unguessable token, not the database id
    path("s/<str:token>/", public.respond, name="respond"),
    path("s/<str:token>/thanks/", public.thanks, name="thanks"),
    path("s/<str:token>/results/", public.public_results, name="public_results"),
]
