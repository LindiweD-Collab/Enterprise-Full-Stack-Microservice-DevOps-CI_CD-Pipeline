"""Standardised error handling.

Every error response, whatever raised it, has this shape::

    {
      "error": {
        "code": "validation_error",     # stable, machine-readable
        "message": "Request validation failed.",
        "status": 400,
        "details": {"field": ["..."]},  # null when there is nothing to add
        "request_id": "3f2c..."         # matches the X-Request-ID header
      }
    }
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class ConflictError(APIException):
    """409: the request is valid but clashes with current state."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state of the resource."
    default_code = "conflict"

    def __init__(self, detail=None, code=None, details=None):
        super().__init__(detail, code)
        self.extra = details


def build_error(code, message, http_status, details=None, request=None):
    return {
        "error": {
            "code": code,
            "message": message,
            "status": http_status,
            "details": details,
            "request_id": getattr(request, "request_id", None),
        }
    }


def _normalise(exc):
    """Translate framework-agnostic exceptions into API exceptions."""
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "error_dict") else list(exc.messages)
        return ValidationError(detail)
    if isinstance(exc, Http404):
        return NotFound()
    if isinstance(exc, DjangoPermissionDenied):
        return PermissionDenied()
    if isinstance(exc, ProtectedError):
        return ConflictError(
            "This resource is referenced by other records and cannot be deleted.",
            code="protected",
        )
    if isinstance(exc, IntegrityError):
        return ConflictError(
            "The request conflicts with existing data.", code="integrity_error"
        )
    return exc


def api_exception_handler(exc, context):
    exc = _normalise(exc)
    request = context.get("request")
    response = drf_exception_handler(exc, context)

    if response is None:
        logger.exception("Unhandled exception", exc_info=exc)
        return Response(
            build_error(
                "server_error",
                "An unexpected error occurred.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                request=request,
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    data = response.data
    details = getattr(exc, "extra", None)

    if isinstance(exc, ValidationError):
        code = "validation_error"
        message = "Request validation failed."
        details = data
    else:
        detail = getattr(exc, "detail", None)
        if isinstance(data, dict) and "detail" in data:
            message = str(data["detail"])
        else:
            message = str(exc)
        if isinstance(detail, dict) and "code" in detail:
            code = str(detail["code"])
        else:
            code = getattr(detail, "code", None) or exc.default_code
        if isinstance(exc, APIException) and not isinstance(detail, (dict, list)):
            code = str(code)

    response.data = build_error(
        code, message, response.status_code, details=details, request=request
    )
    return response
