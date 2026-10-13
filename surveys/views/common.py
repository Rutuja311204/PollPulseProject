"""Helpers shared by the survey views."""
from datetime import date
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from core.exceptions import ConflictError, NotFound, PermissionDeniedError, RateLimited, ValidationFailed


def survey_errors(view):
    """Translate service-layer exceptions into normal Django behaviour:
    NotFound -> 404 page, PermissionDeniedError -> 403 page."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except NotFound as exc:
            raise Http404(exc.message)
        except PermissionDeniedError as exc:
            raise PermissionDenied(exc.message)

    return wrapper


def action_view(view):
    """For POST actions (publish, close ...): show problems as flash messages
    and go back, instead of an error page."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except ValidationFailed as exc:
            for error in exc.errors[:6]:
                messages.error(request, error)
        except (ConflictError, RateLimited) as exc:
            messages.error(request, exc.message)
        return redirect(safe_next(request, request.META.get("HTTP_REFERER") or "/dashboard/"))

    return wrapper


def safe_next(request, fallback):
    """Return ?next= / POST next only if it points to this site (no open redirect)."""
    target = request.POST.get("next") or request.GET.get("next") or fallback
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return "/dashboard/"


def parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def date_range(request):
    """Read ?start=YYYY-MM-DD&end=YYYY-MM-DD; ignore invalid or reversed values."""
    start, end = parse_date(request.GET.get("start")), parse_date(request.GET.get("end"))
    if start and end and start > end:
        start, end = end, start
    return start, end


def page_number(request):
    try:
        return max(int(request.GET.get("page", 1)), 1)
    except ValueError:
        return 1


def pagination(page, page_size, total):
    pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, pages)
    return {"page": page, "pages": pages, "total": total, "has_prev": page > 1, "has_next": page < pages,
            "prev": page - 1, "next": page + 1,
            "start": (page - 1) * page_size + 1 if total else 0, "end": min(page * page_size, total)}
