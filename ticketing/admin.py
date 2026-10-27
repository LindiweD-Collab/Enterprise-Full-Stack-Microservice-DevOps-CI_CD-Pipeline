from django.contrib import admin

from ticketing.models import Event, Reservation, ReservationSeat, Seat, Venue


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "capacity")
    search_fields = ("name", "city")


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("venue", "section", "row", "number", "price_multiplier")
    list_filter = ("venue", "section")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "venue", "starts_at", "status", "base_price")
    list_filter = ("status", "venue")
    search_fields = ("title",)


class ReservationSeatInline(admin.TabularInline):
    model = ReservationSeat
    extra = 0


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "event", "status", "total_price")
    list_filter = ("status",)
    inlines = [ReservationSeatInline]
