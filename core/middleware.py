"""
Project middleware.

SecurityHeadersMiddleware
    Adds a Content-Security-Policy. All scripts, styles and fonts are served
    from our own /static/ folder (no CDNs), so the browser can be told to
    refuse scripts from anywhere else. This limits the damage of any
    cross-site-scripting bug. Inline styles are allowed because Bootstrap
    and the charts set style attributes; inline <script> is NOT allowed.

MongoErrorMiddleware
    When MongoDB is unreachable, show a friendly 503 page (or JSON for the
    API) instead of a crash.
"""
import logging

from django.http import JsonResponse
from django.shortcuts import render
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from .exceptions import DatabaseUnavailable

logger = logging.getLogger(__name__)

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'self'",
    "object-src 'none'",
])


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CSP)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


DB_ERRORS = (DatabaseUnavailable, ServerSelectionTimeoutError, ConnectionFailure)


class MongoErrorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, DB_ERRORS):
            return None
        logger.error("Database unavailable while serving %s: %s", request.path, exception)
        message = DatabaseUnavailable.default_message
        if request.path.startswith("/api/"):
            return JsonResponse({"detail": message}, status=503)
        return render(request, "errors/db_error.html", {"message": message}, status=503)
