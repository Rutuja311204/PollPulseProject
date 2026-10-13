"""Top-level URL configuration."""
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.landing, name="landing"),
    path("health/", core_views.health, name="health"),
    path("accounts/", include("accounts.urls")),
    path("admin-panel/", include("adminpanel.urls")),
    path("api/", include("surveys.api.urls")),
    path("", include("surveys.urls")),
]

handler403 = "core.views.error_403"
handler404 = "core.views.error_404"
handler500 = "core.views.error_500"
