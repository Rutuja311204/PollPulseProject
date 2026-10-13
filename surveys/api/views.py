"""
REST API (Django REST Framework). Base URL: /api/

Authentication is the normal session login (same as the website). For
logged-in users DRF's SessionAuthentication enforces the CSRF token on
POST/PUT/PATCH/DELETE, so other sites cannot call the API on your behalf.

Error format: {"detail": "...", "errors": [...], "field_errors": {...}}
with HTTP status 400 / 403 / 404 / 409 / 429 / 503.
"""
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from core.exceptions import PollPulseError, SurveyUnavailable, ValidationFailed

from .. import ai, quality
from ..analytics import survey_report
from ..insights import build_insights
from ..response_service import ResponseService, respondent_for
from ..survey_service import SurveyService, find_by_token
from ..views.common import date_range
from ..views.insights_views import export_analytics, export_responses
from . import serializers as s


def exception_handler(exc, context):
    """Convert PollPulse exceptions into JSON; defer to DRF for the rest."""
    if isinstance(exc, PollPulseError):
        body = {"detail": exc.message}
        if isinstance(exc, ValidationFailed):
            body["errors"] = exc.errors
            if exc.field_errors:
                body["field_errors"] = exc.field_errors
        if isinstance(exc, SurveyUnavailable):
            body["reason"] = exc.reason
        return Response(body, status=exc.status_code)
    return drf_exception_handler(exc, context)


def validated(serializer_class, data, **kwargs):
    serializer = serializer_class(data=data, **kwargs)
    serializer.is_valid(raise_exception=True)
    return serializer


# ---------------------------------------------------------------- surveys

class SurveyListAPI(APIView):
    def get(self, request):
        surveys, total = SurveyService(request.user).list_surveys(
            q=request.query_params.get("q", ""), status=request.query_params.get("status", ""),
            scope=request.query_params.get("scope", "all"), sort=request.query_params.get("sort", "newest"),
            page=int(request.query_params.get("page", 1) or 1), page_size=min(int(request.query_params.get("page_size", 20) or 20), 100))
        return Response({"count": total, "results": s.SurveySummarySerializer(surveys, many=True).data})

    def post(self, request):
        data = validated(s.SurveyCreateSerializer, request.data).validated_data
        survey_id = SurveyService(request.user).create(data["title"], data["description"], request=request)
        survey = SurveyService(request.user).get(survey_id)
        return Response(s.SurveyDetailSerializer(survey).data, status=status.HTTP_201_CREATED)


class SurveyDetailAPI(APIView):
    def get(self, request, survey_id):
        return Response(s.SurveyDetailSerializer(SurveyService(request.user).get(survey_id)).data)

    def patch(self, request, survey_id):
        service = SurveyService(request.user)
        current = service.get(survey_id, "edit")
        data = validated(s.SurveyUpdateSerializer, request.data).validated_data
        service.update_details(survey_id, data.get("title", current["title"]), data.get("description", current["description"]))
        return Response(s.SurveyDetailSerializer(service.get(survey_id)).data)

    def delete(self, request, survey_id):
        removed = SurveyService(request.user).delete(survey_id, request)
        return Response({"deleted": True, "responses_removed": removed})


class SurveySettingsAPI(APIView):
    def put(self, request, survey_id):
        data = validated(s.SettingsSerializer, request.data).validated_data
        clean = SurveyService(request.user).update_settings(survey_id, data, request)
        return Response(clean)


class SurveyActionAPI(APIView):
    """POST /api/surveys/<id>/<action>/ for publish, close, archive, restore, duplicate."""

    action = None

    def post(self, request, survey_id):
        service = SurveyService(request.user)
        if self.action == "duplicate":
            new_id = service.duplicate(survey_id, request)
            return Response({"id": new_id}, status=status.HTTP_201_CREATED)
        getattr(service, self.action)(survey_id, request)
        survey = service.get(survey_id)
        return Response({"id": survey["id"], "status": survey["status"], "accepting": survey["accepting"]})


# ---------------------------------------------------------------- questions

