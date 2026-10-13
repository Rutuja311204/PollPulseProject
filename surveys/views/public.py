"""Respondent-facing pages: answer a survey, save a draft, thank-you page,
public results, and the creator's preview."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect, render
from django.urls import reverse

from core import ratelimit
from core.exceptions import DuplicateResponse, NotFound, RateLimited, SurveyUnavailable, ValidationFailed

from ..analytics import survey_report
from ..question_types import get_type, option_labels
from ..response_service import (ResponseService, answer_to_raw, parse_form_answers, respondent_for,
                                validate_answers)
from ..survey_service import SurveyService, availability, find_by_token
from .common import survey_errors

SUBMIT_LIMIT, SUBMIT_WINDOW = 20, 600   # per IP per survey: 20 posts / 10 minutes


def build_items(survey, raw_by_qid=None, field_errors=None):
    """Everything the templates need to render each question input."""
    raw_by_qid = raw_by_qid or {}
    field_errors = field_errors or {}
    items = []
    for number, q in enumerate(survey["questions"], start=1):
        qtype = get_type(q["type"])
        item = {
            "q": q, "number": number, "raw": raw_by_qid.get(q["qid"]),
            "errors": field_errors.get(q["qid"], []),
            "rules": qtype.describe_rules(q), "template": f"surveys/respond/inputs/{q['type']}.html",
        }
        if q["type"] in ("rating", "linear_scale"):
            low, high = qtype.bounds(q)
            item["scale"] = list(range(low, high + 1))
        if q["type"] == "ranking":
            item["ranks"] = list(range(1, len(q["options"]) + 1))
        if q["type"] == "yes_no":
            item["labels"] = option_labels(q)
        raw = item["raw"]
        if isinstance(raw, dict):
            item["value"] = raw.get("value")
            item["other_text"] = raw.get("other_text", "")
            item["rank_map"] = raw.get("ranks", {})
        else:
            item["value"] = raw
        items.append(item)
    return items


def _render_form(request, survey, items, **extra):
    context = {"survey": survey, "items": items, "required_count": sum(1 for i in items if i["q"]["required"])}
    context.update(extra)
    return render(request, "surveys/respond/form.html", context)


def _unavailable(request, survey, reason, message):
    return render(request, "surveys/respond/unavailable.html",
                  {"survey": survey, "reason": reason, "message": message}, status=200 if reason != "draft" else 404)


@survey_errors
def respond(request, token):
    survey = find_by_token(token)
    try:
        respondent = respondent_for(request, survey)
    except SurveyUnavailable:
        return redirect_to_login(request.get_full_path())

    service = ResponseService(survey)
    existing = service.existing_response(respondent)
    editing = existing is not None and survey["settings"]["allow_edit"]
    accepting, reason, message = availability(survey)
    if not accepting and not (editing and reason == "limit_reached"):
        return _unavailable(request, survey, reason, message)
    if existing is not None and not editing:
        return redirect(reverse("surveys:thanks", args=[token]) + "?already=1")

    resume_token = request.POST.get("resume") or request.GET.get("resume") or ""
    if request.method == "POST":
        try:
            ratelimit.check(request, "submit", SUBMIT_LIMIT, SUBMIT_WINDOW, token)
        except RateLimited as exc:
            messages.error(request, exc.message)
            return redirect(request.path)
        raw = parse_form_answers(survey, request.POST)
        if request.POST.get("action") == "save_draft":
            try:
                saved_token = service.save_draft(respondent, raw, resume_token or None)
            except ValidationFailed as exc:
                messages.error(request, exc.errors[0])
                return redirect(request.path)
            resume_url = request.build_absolute_uri(reverse("surveys:respond", args=[token]) + f"?resume={saved_token}")
            return render(request, "surveys/respond/draft_saved.html", {"survey": survey, "resume_url": resume_url})
        try:
            result = service.submit(respondent, raw, consent=request.POST.get("consent") == "yes",
                                    resume_token=resume_token or None)
        except ValidationFailed as exc:
            return _render_form(request, survey, build_items(survey, raw, exc.field_errors), editing=editing,
                                form_errors=exc.errors, consent_error=exc.field_errors.get("consent"),
                                resume_token=resume_token)
        except DuplicateResponse:
            return redirect(reverse("surveys:thanks", args=[token]) + "?already=1")
        except SurveyUnavailable as exc:
            return _unavailable(request, survey, exc.reason, exc.message)
        return redirect(reverse("surveys:thanks", args=[token]) + ("?edited=1" if result["edited"] else ""))

    # GET: prefill from a saved draft, or from the existing response when editing
    raw = {}
    attempt = service.attempt_by_resume_token(resume_token) if resume_token else None
    if attempt:
        raw = attempt.get("draft", {})
    elif editing:
        answers = {a["qid"]: a for a in existing["answers"]}
        raw = {q["qid"]: answer_to_raw(q, answers.get(q["qid"])) for q in survey["questions"]}
    else:
        attempt = service.start_attempt(respondent)  # records "started" for completion rate
        if attempt.get("draft"):
            raw = attempt["draft"]
            resume_token = attempt["resume_token"]
    return _render_form(request, survey, build_items(survey, raw), editing=editing,
                        resume_token=resume_token or (attempt or {}).get("resume_token", ""),
                        has_draft=bool(raw) and not editing)


def thanks(request, token):
    try:
        survey = find_by_token(token)
    except NotFound:
        return render(request, "errors/404.html", status=404)
    return render(request, "surveys/respond/thanks.html", {
        "survey": survey,
        "already": request.GET.get("already") == "1",
        "edited": request.GET.get("edited") == "1",
    })


PUBLIC_KINDS = {"choice", "multi", "scale", "ranking", "numeric", "date"}


@survey_errors
def public_results(request, token):
    """Aggregated results for respondents — only if the creator enabled it.
    Text and email answers are never shown publicly."""
    survey = find_by_token(token)
    if not survey["settings"]["results_public"] or survey["status"] == "draft" or survey["suspended"]:
        return _unavailable(request, survey, "results_private", "The results of this survey are not public.")
    report = survey_report(survey, include_samples=False)
    for q in report["questions"]:
        q["public"] = q["kind"] in PUBLIC_KINDS
        if not q["public"]:
            q.pop("text", None)
            q.pop("domains", None)
    return render(request, "surveys/respond/public_results.html", {"survey": survey, "report": report})


@login_required
@survey_errors
def preview(request, survey_id):
    """The creator sees exactly what respondents see. Submitting runs the
    real validation but saves nothing."""
    survey = SurveyService(request.user).get(survey_id, "view")
    if request.method == "POST":
        raw = parse_form_answers(survey, request.POST)
        try:
            validate_answers(survey, raw)
            messages.success(request, "Preview: these answers are valid. Nothing was saved.")
            items = build_items(survey, raw)
        except ValidationFailed as exc:
            items = build_items(survey, raw, exc.field_errors)
            return _render_form(request, survey, items, preview=True, form_errors=exc.errors)
    else:
        items = build_items(survey)
    return _render_form(request, survey, items, preview=True)
