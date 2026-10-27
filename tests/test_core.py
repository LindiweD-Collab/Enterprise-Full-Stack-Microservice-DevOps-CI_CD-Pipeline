import json
from unittest import mock

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError, OperationalError
from django.db.models import ProtectedError
from django.test import RequestFactory

from core import exceptions
from core.views import server_error
from tests.conftest import assert_error
from ticketing.models import Event, Seat, Venue


@pytest.mark.django_db
class TestHealth:
    def test_ok(self, api_client):
        resp = api_client.get("/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "halcyon-ticketing"
        assert body["uptime_seconds"] >= 0
        db = body["checks"]["database"]
        assert db["status"] == "up"
        assert db["latency_ms"] >= 0
        assert db["vendor"]

    def test_requires_no_authentication(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer garbage")
        assert api_client.get("/health/").status_code == 200

    def test_database_down_returns_503(self, api_client):
        with mock.patch("core.views.connection") as conn:
            conn.cursor.side_effect = OperationalError("boom")
            resp = api_client.get("/health/")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unhealthy"
        assert body["checks"]["database"] == {
            "status": "down",
            "error": "OperationalError",
        }


@pytest.mark.django_db
class TestErrorSchema:
    def test_unknown_path_is_json_404(self, api_client):
        assert_error(api_client.get("/does/not/exist/"), 404, "not_found")

    def test_method_not_allowed(self, user_client):
        assert_error(user_client.delete("/api/v1/auth/me/"), 405, "method_not_allowed")

    def test_malformed_json(self, user_client):
        resp = user_client.post(
            "/api/v1/reservations/", "{not json", content_type="application/json"
        )
        assert_error(resp, 400, "parse_error")

    def test_unsupported_media_type(self, user_client):
        resp = user_client.post(
            "/api/v1/reservations/", "x=1", content_type="text/plain"
        )
        assert_error(resp, 415, "unsupported_media_type")

    def test_request_id_is_echoed_and_in_body(self, api_client):
        resp = api_client.get("/api/v1/venues/", HTTP_X_REQUEST_ID="trace-123")
        assert resp["X-Request-ID"] == "trace-123"
        assert resp.json()["error"]["request_id"] == "trace-123"

    def test_request_id_generated(self, api_client):
        resp = api_client.get("/health/")
        assert len(resp["X-Request-ID"]) == 32

    def test_django_validation_error_mapped(self):
        exc = DjangoValidationError({"title": ["Too short."]})
        response = exceptions.api_exception_handler(exc, {"request": None})
        assert response.status_code == 400
        assert response.data["error"]["code"] == "validation_error"
        assert response.data["error"]["details"] == {"title": ["Too short."]}

    def test_django_validation_error_list_mapped(self):
        response = exceptions.api_exception_handler(
            DjangoValidationError("bad"), {"request": None}
        )
        assert response.status_code == 400

    def test_integrity_error_mapped(self):
        response = exceptions.api_exception_handler(IntegrityError(), {"request": None})
        assert response.status_code == 409
        assert response.data["error"]["code"] == "integrity_error"

    def test_protected_error_mapped(self):
        response = exceptions.api_exception_handler(
            ProtectedError("x", set()), {"request": None}
        )
        assert response.status_code == 409
        assert response.data["error"]["code"] == "protected"

    def test_unhandled_exception_becomes_500_envelope(self):
        response = exceptions.api_exception_handler(
            RuntimeError("secret internals"), {"request": None}
        )
        assert response.status_code == 500
        error = response.data["error"]
        assert error["code"] == "server_error"
        assert "secret" not in json.dumps(response.data)

    def test_server_error_view(self):
        request = RequestFactory().get("/")
        request.request_id = "abc"
        resp = server_error(request)
        assert resp.status_code == 500
        assert json.loads(resp.content)["error"]["request_id"] == "abc"


@pytest.mark.django_db
class TestDocs:
    def test_schema_served(self, api_client):
        resp = api_client.get("/api/schema/")
        assert resp.status_code == 200

    def test_swagger_ui_and_redoc(self, api_client):
        assert api_client.get("/api/docs/").status_code == 200
        assert api_client.get("/api/redoc/").status_code == 200

    def test_root_redirects_to_docs(self, api_client):
        resp = api_client.get("/")
        assert resp.status_code == 302
        assert resp["Location"] == "/api/docs/"


@pytest.mark.django_db
class TestSeedCommand:
    def test_seed_is_idempotent(self):
        call_command("seed_demo")
        call_command("seed_demo")
        assert Venue.objects.filter(name="Halcyon Arena").count() == 1
        assert Seat.objects.count() == 60
        assert Event.objects.filter(title="Launch Night").count() == 1
