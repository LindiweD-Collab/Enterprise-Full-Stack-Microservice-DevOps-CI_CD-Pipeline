from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from tests.conftest import assert_error
from ticketing.models import Event, Reservation

RES = "/api/v1/reservations/"


@pytest.mark.django_db
class TestCreate:
    def test_creates_pending_hold_with_priced_seats(self, book, seats):
        # Row A seat = 100.00, Row B seat (x1.50) = 150.00
        resp = book([seats[0].pk, seats[5].pk])
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert Decimal(body["total_price"]) == Decimal("250.00")
        assert body["expires_at"]
        assert {s["row"] for s in body["seats"]} == {"A", "B"}
        assert body["event_title"] == "Launch Night"

    def test_requires_authentication(self, api_client, event, seats):
        resp = api_client.post(
            RES, {"event": event.pk, "seat_ids": [seats[0].pk]}, format="json"
        )
        assert_error(resp, 401)

    def test_seat_already_taken_returns_409(self, book, seats, other_client):
        assert book([seats[0].pk]).status_code == 201
        resp = book([seats[0].pk, seats[1].pk], client=other_client)
        error = assert_error(resp, 409, "seat_unavailable")
        assert error["details"]["seat_ids"] == [seats[0].pk]
        # all-or-nothing: seat 1 must not have been taken
        assert book([seats[1].pk], client=other_client).status_code == 201

    def test_duplicate_seat_ids_are_collapsed(self, book, seats):
        resp = book([seats[0].pk, seats[0].pk])
        assert resp.status_code == 201
        assert len(resp.json()["seats"]) == 1

    def test_seat_from_other_venue_rejected(self, book, db):
        from ticketing.models import Seat, Venue

        elsewhere = Venue.objects.create(name="Elsewhere", city="X", capacity=5)
        stray = Seat.objects.create(venue=elsewhere, section="S", row="A", number=1)
        error = assert_error(book([stray.pk]), 400, "validation_error")
        assert "seat_ids" in error["details"]

    def test_empty_seat_list_rejected(self, book):
        assert_error(book([]), 400, "validation_error")

    def test_too_many_seats(self, book, seats, settings):
        settings.MAX_SEATS_PER_RESERVATION = 2
        resp = book([s.pk for s in seats[:3]])
        assert_error(resp, 400, "validation_error")

    def test_draft_event_not_bookable(self, book, venue, seats, event):
        draft = Event.objects.create(
            venue=venue,
            title="Draft",
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            base_price=Decimal("10"),
        )
        assert_error(book([seats[0].pk], target=draft), 400, "validation_error")

    def test_event_that_already_started(self, book, seats, event):
        Event.objects.filter(pk=event.pk).update(
            starts_at=timezone.now() - timedelta(hours=1)
        )
        assert_error(book([seats[0].pk]), 409, "event_closed")

    def test_unpublished_status_blocked_in_service(self, user, event, seats):
        from rest_framework.exceptions import ValidationError

        from ticketing import services

        Event.objects.filter(pk=event.pk).update(status=Event.Status.CANCELLED)
        event.refresh_from_db()
        with pytest.raises(ValidationError):
            services.create_reservation(user=user, event=event, seat_ids=[seats[0].pk])

    def test_race_lost_to_unique_index_maps_to_409(
        self, user, other_user, event, seats, monkeypatch
    ):
        """If two requests pass the pre-check, the DB constraint still wins."""
        from core.exceptions import ConflictError
        from ticketing import services

        services.create_reservation(user=user, event=event, seat_ids=[seats[0].pk])
        # Simulate the pre-check missing the other request's claim.
        monkeypatch.setattr(services, "release_expired_holds", lambda event=None: 0)
        real_filter = services.ReservationSeat.objects.filter

        class Blind:
            def values_list(self, *a, **k):
                return []

        def fake_filter(*args, **kwargs):
            if kwargs.get("is_active") is True and "seat_id__in" in kwargs:
                return Blind()
            return real_filter(*args, **kwargs)

        monkeypatch.setattr(services.ReservationSeat.objects, "filter", fake_filter)
        with pytest.raises(ConflictError) as info:
            services.create_reservation(
                user=other_user, event=event, seat_ids=[seats[0].pk]
            )
        assert info.value.detail.code == "seat_unavailable"

    def test_expired_hold_is_released_for_next_booker(self, book, seats, other_client):
        first = book([seats[0].pk]).json()["id"]
        Reservation.objects.filter(pk=first).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        assert book([seats[0].pk], client=other_client).status_code == 201
        assert Reservation.objects.get(pk=first).status == Reservation.Status.EXPIRED


