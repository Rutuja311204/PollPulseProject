"""
DRF serializers.

Input serializers check the SHAPE of JSON requests (types, required keys,
lengths). Business rules (option belongs to question, rating range, survey
open ...) are then enforced by the same service functions the HTML pages
use, so both paths apply identical validation.

Output serializers decide exactly which fields leave the server. For
example the public survey serializer never includes owner_id,
collaborators, response counts or settings that are only for the creator.
"""
from rest_framework import serializers

from ..question_types import REGISTRY


# ---------- input ----------

class SurveyCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class SurveyUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class SettingsSerializer(serializers.Serializer):
    access = serializers.ChoiceField(choices=["public", "login"])
    identity = serializers.ChoiceField(choices=["anonymous", "identified"])
    duplicate_protection = serializers.ChoiceField(choices=["none", "session", "user"])
    display_mode = serializers.ChoiceField(choices=["all", "one_per_page"])
    allow_edit = serializers.BooleanField()
    allow_save_resume = serializers.BooleanField()
    results_public = serializers.BooleanField()
    start_at = serializers.DateTimeField(allow_null=True, required=False, default=None)
    end_at = serializers.DateTimeField(allow_null=True, required=False, default=None)
    max_responses = serializers.IntegerField(allow_null=True, required=False, default=None, min_value=1)
    thank_you_message = serializers.CharField(allow_blank=True, required=False, default="", max_length=1000)
    consent_text = serializers.CharField(allow_blank=True, required=False, default="", max_length=2000)


class QuestionInputSerializer(serializers.Serializer):
    qid = serializers.CharField(required=False, allow_blank=True, max_length=20)
    type = serializers.ChoiceField(choices=list(REGISTRY))
    title = serializers.CharField(allow_blank=True, max_length=400, default="")
    description = serializers.CharField(allow_blank=True, required=False, default="", max_length=600)
    required = serializers.BooleanField(default=False)
    config = serializers.DictField(required=False, default=dict)
    options = serializers.ListField(child=serializers.JSONField(), required=False, default=list, max_length=60)


class QuestionListSerializer(serializers.Serializer):
    questions = QuestionInputSerializer(many=True)


class ReorderSerializer(serializers.Serializer):
    order = serializers.ListField(child=serializers.CharField(max_length=20), allow_empty=False)


class AnswerInputSerializer(serializers.Serializer):
    qid = serializers.CharField(max_length=20)
    value = serializers.JSONField(allow_null=True)
    other_text = serializers.CharField(required=False, allow_blank=True, max_length=300, default="")


class SubmitSerializer(serializers.Serializer):
    """{"answers": [{"qid": "q_...", "value": ...}], "consent": true}"""

    answers = AnswerInputSerializer(many=True)
    consent = serializers.BooleanField(default=False)

    def validate_answers(self, answers):
        qids = [a["qid"] for a in answers]
        if len(qids) != len(set(qids)):
            raise serializers.ValidationError("Each question can be answered only once.")
        return answers

    def raw_by_qid(self):
        return {a["qid"]: {"value": a["value"], "other_text": a.get("other_text", "")}
                for a in self.validated_data["answers"]}


class SuggestSerializer(serializers.Serializer):
    topic = serializers.CharField(max_length=120)
    objective = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class ModerationSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["suspend", "unsuspend", "archive"])
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class UserStatusSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["suspend", "activate"])
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


# ---------- output ----------

class OptionOutSerializer(serializers.Serializer):
    oid = serializers.CharField()
    text = serializers.CharField()


class QuestionOutSerializer(serializers.Serializer):
    qid = serializers.CharField()
    type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    required = serializers.BooleanField()
    config = serializers.DictField()
    options = OptionOutSerializer(many=True)


class SurveySummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    status = serializers.CharField()
    role = serializers.CharField()
    question_count = serializers.IntegerField()
    response_count = serializers.IntegerField()
    accepting = serializers.BooleanField()
    public_token = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class SurveyDetailSerializer(SurveySummarySerializer):
    description = serializers.CharField()
    settings = serializers.DictField()
    questions = QuestionOutSerializer(many=True)
    collaborators = serializers.SerializerMethodField()

    def get_collaborators(self, survey):
        return [{"user_id": c["user_id"], "username": c["username"], "role": c["role"]}
                for c in survey.get("collaborators", [])]


class PublicSurveySerializer(serializers.Serializer):
    """Only what a respondent needs to answer — no internal ids or counts."""

    token = serializers.CharField(source="public_token")
    title = serializers.CharField()
    description = serializers.CharField()
    questions = QuestionOutSerializer(many=True)
    consent_text = serializers.SerializerMethodField()
    accepting = serializers.BooleanField()

    def get_consent_text(self, survey):
        return survey["settings"]["consent_text"]
