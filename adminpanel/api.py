"""Admin REST endpoints — every view requires IsPlatformAdmin (server-side)."""
from django.core.paginator import Paginator
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from core.roles import IsPlatformAdmin
from surveys.api.serializers import ModerationSerializer, UserStatusSerializer

from . import services


class AdminAPIView(APIView):
    permission_classes = [IsPlatformAdmin]


class StatsAPI(AdminAPIView):
    def get(self, request):
        return Response({"overview": services.overview(),
                         "activity": services.activity(request.query_params.get("period", "day"))})


class UsersAPI(AdminAPIView):
    def get(self, request):
        users = services.search_users(request.query_params.get("q", ""), request.query_params.get("status", ""),
                                      request.query_params.get("role", ""))
        page = Paginator(users, 25).get_page(request.query_params.get("page"))
        return Response({
            "count": page.paginator.count,
            "results": [{"id": u.id, "username": u.username, "email": u.email, "is_active": u.is_active,
                         "is_admin": u.is_staff, "date_joined": u.date_joined} for u in page],
        })


class UserStatusAPI(AdminAPIView):
    def post(self, request, user_id):
        serializer = UserStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.set_user_status(request.user, user_id, serializer.validated_data["action"],
                                        serializer.validated_data["reason"], request)
        return Response({"id": user.id, "is_active": user.is_active})


class ModerationAPI(AdminAPIView):
    def post(self, request, survey_id):
        serializer = ModerationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.moderate_survey(request.user, survey_id, serializer.validated_data["action"],
                                 serializer.validated_data["reason"], request)
        survey = services.get_survey(survey_id)
        return Response({"id": survey["id"], "status": survey["status"], "moderation": survey.get("moderation", {})})


class AuditAPI(AdminAPIView):
    def get(self, request):
        page = int(request.query_params.get("page", 1) or 1)
        entries, total = audit.search(request.query_params.get("action", ""), request.query_params.get("actor", ""),
                                      page=max(page, 1))
        for entry in entries:
            entry["_id"] = str(entry["_id"])
        return Response({"count": total, "results": entries})
