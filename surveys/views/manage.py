"""Creator pages: dashboard, survey list, create, overview, builder, settings,
collaborators and status actions. Views stay thin: they read the request,
call SurveyService and render."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import audit, mongo
from core.exceptions import ConflictError, ValidationFailed
from core.platform import get_settings as platform_settings

from .. import ai, quality
from ..analytics import daily_trend, load_responses
from ..forms import CollaboratorForm, NewSurveyForm, SurveyDetailsForm, SurveySettingsForm
from ..question_types import builder_type_catalog
from ..survey_service import SORTS, STATUSES, SurveyService
from .common import action_view, page_number, pagination, survey_errors

PAGE_SIZE = 10


@login_required
def dashboard(request):
    service = SurveyService(request.user)
    stats = service.dashboard()
    trend = []
    if stats["total"]:
        today = timezone.localdate()
        start = today - timedelta(days=29)
        ids = service.all_ids()
        cursor = mongo.responses().find({"survey_id": {"$in": ids}}, {"submitted_at": 1})
        trend = daily_trend(mongo.db_call(list, cursor), start, today)
    return render(request, "surveys/dashboard.html", {
        "stats": stats, "trend": trend, "nav": "dashboard",
        "activity": audit.recent_for_user(request.user.id, 6),
        "announcement": platform_settings()["announcement"],
    })


@login_required
def survey_list(request):
    service = SurveyService(request.user)
    filters = {
        "q": request.GET.get("q", "").strip(),
        "status": request.GET.get("status", "") if request.GET.get("status", "") in STATUSES else "",
        "scope": request.GET.get("scope", "all") if request.GET.get("scope") in ("all", "owned", "shared") else "all",
        "sort": request.GET.get("sort", "newest") if request.GET.get("sort") in SORTS else "newest",
    }
    page = page_number(request)
    surveys, total = service.list_surveys(page=page, page_size=PAGE_SIZE, **filters)
    return render(request, "surveys/survey_list.html", {
        "surveys": surveys, "filters": filters, "nav": "surveys",
        "filtered": bool(filters["q"] or filters["status"] or filters["scope"] != "all"),
        "pager": pagination(page, PAGE_SIZE, total),
    })


@login_required
def survey_create(request):
    form = NewSurveyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        questions, note = [], ""
        if form.cleaned_data["start"] == "suggest":
            result = ai.suggest_questions(form.cleaned_data["topic"], form.cleaned_data["objective"])
            questions, note = result["questions"], result["note"]
        try:
            survey_id = SurveyService(request.user).create(
                form.cleaned_data["title"], form.cleaned_data["description"], questions, request=request)
        except ConflictError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "Draft created. " + (note or "Add your questions, then publish."))
            return redirect("surveys:builder", survey_id=survey_id)
    return render(request, "surveys/survey_create.html", {"form": form, "nav": "create",
                                                          "ai_enabled": ai.is_configured()})


@login_required
@survey_errors
def survey_overview(request, survey_id):
    service = SurveyService(request.user)
    survey = service.get(survey_id)
    issues = quality.check_survey(survey)
    recent = load_responses(survey["_id"])[-5:][::-1]
    return render(request, "surveys/survey_overview.html", {
        "survey": survey, "nav": "surveys",
        "share_url": request.build_absolute_uri(reverse("surveys:respond", args=[survey["public_token"]])),
        "issues": issues, "issue_counts": quality.summary(issues),
        "recent_responses": recent,
    })


@login_required
@survey_errors
def survey_builder(request, survey_id):
    survey = SurveyService(request.user).get(survey_id, "view")
    return render(request, "surveys/builder.html", {
        "survey": survey, "nav": "surveys",
        "builder_data": {
            "surveyId": survey["id"],
            "title": survey["title"],
            "status": survey["status"],
            "locked": survey["locked"] or survey["status"] == "archived",
            "canEdit": survey["can"]["edit"],
            "canPublish": survey["can"]["publish"],
            "questions": survey["questions"],
            "types": builder_type_catalog(),
            "issues": [i.as_dict() for i in quality.check_survey(survey)],
            "aiEnabled": ai.is_configured(),
            "urls": {
                "questions": reverse("api:questions", args=[survey["id"]]),
                "publish": reverse("api:survey_publish", args=[survey["id"]]),
                "suggest": reverse("api:ai_suggest"),
                "overview": reverse("surveys:overview", args=[survey["id"]]),
                "preview": reverse("surveys:preview", args=[survey["id"]]),
            },
        },
    })


@login_required
@survey_errors
def survey_settings(request, survey_id):
    service = SurveyService(request.user)
    survey = service.get(survey_id, "edit")
    s = survey["settings"]
    initial = {**s, "title": survey["title"], "description": survey["description"],
               "start_at": timezone.localtime(s["start_at"]) if s["start_at"] else None,
               "end_at": timezone.localtime(s["end_at"]) if s["end_at"] else None}

    details_form = SurveyDetailsForm(initial=initial, prefix="details")
    settings_form = SurveySettingsForm(initial=initial, prefix="settings")
    if request.method == "POST":
        details_form = SurveyDetailsForm(request.POST, prefix="details")
        settings_form = SurveySettingsForm(request.POST, prefix="settings")
        if details_form.is_valid() and settings_form.is_valid():
            try:
                service.update_details(survey_id, details_form.cleaned_data["title"], details_form.cleaned_data["description"])
                service.update_settings(survey_id, settings_form.cleaned_settings, request)
                messages.success(request, "Settings saved.")
                return redirect("surveys:settings", survey_id=survey_id)
            except (ValidationFailed, ConflictError) as exc:
                for error in getattr(exc, "errors", [exc.message]):
                    settings_form.add_error(None, error)
    return render(request, "surveys/survey_settings.html", {
        "survey": survey, "details_form": details_form, "settings_form": settings_form,
        "collaborator_form": CollaboratorForm(), "nav": "surveys",
    })


@login_required
@require_POST
@survey_errors
@action_view
def collaborator_add(request, survey_id):
    form = CollaboratorForm(request.POST)
    if form.is_valid():
        user = SurveyService(request.user).add_collaborator(survey_id, form.cleaned_data["username"], form.cleaned_data["role"], request)
        messages.success(request, f"{user.username} can now access this survey as {form.cleaned_data['role']}.")
    else:
        messages.error(request, "Enter a username and choose a role.")
    return redirect(reverse("surveys:settings", args=[survey_id]) + "#collaborators")


@login_required
@require_POST
@survey_errors
@action_view
def collaborator_remove(request, survey_id, user_id):
    SurveyService(request.user).remove_collaborator(survey_id, user_id, request)
    messages.success(request, "Collaborator removed.")
    return redirect(reverse("surveys:settings", args=[survey_id]) + "#collaborators")


# ---------- status actions (POST only, CSRF protected) ----------

@login_required
@require_POST
@survey_errors
@action_view
def survey_publish(request, survey_id):
    service = SurveyService(request.user)
    was_closed = service.get(survey_id)["status"] == "closed"
    service.publish(survey_id, request)
    messages.success(request, "Survey reopened." if was_closed else "Survey published. Share the link to collect responses.")
    return redirect("surveys:overview", survey_id=survey_id)


@login_required
@require_POST
@survey_errors
@action_view
def survey_close(request, survey_id):
    SurveyService(request.user).close(survey_id, request)
    messages.success(request, "Survey closed. It no longer accepts responses.")
    return redirect("surveys:overview", survey_id=survey_id)


@login_required
@require_POST
@survey_errors
@action_view
def survey_archive(request, survey_id):
    SurveyService(request.user).archive(survey_id, request)
    messages.success(request, "Survey archived. Find it under the 'Archived' filter.")
    return redirect("surveys:list")


@login_required
@require_POST
@survey_errors
@action_view
def survey_restore(request, survey_id):
    SurveyService(request.user).restore(survey_id, request)
    messages.success(request, "Survey restored.")
    return redirect("surveys:overview", survey_id=survey_id)


@login_required
@require_POST
@survey_errors
@action_view
def survey_duplicate(request, survey_id):
    new_id = SurveyService(request.user).duplicate(survey_id, request)
    messages.success(request, "Copy created as a draft.")
    return redirect("surveys:builder", survey_id=new_id)


@login_required
@survey_errors
def survey_delete(request, survey_id):
    service = SurveyService(request.user)
    survey = service.get(survey_id, "delete")
    if request.method == "POST":
        if request.POST.get("confirm_title", "").strip() != survey["title"]:
            messages.error(request, "Type the survey title exactly to confirm deletion.")
            return redirect("surveys:delete", survey_id=survey_id)
        removed = service.delete(survey_id, request)
        messages.success(request, f"Deleted “{survey['title']}” and {removed} response{'s' if removed != 1 else ''}.")
        return redirect("surveys:list")
    return render(request, "surveys/survey_delete.html", {"survey": survey, "nav": "surveys"})
