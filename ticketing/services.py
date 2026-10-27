"""Reservation business logic, kept out of views so it is easy to test."""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from datetime import timedelta
from rest_framework.exceptions import ValidationError

from core.exceptions import ConflictError
from ticketing.models import Event, Reservation, ReservationSeat, Seat

CENT = Decimal("0.01")
LIVE = (Reservation.Status.PENDING, Reservation.Status.CONFIRMED)


def live_claim_q(prefix: str = "") -> Q:
    """Q matching claims that currently block a seat.

    A claim blocks when its reservation is confirmed, or pending with an
    unexpired hold. Evaluating expiry at read time means availability is
    always correct without needing a background sweeper.
    """
    now = timezone.now()
    p = f"{prefix}reservation__"
    return Q(**{f"{prefix}is_active": True}) & (
        Q(**{f"{p}status": Reservation.Status.CONFIRMED})
        | Q(
            **{
                f"{p}status": Reservation.Status.PENDING,
                f"{p}expires_at__gt": now,
            }
        )
    )


def annotate_event_capacity(queryset):
    """Add ``seats_total`` and ``seats_taken`` to an Event queryset."""
    taken = (
        ReservationSeat.objects.filter(event=OuterRef("pk"))
        .filter(live_claim_q())
        .order_by()
        .values("event")
        .annotate(c=Count("pk"))
        .values("c")
    )
    total = (
        Seat.objects.filter(venue=OuterRef("venue"))
        .order_by()
        .values("venue")
        .annotate(c=Count("pk"))
        .values("c")
    )
    return queryset.annotate(
        seats_total=Coalesce(Subquery(total, output_field=IntegerField()), Value(0)),
        seats_taken=Coalesce(Subquery(taken, output_field=IntegerField()), Value(0)),
    )


def _release(reservation_ids, new_status):
    """Free the seats and set the status for the given reservations."""
    if not reservation_ids:
        return 0
    ReservationSeat.objects.filter(
        reservation_id__in=reservation_ids, is_active=True
    ).update(is_active=False)
    return Reservation.objects.filter(pk__in=reservation_ids).update(
        status=new_status, updated_at=timezone.now()
    )


def release_expired_holds(event=None) -> int:
    """Mark lapsed pending reservations as expired and free their seats."""
    qs = Reservation.objects.filter(
        status=Reservation.Status.PENDING, expires_at__lte=timezone.now()
    )
    if event is not None:
        qs = qs.filter(event=event)
    return _release(list(qs.values_list("pk", flat=True)), Reservation.Status.EXPIRED)


def _price(event: Event, seat: Seat) -> Decimal:
    return (event.base_price * seat.price_multiplier).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def create_reservation(*, user, event: Event, seat_ids, notes: str = "") -> Reservation:
    """Hold the requested seats for ``user`` (all-or-nothing)."""
    if event.status != Event.Status.PUBLISHED:
        raise ValidationError({"event": "This event is not open for booking."})
    if event.starts_at <= timezone.now():
        raise ConflictError("This event has already started.", code="event_closed")

    ids = list(dict.fromkeys(seat_ids))  # de-duplicate, keep order
    seats = list(Seat.objects.filter(pk__in=ids, venue_id=event.venue_id))
    if len(seats) != len(ids):
        raise ValidationError(
            {"seat_ids": "One or more seats do not exist at this event's venue."}
        )

    try:
        with transaction.atomic():
            release_expired_holds(event)
            taken = sorted(
                ReservationSeat.objects.filter(
                    event=event, seat_id__in=ids, is_active=True
                ).values_list("seat_id", flat=True)
            )
            if taken:
                raise _seat_unavailable(taken)

            prices = {seat.pk: _price(event, seat) for seat in seats}
            reservation = Reservation.objects.create(
                user=user,
                event=event,
                total_price=sum(prices.values(), Decimal("0.00")),
                expires_at=timezone.now()
                + timedelta(minutes=settings.RESERVATION_HOLD_MINUTES),
                notes=notes,
            )
            ReservationSeat.objects.bulk_create(
                [
                    ReservationSeat(
                        reservation=reservation,
                        event=event,
                        seat=seat,
                        price=prices[seat.pk],
                    )
                    for seat in seats
                ]
            )
    except IntegrityError:
        # Lost a race: another request claimed a seat between our check and
        # insert. The partial unique index rejected us; report it cleanly.
        raise _seat_unavailable(ids)
    return reservation


def _seat_unavailable(seat_ids) -> ConflictError:
    return ConflictError(
        "One or more requested seats are no longer available.",
        code="seat_unavailable",
        details={"seat_ids": list(seat_ids)},
    )


def confirm_reservation(reservation: Reservation) -> Reservation:
    expired = False
    with transaction.atomic():
        res = Reservation.objects.select_for_update().get(pk=reservation.pk)
        if res.status != Reservation.Status.PENDING:
            raise ConflictError(
                f"Only pending reservations can be confirmed (status: {res.status}).",
                code="invalid_state",
            )
        if res.expires_at and res.expires_at <= timezone.now():
            _release([res.pk], Reservation.Status.EXPIRED)
            expired = True
        else:
            res.status = Reservation.Status.CONFIRMED
            res.expires_at = None
            res.save(update_fields=["status", "expires_at", "updated_at"])
    if expired:
        raise ConflictError(
            "The seat hold expired before confirmation. Please book again.",
            code="hold_expired",
        )
    return res


def cancel_reservation(reservation: Reservation) -> Reservation:
    with transaction.atomic():
        res = (
            Reservation.objects.select_for_update()
            .select_related("event")
            .get(pk=reservation.pk)
        )
        if res.status not in LIVE:
            raise ConflictError(
                f"Reservation is already {res.status}.", code="invalid_state"
            )
        if (
            res.status == Reservation.Status.CONFIRMED
            and res.event.starts_at <= timezone.now()
        ):
            raise ConflictError(
                "Confirmed reservations cannot be cancelled after the event starts.",
                code="event_closed",
            )
        _release([res.pk], Reservation.Status.CANCELLED)
    res.refresh_from_db()
    return res


def cancel_event_reservations(event: Event) -> int:
    """Cancel every live reservation when an event is cancelled."""
    ids = list(
        Reservation.objects.filter(event=event, status__in=LIVE).values_list(
            "pk", flat=True
        )
    )
    return _release(ids, Reservation.Status.CANCELLED)
