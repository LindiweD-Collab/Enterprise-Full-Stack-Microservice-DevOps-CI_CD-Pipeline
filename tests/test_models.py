from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from ticketing import services
from ticketing.models import Event, Reservation, ReservationSeat, Seat, Venue


def make_reservation(user, event, status=Reservation.Status.PENDING):
    return Reservation.objects.create(
        user=user,
        event=event,
        total_price=Decimal("0"),
        status=status,
        expires_at=timezone.now() + timedelta(minutes=5),
    )


@pytest.mark.django_db
class TestConstraints:
    def test_one_active_claim_per_seat_per_event(self, user, other_user, event, seats):
        first = make_reservation(user, event)
        second = make_reservation(other_user, event)
        ReservationSeat.objects.create(
            reservation=first, event=event, seat=seats[0], price=Decimal("1")
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            ReservationSeat.objects.create(
                reservation=second, event=event, seat=seats[0], price=Decimal("1")
            )

    def test_inactive_claims_do_not_block(self, user, other_user, event, seats):
        first = make_reservation(user, event)
        second = make_reservation(other_user, event)
        ReservationSeat.objects.create(
            reservation=first,
            event=event,
            seat=seats[0],
            price=Decimal("1"),
            is_active=False,
        )
        ReservationSeat.objects.create(
            reservation=second, event=event, seat=seats[0], price=Decimal("1")
        )

    def test_same_seat_can_be_claimed_for_different_events(
        self, user, event, seats, venue
    ):
        other_event = Event.objects.create(
            venue=venue,
            title="Second",
            starts_at=event.starts_at + timedelta(days=1),
            ends_at=event.ends_at + timedelta(days=1),
            base_price=Decimal("10"),
        )
        for ev in (event, other_event):
            ReservationSeat.objects.create(
                reservation=make_reservation(user, ev),
                event=ev,
                seat=seats[0],
                price=Decimal("1"),
            )

    def test_event_end_must_follow_start(self, venue):
        now = timezone.now()
        with pytest.raises(IntegrityError), transaction.atomic():
            Event.objects.create(
                venue=venue,
                title="Bad",
                starts_at=now,
                ends_at=now - timedelta(hours=1),
                base_price=Decimal("1"),
            )

    def test_negative_price_blocked_by_database(self, venue):
        now = timezone.now()
        with pytest.raises(IntegrityError), transaction.atomic():
            Event.objects.create(
                venue=venue,
                title="Bad",
                starts_at=now,
                ends_at=now + timedelta(hours=1),
                base_price=Decimal("-1"),
            )

    def test_seat_position_unique(self, venue):
        with pytest.raises(IntegrityError), transaction.atomic():
            Seat.objects.create(venue=venue, section="Main", row="A", number=1)

    def test_venue_name_unique_per_city(self, venue):
        with pytest.raises(IntegrityError), transaction.atomic():
            Venue.objects.create(name=venue.name, city=venue.city, capacity=1)


@pytest.mark.django_db
class TestStrings:
    def test_str_methods(self, venue, event, seats, user):
        reservation = make_reservation(user, event)
        item = ReservationSeat.objects.create(
            reservation=reservation, event=event, seat=seats[0], price=Decimal("1")
        )
        assert "Halcyon Arena" in str(venue)
        assert str(seats[0]) == "Main-A1"
        assert str(event) == "Launch Night"
        assert "pending" in str(reservation)
        assert str(item) == f"{reservation.pk}:{seats[0].pk}"


@pytest.mark.django_db
class TestServices:
    def test_release_expired_holds_counts(self, user, event, seats):
        stale = make_reservation(user, event)
        Reservation.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        ReservationSeat.objects.create(
            reservation=stale, event=event, seat=seats[0], price=Decimal("1")
        )
        fresh = make_reservation(user, event)
        ReservationSeat.objects.create(
            reservation=fresh, event=event, seat=seats[1], price=Decimal("1")
        )
        assert services.release_expired_holds() == 1
        assert services.release_expired_holds(event) == 0
        stale.refresh_from_db()
        fresh.refresh_from_db()
        assert stale.status == Reservation.Status.EXPIRED
        assert fresh.status == Reservation.Status.PENDING

    def test_release_with_nothing_to_do(self, db):
        assert services.release_expired_holds() == 0

    def test_price_rounding(self, event, seats):
        seats[0].price_multiplier = Decimal("1.33")
        event.base_price = Decimal("10.05")
        assert services._price(event, seats[0]) == Decimal("13.37")
