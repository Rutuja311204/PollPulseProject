"""Values available in every template."""
from django.conf import settings

from .roles import is_admin


def site(request):
    return {
        "contact_email": settings.CONTACT_EMAIL,
        "time_zone": settings.TIME_ZONE,
        "is_platform_admin": is_admin(getattr(request, "user", None)),
    }
