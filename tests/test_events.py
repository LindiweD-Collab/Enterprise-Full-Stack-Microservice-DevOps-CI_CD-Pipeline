from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from tests.conftest import assert_error
from ticketing.models import Event, Reservation

EVENTS = "/api/v1/events/"


def payload(venue, **overrides):
    start = timezone.now() + timedelta(days=10)
    body = {
        "venue": venue.pk,
        "title": "Jazz Night",
        "description": "Live jazz",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
        "base_price": "250.00",
        "status": "published",
    }
    body.update(overrides)
    return body


@pytest.mark.django_db
class TestEventVisibility:
    def test_requires_authentication(self, api_client):
        assert_error(api_client.get(EVENTS), 401)

    def test_standard_user_sees_only_published(self, user_client, venue, event):
        Event.objects.create(
            venue=venue,
            title="Secret Draft",
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            base_price=Decimal("10"),
        )
        results = user_client.get(EVENTS).json()["results"]
        assert [e["title"] for e in results] == ["Launch Night"]

    def test_admin_sees_drafts(self, admin_client, venue, event):
        Event.objects.create(
            venue=venue,
            title="Secret Draft",
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            base_price=Decimal("10"),
        )
        assert admin_client.get(EVENTS).json()["count"] == 2

    def test_draft_detail_hidden_from_standard_user(self, user_client, venue, event):
        draft = Event.objects.create(
            venue=venue,
            title="Secret Draft",
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            base_price=Decimal("10"),
        )
        assert_error(user_client.get(f"{EVENTS}{draft.pk}/"), 404)

    def test_capacity_fields(self, user_client, event, seats, book):
        before = user_client.get(f"{EVENTS}{event.pk}/").json()
        assert before["seats_total"] == 10
        assert before["seats_available"] == 10
        assert before["venue_name"] == "Halcyon Arena"
        book([seats[0].pk, seats[1].pk])
        after = user_client.get(f"{EVENTS}{event.pk}/").json()
        assert after["seats_available"] == 8

    def test_filters_search_ordering(self, user_client, venue, event):
        later = event.starts_at + timedelta(days=5)
        Event.objects.create(
            venue=venue,
            title="Rock Festival",
            starts_at=later,
            ends_at=later + timedelta(hours=4),
            base_price=Decimal("500"),
            status=Event.Status.PUBLISHED,
        )
        assert user_client.get(EVENTS, {"search": "rock"}).json()["count"] == 1
        assert user_client.get(EVENTS, {"city": "johannesburg"}).json()["count"] == 2
        assert user_client.get(EVENTS, {"city": "Durban"}).json()["count"] == 0
        cutoff = (event.starts_at + timedelta(days=1)).isoformat()
        assert user_client.get(EVENTS, {"starts_after": cutoff}).json()["count"] == 1
        assert user_client.get(EVENTS, {"starts_before": cutoff}).json()["count"] == 1
        ordered = user_client.get(EVENTS, {"ordering": "-base_price"}).json()
        assert ordered["results"][0]["title"] == "Rock Festival"


@pytest.mark.django_db
class TestEventWrites:
    def test_standard_user_cannot_write(self, user_client, venue, event):
        assert_error(user_client.post(EVENTS, payload(venue), format="json"), 403)
        assert_error(
            user_client.patch(f"{EVENTS}{event.pk}/", {"title": "x"}, format="json"),
            403,
        )
        assert_error(user_client.delete(f"{EVENTS}{event.pk}/"), 403)

    def test_admin_crud(self, admin_client, admin_user, venue):
        created = admin_client.post(EVENTS, payload(venue), format="json")
        assert created.status_code == 201
        body = created.json()
        assert body["created_by"] == admin_user.pk
        pk = body["id"]
        patched = admin_client.patch(
            f"{EVENTS}{pk}/", {"title": "Jazz Night II"}, format="json"
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Jazz Night II"
        assert admin_client.delete(f"{EVENTS}{pk}/").status_code == 204

    def test_ends_before_start_rejected(self, admin_client, venue):
        start = timezone.now() + timedelta(days=3)
        resp = admin_client.post(
            EVENTS,
            payload(
                venue,
                starts_at=start.isoformat(),
                ends_at=(start - timedelta(hours=1)).isoformat(),
            ),
            format="json",
        )
        error = assert_error(resp, 400, "validation_error")
        assert "ends_at" in error["details"]

    def test_new_event_must_start_in_future(self, admin_client, venue):
        past = timezone.now() - timedelta(days=1)
        resp = admin_client.post(
            EVENTS,
            payload(
                venue,
                starts_at=past.isoformat(),
                ends_at=(past + timedelta(hours=1)).isoformat(),
            ),
            format="json",
        )
        error = assert_error(resp, 400, "validation_error")
        assert "starts_at" in error["details"]

    def test_negative_price_rejected(self, admin_client, venue):
        resp = admin_client.post(
            EVENTS, payload(venue, base_price="-5.00"), format="json"
        )
        error = assert_error(resp, 400, "validation_error")
        assert "base_price" in error["details"]

    def test_unknown_venue_rejected(self, admin_client, venue):
        resp = admin_client.post(
            EVENTS, {**payload(venue), "venue": 9999}, format="json"
        )
        assert_error(resp, 400, "validation_error")

    def test_patch_dates_validated_against_instance(self, admin_client, event):
        resp = admin_client.patch(
            f"{EVENTS}{event.pk}/",
            {"ends_at": (event.starts_at - timedelta(hours=1)).isoformat()},
            format="json",
        )
        assert_error(resp, 400, "validation_error")

    def test_delete_blocked_with_reservations(self, admin_client, event, seats, book):
        book([seats[0].pk])
        assert_error(admin_client.delete(f"{EVENTS}{event.pk}/"), 409, "protected")

    def test_cancelling_event_cancels_reservations(
        self, admin_client, event, seats, book
    ):
        pk = book([seats[0].pk]).json()["id"]
        resp = admin_client.patch(
            f"{EVENTS}{event.pk}/", {"status": "cancelled"}, format="json"
        )
        assert resp.status_code == 200
        reservation = Reservation.objects.get(pk=pk)
        assert reservation.status == Reservation.Status.CANCELLED
        assert not reservation.items.filter(is_active=True).exists()


@pytest.mark.django_db
class TestSeatMap:
    def test_availability_flags(self, user_client, event, seats, book):
        book([seats[0].pk])
        resp = user_client.get(f"{EVENTS}{event.pk}/seats/")
        assert resp.status_code == 200
        results = {s["id"]: s["is_available"] for s in resp.json()["results"]}
        assert len(results) == 10
        assert results[seats[0].pk] is False
        assert results[seats[1].pk] is True

    def test_section_filter(self, user_client, event):
        resp = user_client.get(f"{EVENTS}{event.pk}/seats/", {"section": "Nope"})
        assert resp.json()["count"] == 0

    def test_expired_hold_shows_as_available(self, user_client, event, seats, book):
        pk = book([seats[0].pk]).json()["id"]
        Reservation.objects.filter(pk=pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        results = user_client.get(f"{EVENTS}{event.pk}/seats/").json()["results"]
        assert all(s["is_available"] for s in results)
        detail = user_client.get(f"{EVENTS}{event.pk}/").json()
        assert detail["seats_available"] == 10
