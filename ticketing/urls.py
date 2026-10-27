from rest_framework.routers import SimpleRouter

from ticketing.views import EventViewSet, ReservationViewSet, SeatViewSet, VenueViewSet

router = SimpleRouter()
router.register("venues", VenueViewSet, basename="venue")
router.register("seats", SeatViewSet, basename="seat")
router.register("events", EventViewSet, basename="event")
router.register("reservations", ReservationViewSet, basename="reservation")

urlpatterns = router.urls