@pytest.mark.django_db
class TestReadAndScope:
    def test_users_only_see_their_own(self, book, seats, other_client):
        mine = book([seats[0].pk]).json()["id"]
        theirs = book([seats[1].pk], client=other_client).json()["id"]
        resp = other_client.get(RES)
        assert [r["id"] for r in resp.json()["results"]] == [theirs]
        assert_error(other_client.get(f"{RES}{mine}/"), 404, "not_found")

    def test_admin_sees_everything(self, book, seats, other_client, admin_client):
        book([seats[0].pk])
        book([seats[1].pk], client=other_client)
        assert admin_client.get(RES).json()["count"] == 2

    def test_filter_by_status(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/cancel/")
        book([seats[1].pk])
        cancelled = user_client.get(RES, {"status": "cancelled"}).json()
        assert cancelled["count"] == 1

    def test_retrieve_includes_seats(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        body = user_client.get(f"{RES}{pk}/").json()
        assert body["seats"][0]["section"] == "Main"


@pytest.mark.django_db
class TestLifecycle:
    def test_confirm(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        resp = user_client.post(f"{RES}{pk}/confirm/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"
        assert resp.json()["expires_at"] is None

    def test_confirm_twice_conflicts(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/confirm/")
        assert_error(user_client.post(f"{RES}{pk}/confirm/"), 409, "invalid_state")

    def test_confirm_after_hold_expired(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        Reservation.objects.filter(pk=pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        assert_error(user_client.post(f"{RES}{pk}/confirm/"), 409, "hold_expired")
        reservation = Reservation.objects.get(pk=pk)
        assert reservation.status == Reservation.Status.EXPIRED
        assert not reservation.items.filter(is_active=True).exists()

    def test_other_user_cannot_confirm(self, book, seats, other_client):
        pk = book([seats[0].pk]).json()["id"]
        assert_error(other_client.post(f"{RES}{pk}/confirm/"), 404)

    def test_admin_can_confirm_for_user(self, book, seats, admin_client):
        pk = book([seats[0].pk]).json()["id"]
        assert admin_client.post(f"{RES}{pk}/confirm/").status_code == 200

    def test_cancel_frees_seat(self, book, seats, user_client, other_client):
        pk = book([seats[0].pk]).json()["id"]
        resp = user_client.post(f"{RES}{pk}/cancel/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        assert book([seats[0].pk], client=other_client).status_code == 201

    def test_cancel_confirmed(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/confirm/")
        assert user_client.post(f"{RES}{pk}/cancel/").status_code == 200

    def test_cancel_twice_conflicts(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/cancel/")
        assert_error(user_client.post(f"{RES}{pk}/cancel/"), 409, "invalid_state")

    def test_cannot_cancel_confirmed_after_start(self, book, seats, user_client, event):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/confirm/")
        Event.objects.filter(pk=event.pk).update(
            starts_at=timezone.now() - timedelta(minutes=5)
        )
        assert_error(user_client.post(f"{RES}{pk}/cancel/"), 409, "event_closed")


@pytest.mark.django_db
class TestUpdateDelete:
    def test_patch_notes(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        resp = user_client.patch(
            f"{RES}{pk}/", {"notes": "Aisle please"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Aisle please"

    def test_put_notes(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        resp = user_client.put(f"{RES}{pk}/", {"notes": "Hello"}, format="json")
        assert resp.status_code == 200

    def test_cannot_edit_cancelled(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/cancel/")
        resp = user_client.patch(f"{RES}{pk}/", {"notes": "x"}, format="json")
        assert_error(resp, 409, "invalid_state")

    def test_status_is_not_writable(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        resp = user_client.patch(f"{RES}{pk}/", {"status": "confirmed"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_standard_user_cannot_delete(self, book, seats, user_client):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/cancel/")
        assert_error(user_client.delete(f"{RES}{pk}/"), 403, "permission_denied")

    def test_admin_cannot_delete_live_reservation(self, book, seats, admin_client):
        pk = book([seats[0].pk]).json()["id"]
        assert_error(admin_client.delete(f"{RES}{pk}/"), 409, "invalid_state")

    def test_admin_deletes_cancelled(self, book, seats, user_client, admin_client):
        pk = book([seats[0].pk]).json()["id"]
        user_client.post(f"{RES}{pk}/cancel/")
        assert admin_client.delete(f"{RES}{pk}/").status_code == 204
        assert not Reservation.objects.filter(pk=pk).exists()
