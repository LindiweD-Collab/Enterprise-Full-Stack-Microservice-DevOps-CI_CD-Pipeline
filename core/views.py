"""Health check and JSON 404/500 handlers."""

import time
from datetime import datetime, timezone

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import build_error

_STARTED_MONOTONIC = time.monotonic()
_STARTED_AT = datetime.now(timezone.utc)


def check_database() -> dict:
    """Run a trivial query and report connectivity and latency."""
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - any failure means "down"
        return {"status": "down", "error": exc.__class__.__name__}
    return {
        "status": "up",
        "vendor": connection.vendor,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


_database_check = inline_serializer(
    "DatabaseCheck",
    {
        "status": serializers.ChoiceField(choices=["up", "down"]),
        "vendor": serializers.CharField(required=False),
        "latency_ms": serializers.FloatField(required=False),
        "error": serializers.CharField(required=False),
    },
)
_health_serializer = inline_serializer(
    "Health",
    {
        "status": serializers.ChoiceField(choices=["ok", "unhealthy"]),
        "service": serializers.CharField(),
        "version": serializers.CharField(),
        "environment": serializers.CharField(),
        "timestamp": serializers.DateTimeField(),
        "started_at": serializers.DateTimeField(),
        "uptime_seconds": serializers.FloatField(),
        "checks": inline_serializer("HealthChecks", {"database": _database_check}),
    },
)


class HealthView(APIView):
    """Liveness and readiness probe. Unauthenticated by design."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    pagination_class = None

    @extend_schema(
        summary="Service health",
        description=(
            "Reports service status, uptime and database connectivity. "
            "Returns 503 when the database is unreachable so orchestrators "
            "can take the instance out of rotation."
        ),
        responses={
            200: _health_serializer,
            503: OpenApiResponse(
                response=_health_serializer, description="Database unreachable"
            ),
        },
        auth=[],
        tags=["health"],
    )
    def get(self, request):
        database = check_database()
        healthy = database["status"] == "up"
        body = {
            "status": "ok" if healthy else "unhealthy",
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "environment": settings.ENVIRONMENT,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "started_at": _STARTED_AT.isoformat(),
            "uptime_seconds": round(time.monotonic() - _STARTED_MONOTONIC, 2),
            "checks": {"database": database},
        }
        code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(body, status=code)


def not_found(request, exception=None):
    return JsonResponse(
        build_error(
            "not_found", "The requested resource was not found.", 404, None, request
        ),
        status=404,
    )


def server_error(request):
    return JsonResponse(
        build_error(
            "server_error", "An unexpected error occurred.", 500, None, request
        ),
        status=500,
    )
