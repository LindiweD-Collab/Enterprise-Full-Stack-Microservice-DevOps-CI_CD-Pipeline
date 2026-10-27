from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.exceptions import ConflictError
from core.permissions import IsAdminOrReadOnly, IsAdminRole, IsOwnerOrAdmin
from ticketing import services
from ticketing.filters import EventFilter
from ticketing.models import Event, Reservation, ReservationSeat, Seat, Venue
from ticketing.serializers import (
    EventSerializer,
    GeneratedSeatsSerializer,
    EventSeatSerializer,
    ReservationCreateSerializer,
    ReservationSerializer,
    ReservationUpdateSerializer,
    SeatGenerateSerializer,
    SeatSerializer,
    VenueSerializer,
)


class SeatMapPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


@extend_schema_view(
    list=extend_schema(summary="List venues"),
    create=extend_schema(summary="Create a venue (admin)"),
    update=extend_schema(summary="Replace a venue (admin)"),
    partial_update=extend_schema(summary="Update a venue (admin)"),
    destroy=extend_schema(summary="Delete a venue (admin)"),
)
@extend_schema(tags=["venues"])
class VenueViewSet(viewsets.ModelViewSet):
    serializer_class = VenueSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_fields = ["city"]
    search_fields = ["name", "city", "address"]
    ordering_fields = ["name", "city", "capacity", "created_at"]

    def get_queryset(self):
        return Venue.objects.annotate(seat_count=Count("seats")).order_by("name", "id")

    @extend_schema(
        summary="Bulk-create a block of seats (admin)",
        request=SeatGenerateSerializer,
        responses={201: GeneratedSeatsSerializer},
    )
    @action(detail=True, methods=["post"], url_path="seats/generate")
    def generate_seats(self, request, pk=None):
        venue = self.get_object()
        serializer = SeatGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        wanted = [
            (data["section"], row, number)
            for row in data["rows"]
            for number in range(
                data["start_number"], data["start_number"] + data["seats_per_row"]
            )
        ]
        existing = set(
            venue.seats.filter(section=data["section"]).values_list(
                "section", "row", "number"
            )
        )
        clashes = sorted(set(wanted) & existing)
        if clashes:
            raise ConflictError(
                "Some of these seats already exist.",
                code="seats_exist",
                details={"existing": [f"{s}-{r}{n}" for s, r, n in clashes[:20]]},
            )
        if venue.seats.count() + len(wanted) > venue.capacity:
            raise ValidationError(
                {
                    "seats_per_row": (
                        f"Would exceed venue capacity of {venue.capacity} seats."
                    )
                }
            )
        Seat.objects.bulk_create(
            [
                Seat(
                    venue=venue,
                    section=s,
                    row=r,
                    number=n,
                    price_multiplier=data["price_multiplier"],
                )
                for s, r, n in wanted
            ]
        )
        return Response({"created": len(wanted)}, status=status.HTTP_201_CREATED)


@extend_schema(tags=["seats"])
class SeatViewSet(viewsets.ModelViewSet):
    queryset = Seat.objects.all().order_by("venue_id", "section", "row", "number")
    serializer_class = SeatSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_fields = ["venue", "section", "row"]
    ordering_fields = ["section", "row", "number"]


@extend_schema(tags=["events"])
class EventViewSet(viewsets.ModelViewSet):
    """Standard users only ever see published events; admins see everything."""

    serializer_class = EventSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_class = EventFilter
    search_fields = ["title", "description", "venue__name", "venue__city"]
    ordering_fields = ["starts_at", "base_price", "title", "created_at"]

    def get_queryset(self):
        qs = Event.objects.select_related("venue")
        user = getattr(self.request, "user", None)
        if not (user and user.is_authenticated and user.is_admin_role):
            qs = qs.filter(status=Event.Status.PUBLISHED)
        return services.annotate_event_capacity(qs).order_by("starts_at", "id")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        with transaction.atomic():
            event = serializer.save()
            if event.status == Event.Status.CANCELLED:
                services.cancel_event_reservations(event)

    @extend_schema(
        summary="Seat map with live availability",
        parameters=[OpenApiParameter("section", str, description="Filter by section")],
        responses=EventSeatSerializer(many=True),
    )
    @action(
        detail=True,
        methods=["get"],
        pagination_class=SeatMapPagination,
        filter_backends=[],
    )
    def seats(self, request, pk=None):
        event = self.get_object()
        taken = ReservationSeat.objects.filter(event=event, seat=OuterRef("pk")).filter(
            services.live_claim_q()
        )
        qs = Seat.objects.filter(venue_id=event.venue_id).annotate(
            is_available=~Exists(taken)
        )
        section = request.query_params.get("section")
        if section:
            qs = qs.filter(section=section)
        qs = qs.order_by("section", "row", "number")
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(
            EventSeatSerializer(page, many=True, context={"request": request}).data
        )


@extend_schema(tags=["reservations"])
class ReservationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Standard users manage their own reservations; admins see all."""

    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    filterset_fields = ["status", "event"]
    ordering_fields = ["created_at", "total_price"]

    def get_permissions(self):
        if self.action == "destroy":
            return [IsAdminRole()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Reservation.objects.none()
        qs = Reservation.objects.select_related("event", "user").prefetch_related(
            Prefetch("items", queryset=ReservationSeat.objects.select_related("seat"))
        )
        user = self.request.user
        if not user.is_admin_role:
            qs = qs.filter(user=user)
        return qs.order_by("-created_at", "-id")

    def get_serializer_class(self):
        if self.action == "create":
            return ReservationCreateSerializer
        if self.action in {"update", "partial_update"}:
            return ReservationUpdateSerializer
        return ReservationSerializer

    def _fresh(self, reservation):
        instance = self.get_queryset().get(pk=reservation.pk)
        return ReservationSerializer(instance, context=self.get_serializer_context())

    @extend_schema(
        summary="Hold seats for an event",
        request=ReservationCreateSerializer,
        responses={201: ReservationSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reservation = services.create_reservation(
            user=request.user,
            event=data["event"],
            seat_ids=data["seat_ids"],
            notes=data.get("notes", ""),
        )
        return Response(self._fresh(reservation).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Update reservation notes",
        request=ReservationUpdateSerializer,
        responses={200: ReservationSerializer},
    )
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status not in services.LIVE:
            raise ConflictError(
                f"Reservation is {instance.status} and can no longer be edited.",
                code="invalid_state",
            )
        serializer = self.get_serializer(
            instance, data=request.data, partial=kwargs.pop("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self._fresh(instance).data)

    @extend_schema(
        summary="Hard-delete a cancelled or expired reservation (admin)",
        responses={204: None},
    )
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status in services.LIVE:
            raise ConflictError(
                "Cancel the reservation before deleting it.", code="invalid_state"
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Confirm a pending reservation",
        request=None,
        responses={200: ReservationSerializer},
    )
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        reservation = services.confirm_reservation(self.get_object())
        return Response(self._fresh(reservation).data)

    @extend_schema(
        summary="Cancel a reservation and release its seats",
        request=None,
        responses={200: ReservationSerializer},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        reservation = services.cancel_reservation(self.get_object())
        return Response(self._fresh(reservation).data)
