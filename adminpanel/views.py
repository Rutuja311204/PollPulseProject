"""
Admin panel views (/admin-panel/). Every view is wrapped in
@admin_required, which checks the role on the server for each request.
State-changing views accept POST only (CSRF protected) and are audited.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core import audit
from core.exceptions import NotFound, PermissionDeniedError, ValidationFailed
from core.platform import get_settings, update_settings
from core.roles import admin_required
from surveys.views.common import page_number, pagination

from . import services

USERS_PAGE_SIZE = 15
SURVEYS_PAGE_SIZE = 15
AUDIT_PAGE_SIZE = 25


def _handle(request, func, *args, **kwargs):
    """Run an admin action; show validation/permission problems as messages."""
    try:
        func(*args, **kwargs)
        return True
    except ValidationFailed as exc:
        messages.error(request, exc.errors[0])
    except PermissionDeniedError as exc:
        messages.error(request, exc.message)
    except NotFound as exc:
        raise Http404(exc.message)
    return False


@admin_required
def dashboard(request):
    granularity = request.GET.get("period", "day")
    return render(request, "adminpanel/dashboard.html", {
        "stats": services.overview(),
        "activity": services.activity(granularity),
        "registrations": services.recent_registrations(),
        "publications": services.recent_publications(),
        "health": services.health(),
        "recent_audit": audit.search(admin_only=True, page_size=6)[0],
        "anav": "dashboard",
    })


@admin_required
def users(request):
    filters = {k: request.GET.get(k, "") for k in ("q", "status", "role")}
    paginator = Paginator(services.search_users(**filters), USERS_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    counts = services.survey_counts([u.id for u in page])
    rows = [{"user": u, "surveys": counts.get(u.id, {}).get("count", 0),
             "responses": counts.get(u.id, {}).get("responses", 0)} for u in page]
    return render(request, "adminpanel/users.html", {"rows": rows, "page": page, "filters": filters, "anav": "users"})


@admin_required
def user_detail(request, user_id):
    try:
        target = services.get_user(user_id)
    except NotFound:
        raise Http404("User not found.")
    owned = services.surveys_of_user(target.id)
    history = audit.search(actor=target.username, page_size=10)[0]
    return render(request, "adminpanel/user_detail.html", {
        "target": target, "surveys": owned, "history": history, "anav": "users",
    })


@admin_required
@require_POST
def user_status(request, user_id):
    if _handle(request, services.set_user_status, request.user, user_id, request.POST.get("action"),
               request.POST.get("reason", ""), request):
        messages.success(request, "Account updated.")
    return redirect("adminpanel:user_detail", user_id=user_id)


@admin_required
@require_POST
def user_role(request, user_id):
    if _handle(request, services.set_admin_role, request.user, user_id, request.POST.get("grant") == "1", request):
        messages.success(request, "Role updated.")
    return redirect("adminpanel:user_detail", user_id=user_id)


@admin_required
def surveys(request):
    filters = {k: request.GET.get(k, "") for k in ("q", "status", "moderation")}
    page = page_number(request)
    items, total = services.search_surveys(page=page, page_size=SURVEYS_PAGE_SIZE, **filters)
    return render(request, "adminpanel/surveys.html", {
        "surveys": items, "filters": filters, "pager": pagination(page, SURVEYS_PAGE_SIZE, total), "anav": "surveys",
    })


@admin_required
def survey_detail(request, survey_id):
    try:
        survey = services.get_survey(survey_id)
    except NotFound:
        raise Http404("Survey not found.")
    history = audit.search(target_id=survey["id"], page_size=15)[0]
    return render(request, "adminpanel/survey_detail.html", {"survey": survey, "history": history, "anav": "surveys"})


@admin_required
@require_POST
def survey_moderate(request, survey_id):
    if _handle(request, services.moderate_survey, request.user, survey_id, request.POST.get("action"),
               request.POST.get("reason", ""), request):
        messages.success(request, "Moderation action applied.")
    return redirect("adminpanel:survey_detail", survey_id=survey_id)


@admin_required
def audit_log(request):
    filters = {"action": request.GET.get("action", ""), "actor": request.GET.get("actor", "").strip()}
    if filters["action"] not in audit.ACTION_LABELS:
        filters["action"] = ""
    page = page_number(request)
    entries, total = audit.search(page=page, page_size=AUDIT_PAGE_SIZE, **filters)
    return render(request, "adminpanel/audit.html", {
        "entries": entries, "filters": filters, "actions": sorted(audit.ACTION_LABELS.items(), key=lambda kv: kv[1]),
        "pager": pagination(page, AUDIT_PAGE_SIZE, total), "anav": "audit",
    })


@admin_required
def platform_settings(request):
    current = get_settings()
    errors = []
    if request.method == "POST":
        try:
            update_settings(request.POST.get("registration_open") == "on", request.POST.get("announcement", ""),
                            request.POST.get("max_surveys_per_user", ""), request.user)
            audit.log(request.user, "admin.settings", "platform", "global", {
                "registration_open": request.POST.get("registration_open") == "on"}, request)
            messages.success(request, "Platform settings saved.")
            return redirect("adminpanel:settings")
        except ValidationFailed as exc:
            errors = exc.errors
            current.update({"announcement": request.POST.get("announcement", ""),
                            "max_surveys_per_user": request.POST.get("max_surveys_per_user", "")})
    return render(request, "adminpanel/settings.html", {"settings": current, "errors": errors, "anav": "settings"})

