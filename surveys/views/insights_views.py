"""Analytics, responses browser, reports & exports, printable report, comparison."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from core import audit, mongo
from core.exceptions import NotFound, PermissionDeniedError

from .. import ai, exporters
from ..analytics import compare_questions, crosstab, day_bounds, load_responses, survey_report
from ..insights import build_insights
from ..question_types import CHOICE_LIKE, get_type
from ..survey_service import SurveyService
from .common import date_range, page_number, pagination, survey_errors

RESPONSES_PAGE_SIZE = 20


@login_required
@survey_errors
def analytics_page(request, survey_id):
    survey = SurveyService(request.user).get(survey_id, "view")
    start, end = date_range(request)
    report = survey_report(survey, start, end)
    categorical = [(q["qid"], f"Q{i}. {q['title']}") for i, q in enumerate(survey["questions"], start=1)
                   if q["type"] in CHOICE_LIKE]
    ct_rows, ct_cols = request.GET.get("ct_rows", ""), request.GET.get("ct_cols", "")
    table = None
    if ct_rows and ct_cols:
        s, e = day_bounds(start, end)
        table = crosstab(survey, load_responses(survey["_id"], s, e), ct_rows, ct_cols)
    return render(request, "surveys/analytics.html", {
        "survey": survey, "report": report, "insights": build_insights(report),
        "start": start, "end": end, "categorical": categorical,
        "ct_rows": ct_rows, "ct_cols": ct_cols, "crosstab": table,
        "ai_enabled": ai.is_configured(), "nav": "surveys",
        "chart_data": {
            "trend": report["trend"],
            "questions": [{k: q.get(k) for k in ("qid", "kind", "options", "histogram", "by_month", "ranking", "domains", "low", "high")}
                          for q in report["questions"]],
        },
    })


@login_required
@survey_errors
def responses_page(request, survey_id):
    """Browse individual responses, newest first (paginated)."""
    survey = SurveyService(request.user).get(survey_id, "view")
    start, end = date_range(request)
    s, e = day_bounds(start, end)
    query = {"survey_id": survey["_id"]}
    if s or e:
        query["submitted_at"] = {k: v for k, v in (("$gte", s), ("$lt", e)) if v}
    total = mongo.db_call(mongo.responses().count_documents, query)
    page = page_number(request)
    pager = pagination(page, RESPONSES_PAGE_SIZE, total)
    cursor = (mongo.responses().find(query).sort("submitted_at", -1)
              .skip((pager["page"] - 1) * RESPONSES_PAGE_SIZE).limit(RESPONSES_PAGE_SIZE))
    rows = []
    for number, doc in enumerate(mongo.db_call(list, cursor), start=pager["start"]):
        answers = {a["qid"]: a for a in doc.get("answers", [])}
        rows.append({
            "number": total - number + 1,
            "doc": doc,
            "answers": [(q, get_type(q["type"]).display(q, answers[q["qid"]]) if q["qid"] in answers else None)
                        for q in survey["questions"]],
        })
    return render(request, "surveys/responses.html", {"survey": survey, "rows": rows, "pager": pager,
                                                       "start": start, "end": end, "nav": "surveys"})


@login_required
@survey_errors
def reports_page(request, survey_id):
    survey = SurveyService(request.user).get(survey_id, "export")
    start, end = date_range(request)
    return render(request, "surveys/reports.html", {"survey": survey, "start": start, "end": end, "nav": "reports"})


@login_required
def reports_index(request):
    surveys, _ = SurveyService(request.user).list_surveys(sort="updated", page_size=100)
    return render(request, "surveys/reports_index.html", {"surveys": surveys, "nav": "reports"})


def _csv_response(survey, kind):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{exporters.filename(survey, kind)}"'
    response["X-Content-Type-Options"] = "nosniff"
    exporters.write_bom(response)
    return response


def export_responses(request, survey_id):
    """Shared by the web page and the REST API (both pass an authenticated request)."""
    survey = SurveyService(request.user).get(survey_id, "export")
    start, end = date_range(request)
    s, e = day_bounds(start, end)
    response = _csv_response(survey, "responses")
    exporters.responses_csv(survey, load_responses(survey["_id"], s, e), response)
    audit.log(request.user, "survey.export", "survey", survey["_id"], {"kind": "responses"}, request)
    return response


def export_analytics(request, survey_id):
    survey = SurveyService(request.user).get(survey_id, "export")
    start, end = date_range(request)
    report = survey_report(survey, start, end, include_samples=False)
    response = _csv_response(survey, "analytics")
    exporters.analytics_csv(report, build_insights(report), response)
    audit.log(request.user, "survey.export", "survey", survey["_id"], {"kind": "analytics"}, request)
    return response


export_responses_view = login_required(survey_errors(export_responses))
export_analytics_view = login_required(survey_errors(export_analytics))


@login_required
@survey_errors
def print_report(request, survey_id):
    """Printable summary. Use the browser's Print → 'Save as PDF' for a PDF."""
    survey = SurveyService(request.user).get(survey_id, "export")
    start, end = date_range(request)
    report = survey_report(survey, start, end, include_samples=False)
    return render(request, "surveys/print_report.html", {"survey": survey, "report": report,
                                                          "insights": build_insights(report)})


@login_required
def compare_page(request):
    service = SurveyService(request.user)
    choices = service.accessible_choices()
    a_id, b_id = request.GET.get("a", ""), request.GET.get("b", "")
    qa, qb = request.GET.get("qa", ""), request.GET.get("qb", "")
    start, end = date_range(request)
    survey_a = survey_b = result = None
    try:
        if a_id:
            survey_a = service.get(a_id, "view")
        if b_id:
            survey_b = service.get(b_id, "view")
    except (NotFound, PermissionDeniedError):  # ignore a survey the user cannot open
        survey_a = survey_b = None
    if survey_a and survey_b and qa and qb:
        result = compare_questions(survey_a, qa, survey_b, qb, start, end)

    def comparable(survey):
        return [(q["qid"], f"Q{i}. {q['title']}") for i, q in enumerate(survey["questions"], start=1)
                if get_type(q["type"]).analysis in ("choice", "multi", "scale", "numeric")] if survey else []

    return render(request, "surveys/compare.html", {
        "choices": choices, "a_id": a_id, "b_id": b_id, "qa": qa, "qb": qb,
        "survey_a": survey_a, "survey_b": survey_b,
        "questions_a": comparable(survey_a), "questions_b": comparable(survey_b),
        "result": result, "start": start, "end": end, "nav": "compare",
    })
