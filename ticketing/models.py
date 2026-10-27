"""Domain models.

Relationships:

* ``Venue`` 1 ─── * ``Seat``                       (FK, cascade)
* ``Venue`` 1 ─── * ``Event``                      (FK, protect)
* ``User``  1 ─── * ``Reservation`` * ─── 1 ``Event``  (FKs, protect)
* ``Reservation`` * ─── * ``Seat`` via ``ReservationSeat``   (many-to-many)

``ReservationSeat`` carries a *partial unique constraint* on
``(event, seat) WHERE is_active`` which makes double-booking impossible at
the database level, regardless of application races.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Venue(models.Model):
    name = models.CharField(max_length=120)
    city = models.CharField(max_length=80)
    address = models.CharField(max_length=255, blank=True)
    capacity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "city"], name="uniq_venue_name_city"
            ),
            models.CheckConstraint(
                condition=Q(capacity__gt=0), name="venue_capacity_positive"
            ),
        ]
        indexes = [models.Index(fields=["city"], name="venue_city_idx")]

    def __str__(self) -> str:
        return f"{self.name} ({self.city})"


class Seat(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="seats")
    section = models.CharField(max_length=30)
    row = models.CharField(max_length=10)
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    price_multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.10"))],
        help_text="Multiplies the event base price (e.g. 2.50 for VIP).",
    )

    class Meta:
        ordering = ["venue_id", "section", "row", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["venue", "section", "row", "number"], name="uniq_seat_position"
            ),
            models.CheckConstraint(
                condition=Q(number__gt=0), name="seat_number_positive"
            ),
            models.CheckConstraint(
                condition=Q(price_multiplier__gt=0), name="seat_multiplier_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.section}-{self.row}{self.number}"


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"

    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    base_price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")),
                name="event_ends_after_start",
            ),
            models.CheckConstraint(
                condition=Q(base_price__gte=0), name="event_price_non_negative"
            ),
        ]
        indexes = [
            # Public listing: "published events from <date>, soonest first".
            models.Index(fields=["status", "starts_at"], name="event_status_start_idx"),
            # Venue calendar: "what is on at venue X, and when?".
            models.Index(fields=["venue", "starts_at"], name="event_venue_start_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class Reservation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending (seats held)"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reservations"
    )
    event = models.ForeignKey(
        Event, on_delete=models.PROTECT, related_name="reservations"
    )
    seats = models.ManyToManyField(
        Seat, through="ReservationSeat", related_name="reservations"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    expires_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_price__gte=0), name="reservation_total_non_negative"
            ),
        ]
        indexes = [
            # "My reservations", newest first.
            models.Index(fields=["user", "-created_at"], name="resv_user_created_idx"),
            # Per-event dashboards and cascades on event cancellation.
            models.Index(fields=["event", "status"], name="resv_event_status_idx"),
            # Hold-expiry sweep: pending rows whose hold has lapsed.
            models.Index(
                fields=["status", "expires_at"], name="resv_status_expiry_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"Reservation {self.reference} ({self.status})"


class ReservationSeat(models.Model):
    """Join row between a reservation and a seat (many-to-many through).

    ``event`` is deliberately denormalised from the reservation so the
    database can enforce "one live claim per seat per event" with a partial
    unique index. ``price`` snapshots the seat price at booking time.
    """

    reservation = models.ForeignKey(
        Reservation, on_delete=models.CASCADE, related_name="items"
    )
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name="claims")
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="claims")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "seat"],
                condition=Q(is_active=True),
                name="uniq_active_seat_per_event",
            ),
            models.UniqueConstraint(
                fields=["reservation", "seat"], name="uniq_seat_per_reservation"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reservation_id}:{self.seat_id}"