class QuestionsAPI(APIView):
    def get(self, request, survey_id):
        survey = SurveyService(request.user).get(survey_id)
        return Response({"questions": s.QuestionOutSerializer(survey["questions"], many=True).data,
                         "locked": survey["locked"]})

    def put(self, request, survey_id):
        """Replace the full question list (used by the builder's Save)."""
        data = validated(s.QuestionListSerializer, request.data).validated_data
        service = SurveyService(request.user)
        service.save_questions(survey_id, data["questions"])
        survey = service.get(survey_id)
        return Response({
            "questions": s.QuestionOutSerializer(survey["questions"], many=True).data,
            "issues": [issue.as_dict() for issue in quality.check_survey(survey)],
        })

    def post(self, request, survey_id):
        data = validated(s.QuestionInputSerializer, request.data).validated_data
        question = SurveyService(request.user).add_question(survey_id, data)
        return Response(s.QuestionOutSerializer(question).data, status=status.HTTP_201_CREATED)


class QuestionDetailAPI(APIView):
    def patch(self, request, survey_id, qid):
        data = validated(s.QuestionInputSerializer, request.data, partial=True).validated_data
        question = SurveyService(request.user).update_question(survey_id, qid, data)
        return Response(s.QuestionOutSerializer(question).data)

    def delete(self, request, survey_id, qid):
        SurveyService(request.user).delete_question(survey_id, qid)
        return Response(status=status.HTTP_204_NO_CONTENT)


class QuestionReorderAPI(APIView):
    def post(self, request, survey_id):
        data = validated(s.ReorderSerializer, request.data).validated_data
        questions = SurveyService(request.user).reorder_questions(survey_id, data["order"])
        return Response({"order": [q["qid"] for q in questions]})


class QualityAPI(APIView):
    def get(self, request, survey_id):
        survey = SurveyService(request.user).get(survey_id)
        issues = quality.check_survey(survey)
        return Response({"summary": quality.summary(issues), "issues": [i.as_dict() for i in issues]})


# ---------------------------------------------------------------- analytics & export

class AnalyticsAPI(APIView):
    def get(self, request, survey_id):
        survey = SurveyService(request.user).get(survey_id, "view")
        start, end = date_range(request)
        report = survey_report(survey, start, end, include_samples=False)
        return Response({**report, "insights": build_insights(report)})


class AISummaryAPI(APIView):
    throttle_scope = "ai"

    def post(self, request, survey_id):
        survey = SurveyService(request.user).get(survey_id, "view")
        if not ai.is_configured():
            return Response({"detail": "No AI service is configured."}, status=status.HTTP_400_BAD_REQUEST)
        start, end = date_range(request)
        findings = build_insights(survey_report(survey, start, end, include_samples=False))
        text = ai.rewrite_insights(findings)
        if not text:
            return Response({"detail": "The AI service did not respond. The rule-based summary is still shown."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"summary": text, "model": ai.provider_label()})


class ExportResponsesAPI(APIView):
    def get(self, request, survey_id):
        return export_responses(request, survey_id)


class ExportAnalyticsAPI(APIView):
    def get(self, request, survey_id):
        return export_analytics(request, survey_id)


class SuggestQuestionsAPI(APIView):
    throttle_scope = "ai"

    def post(self, request):
        data = validated(s.SuggestSerializer, request.data).validated_data
        return Response(ai.suggest_questions(data["topic"], data["objective"]))


# ---------------------------------------------------------------- public

class PublicSurveyAPI(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        survey = find_by_token(token)
        if survey["status"] in ("draft", "archived") or survey["suspended"]:
            return Response({"detail": "Survey not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(s.PublicSurveySerializer(survey).data)


class PublicSubmitAPI(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "submit"

    def post(self, request, token):
        survey = find_by_token(token)
        data = validated(s.SubmitSerializer, request.data)
        respondent = respondent_for(request, survey)
        result = ResponseService(survey).submit(respondent, data.raw_by_qid(),
                                                consent=data.validated_data["consent"], source="api")
        message = survey["settings"]["thank_you_message"] or "Thank you! Your response has been recorded."
        return Response({**result, "detail": message},
                        status=status.HTTP_200_OK if result["edited"] else status.HTTP_201_CREATED)

