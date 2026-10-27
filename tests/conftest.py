from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from ticketing.models import Event, Seat, Venue

User = get_user_model()
PASSWORD = "Str0ng-Pass#2026"


@pytest.fixture(autouse=True)
def fast_password_hashing(settings):
    """PBKDF2 is deliberately slow; use MD5 in tests only."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def assert_error(response, status, code=None):
    """Assert the response follows the standard error envelope."""
    assert response.status_code == status, response.content
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "status", "details", "request_id"}
    assert error["status"] == status
    assert error["request_id"]
    if code:
        assert error["code"] == code, error
    return error


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username="admin",
        email="admin@example.com",
        password=PASSWORD,
        role=User.Role.ADMIN,
    )


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="alice", email="alice@example.com", password=PASSWORD
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        username="bob", email="bob@example.com", password=PASSWORD
    )


@pytest.fixture
def admin_client(admin_user):
    return _client_for(admin_user)


@pytest.fixture
def user_client(user):
    return _client_for(user)


@pytest.fixture
def other_client(other_user):
    return _client_for(other_user)


@pytest.fixture
def venue(db):
    venue = Venue.objects.create(name="Halcyon Arena", city="Johannesburg", capacity=20)
    Seat.objects.bulk_create(
        [
            Seat(
                venue=venue,
                section="Main",
                row=row,
                number=number,
                price_multiplier=Decimal("1.00" if row == "A" else "1.50"),
            )
            for row in "AB"
            for number in range(1, 6)
        ]
    )
    return venue


@pytest.fixture
def seats(venue):
    return list(venue.seats.order_by("row", "number"))


@pytest.fixture
def event(venue, admin_user):
    start = timezone.now() + timedelta(days=7)
    return Event.objects.create(
        venue=venue,
        title="Launch Night",
        description="Opening show",
        starts_at=start,
        ends_at=start + timedelta(hours=3),
        base_price=Decimal("100.00"),
        status=Event.Status.PUBLISHED,
        created_by=admin_user,
    )


@pytest.fixture
def book(user_client, event):
    """Helper: book seats for the default user and return the response."""

    def _book(seat_ids, client=None, target=None):
        return (client or user_client).post(
            "/api/v1/reservations/",
            {"event": (target or event).pk, "seat_ids": seat_ids},
            format="json",
        )

    return _book
