"""Landing page, health check and error pages."""
from django.http import HttpResponseServerError, JsonResponse
from django.template import loader
from django.shortcuts import render

from . import mongo


def landing(request):
    return render(request, "landing.html")


def health(request):
    """Public health check used by monitoring: is MongoDB reachable?"""
    ok = mongo.ping()
    return JsonResponse({"app": "ok", "mongodb": "ok" if ok else "unavailable"}, status=200 if ok else 503)


def error_403(request, exception=None):
    message = str(exception) if exception and str(exception) else "You don't have permission to open this page."
    return render(request, "errors/403.html", {"message": message}, status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    # Rendered without the request context: the error may have happened in a
    # context processor or middleware, so keep this page as simple as possible.
    return HttpResponseServerError(loader.get_template("errors/500.html").render())


def csrf_failure(request, reason=""):
    return render(request, "errors/403.html", {
        "message": "Your form expired or was sent from another site. Go back, reload the page and try again.",
    }, status=403)
