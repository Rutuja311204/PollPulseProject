"""
Platform roles.

* Creator      – any active, signed-in user (default role after registering)
* Respondent   – anyone answering a survey; needs no account for public surveys
* Administrator – an active user with Django's `is_staff` flag

Admin access is enforced on the SERVER with these helpers; hiding links in
templates is only cosmetic. Superusers can additionally grant/revoke the
admin role. Suspending a user sets `is_active = False`, which Django's
authentication backend already refuses at login.
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


def is_admin(user):
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


def admin_required(view):
    """Decorator for admin-panel views: login required, then is_staff required (403)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_admin(request.user):
            raise PermissionDenied("Administrator access required.")
        return view(request, *args, **kwargs)

    return wrapper


class IsPlatformAdmin(BasePermission):
    """DRF permission class with the same rule as admin_required."""

    message = "Administrator access required."

    def has_permission(self, request, view):
        return is_admin(request.user)
