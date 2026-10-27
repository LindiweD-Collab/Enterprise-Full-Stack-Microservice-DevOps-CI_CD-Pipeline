"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from core.views import HealthView

handler404 = "core.views.not_found"
handler500 = "core.views.server_error"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="swagger-ui", permanent=False)),
    path("admin/", admin.site.urls),
    path("health/", HealthView.as_view(), name="health"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("ticketing.urls")),
]
