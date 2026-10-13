"""REST API routes, mounted under /api/ (see docs/API.md)."""
from django.urls import path

from adminpanel import api as admin_api

from . import views as v

app_name = "api"

urlpatterns = [
    path("surveys/", v.SurveyListAPI.as_view(), name="surveys"),
    path("surveys/<str:survey_id>/", v.SurveyDetailAPI.as_view(), name="survey"),
    path("surveys/<str:survey_id>/settings/", v.SurveySettingsAPI.as_view(), name="survey_settings"),
    path("surveys/<str:survey_id>/publish/", v.SurveyActionAPI.as_view(action="publish"), name="survey_publish"),
    path("surveys/<str:survey_id>/close/", v.SurveyActionAPI.as_view(action="close"), name="survey_close"),
    path("surveys/<str:survey_id>/archive/", v.SurveyActionAPI.as_view(action="archive"), name="survey_archive"),
    path("surveys/<str:survey_id>/restore/", v.SurveyActionAPI.as_view(action="restore"), name="survey_restore"),
    path("surveys/<str:survey_id>/duplicate/", v.SurveyActionAPI.as_view(action="duplicate"), name="survey_duplicate"),
    path("surveys/<str:survey_id>/questions/", v.QuestionsAPI.as_view(), name="questions"),
    path("surveys/<str:survey_id>/questions/reorder/", v.QuestionReorderAPI.as_view(), name="questions_reorder"),
    path("surveys/<str:survey_id>/questions/<str:qid>/", v.QuestionDetailAPI.as_view(), name="question"),
    path("surveys/<str:survey_id>/quality/", v.QualityAPI.as_view(), name="quality"),
    path("surveys/<str:survey_id>/analytics/", v.AnalyticsAPI.as_view(), name="analytics"),
    path("surveys/<str:survey_id>/analytics/ai-summary/", v.AISummaryAPI.as_view(), name="ai_summary"),
    path("surveys/<str:survey_id>/export/responses.csv", v.ExportResponsesAPI.as_view(), name="export_responses"),
    path("surveys/<str:survey_id>/export/analytics.csv", v.ExportAnalyticsAPI.as_view(), name="export_analytics"),
    path("ai/suggest-questions/", v.SuggestQuestionsAPI.as_view(), name="ai_suggest"),
    # public
    path("public/surveys/<str:token>/", v.PublicSurveyAPI.as_view(), name="public_survey"),
    path("public/surveys/<str:token>/responses/", v.PublicSubmitAPI.as_view(), name="public_submit"),
    # administrators
    path("admin/stats/", admin_api.StatsAPI.as_view(), name="admin_stats"),
    path("admin/users/", admin_api.UsersAPI.as_view(), name="admin_users"),
    path("admin/users/<int:user_id>/status/", admin_api.UserStatusAPI.as_view(), name="admin_user_status"),
    path("admin/surveys/<str:survey_id>/moderation/", admin_api.ModerationAPI.as_view(), name="admin_moderation"),
    path("admin/audit-logs/", admin_api.AuditAPI.as_view(), name="admin_audit"),
]
