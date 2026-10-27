from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from ticketing.models import Event, Reservation, ReservationSeat, Seat, Venue


class VenueSerializer(serializers.ModelSerializer):
    seat_count = serializers.SerializerMethodField()

    class Meta:
        model = Venue
        fields = [
            "id",
            "name",
            "city",
            "address",
            "capacity",
            "seat_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_seat_count(self, obj) -> int:
        annotated = getattr(obj, "seat_count", None)
        return annotated if annotated is not None else obj.seats.count()

    def validate_capacity(self, value):
        if self.instance is not None:
            existing = self.instance.seats.count()
            if value < existing:
                raise serializers.ValidationError(
                    f"Capacity cannot be lower than the {existing} seats already defined."
                )
        return value


class SeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Seat
        fields = ["id", "venue", "section", "row", "number", "price_multiplier"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        venue = attrs.get("venue") or (self.instance.venue if self.instance else None)
        if self.instance is None and venue is not None:
            if venue.seats.count() >= venue.capacity:
                raise serializers.ValidationError(
                    {"venue": f"Venue is at capacity ({venue.capacity} seats)."}
                )
        return attrs


class SeatGenerateSerializer(serializers.Serializer):
    """Bulk-create a rectangular block of seats in one venue section."""

    section = serializers.CharField(max_length=30)
    rows = serializers.ListField(
        child=serializers.CharField(max_length=10),
        min_length=1,
        max_length=52,
        help_text='Row labels, e.g. ["A", "B", "C"].',
    )
    seats_per_row = serializers.IntegerField(min_value=1, max_value=200)
    start_number = serializers.IntegerField(min_value=1, default=1)
    price_multiplier = serializers.DecimalField(
        max_digits=4,
        decimal_places=2,
        min_value=Decimal("0.10"),
        default=Decimal("1.00"),
    )

    def validate_rows(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Row labels must be unique.")
        return value


class GeneratedSeatsSerializer(serializers.Serializer):
    created = serializers.IntegerField()


class EventSerializer(serializers.ModelSerializer):
    venue_name = serializers.CharField(source="venue.name", read_only=True)
    seats_total = serializers.SerializerMethodField()
    seats_available = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id",
            "venue",
            "venue_name",
            "title",
            "description",
            "starts_at",
            "ends_at",
            "base_price",
            "status",
            "seats_total",
            "seats_available",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def get_seats_total(self, obj) -> int:
        return getattr(obj, "seats_total", obj.venue.seats.count())

    def get_seats_available(self, obj) -> int:
        total = self.get_seats_total(obj)
        taken = getattr(obj, "seats_taken", 0)
        return max(total - taken, 0)

    def validate(self, attrs):
        instance = self.instance
        starts = attrs.get("starts_at", instance.starts_at if instance else None)
        ends = attrs.get("ends_at", instance.ends_at if instance else None)
        if starts and ends and ends <= starts:
            raise serializers.ValidationError(
                {"ends_at": "Must be later than starts_at."}
            )
        if instance is None and starts and starts <= timezone.now():
            raise serializers.ValidationError(
                {"starts_at": "New events must start in the future."}
            )
        return attrs


class EventSeatSerializer(serializers.ModelSerializer):
    """A seat plus whether it can currently be booked for the event."""

    is_available = serializers.BooleanField(read_only=True)

    class Meta:
        model = Seat
        fields = ["id", "section", "row", "number", "price_multiplier", "is_available"]


class ReservationItemSerializer(serializers.ModelSerializer):
    section = serializers.CharField(source="seat.section", read_only=True)
    row = serializers.CharField(source="seat.row", read_only=True)
    number = serializers.IntegerField(source="seat.number", read_only=True)

    class Meta:
        model = ReservationSeat
        fields = ["seat", "section", "row", "number", "price"]


class ReservationSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source="event.title", read_only=True)
    seats = ReservationItemSerializer(source="items", many=True, read_only=True)

    class Meta:
        model = Reservation
        fields = [
            "id",
            "reference",
            "event",
            "event_title",
            "user",
            "status",
            "total_price",
            "expires_at",
            "notes",
            "seats",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ReservationCreateSerializer(serializers.Serializer):
    event = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.filter(status=Event.Status.PUBLISHED)
    )
    seat_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False
    )
    notes = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_seat_ids(self, value):
        unique = len(set(value))
        if unique > settings.MAX_SEATS_PER_RESERVATION:
            raise serializers.ValidationError(
                f"At most {settings.MAX_SEATS_PER_RESERVATION} seats per reservation."
            )
        return value


class ReservationUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reservation
        fields = ["notes"]
