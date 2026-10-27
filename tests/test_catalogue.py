import pytest

from tests.conftest import assert_error
from ticketing.models import Seat, Venue

VENUES = "/api/v1/venues/"
SEATS = "/api/v1/seats/"


@pytest.mark.django_db
class TestVenues:
    def test_requires_authentication(self, api_client):
        assert_error(api_client.get(VENUES), 401)

    def test_standard_user_can_read(self, user_client, venue):
        listing = user_client.get(VENUES)
        assert listing.status_code == 200
        row = listing.json()["results"][0]
        assert row["seat_count"] == 10
        detail = user_client.get(f"{VENUES}{venue.pk}/")
        assert detail.status_code == 200

    def test_standard_user_cannot_write(self, user_client, venue):
        body = {"name": "X", "city": "Y", "capacity": 5}
        assert_error(user_client.post(VENUES, body, format="json"), 403)
        assert_error(
            user_client.patch(f"{VENUES}{venue.pk}/", body, format="json"), 403
        )
        assert_error(user_client.delete(f"{VENUES}{venue.pk}/"), 403)

    def test_admin_crud(self, admin_client):
        created = admin_client.post(
            VENUES,
            {"name": "Moses Mabhida", "city": "Durban", "capacity": 100},
            format="json",
        )
        assert created.status_code == 201
        assert created.json()["seat_count"] == 0
        pk = created.json()["id"]
        put = admin_client.put(
            f"{VENUES}{pk}/",
            {"name": "Moses Mabhida", "city": "Durban", "capacity": 120},
            format="json",
        )
        assert put.status_code == 200 and put.json()["capacity"] == 120
        assert admin_client.delete(f"{VENUES}{pk}/").status_code == 204

    def test_validation(self, admin_client):
        resp = admin_client.post(
            VENUES, {"name": "A", "city": "B", "capacity": 0}, format="json"
        )
        error = assert_error(resp, 400, "validation_error")
        assert "capacity" in error["details"]

    def test_duplicate_name_in_city_rejected(self, admin_client, venue):
        resp = admin_client.post(
            VENUES,
            {"name": venue.name, "city": venue.city, "capacity": 10},
            format="json",
        )
        assert_error(resp, 400, "validation_error")

    def test_capacity_cannot_drop_below_seat_count(self, admin_client, venue):
        resp = admin_client.patch(
            f"{VENUES}{venue.pk}/", {"capacity": 5}, format="json"
        )
        error = assert_error(resp, 400, "validation_error")
        assert "capacity" in error["details"]

    def test_filter_and_search(self, user_client, venue):
        Venue.objects.create(name="Cape Town Stadium", city="Cape Town", capacity=50)
        by_city = user_client.get(VENUES, {"city": "Cape Town"}).json()
        assert by_city["count"] == 1
        searched = user_client.get(VENUES, {"search": "halcyon"}).json()
        assert searched["count"] == 1

    def test_delete_blocked_when_events_exist(self, admin_client, venue, event):
        resp = admin_client.delete(f"{VENUES}{venue.pk}/")
        assert_error(resp, 409, "protected")

    def test_not_found_uses_envelope(self, admin_client):
        assert_error(admin_client.get(f"{VENUES}9999/"), 404, "not_found")


@pytest.mark.django_db
class TestSeats:
    def test_standard_user_read_only(self, user_client, venue):
        assert user_client.get(SEATS, {"venue": venue.pk}).json()["count"] == 10
        resp = user_client.post(
            SEATS,
            {"venue": venue.pk, "section": "S", "row": "Z", "number": 1},
            format="json",
        )
        assert_error(resp, 403)

    def test_admin_crud(self, admin_client, venue):
        created = admin_client.post(
            SEATS,
            {
                "venue": venue.pk,
                "section": "VIP",
                "row": "V",
                "number": 1,
                "price_multiplier": "3.00",
            },
            format="json",
        )
        assert created.status_code == 201
        pk = created.json()["id"]
        patched = admin_client.patch(
            f"{SEATS}{pk}/", {"price_multiplier": "2.50"}, format="json"
        )
        assert patched.status_code == 200
        assert patched.json()["price_multiplier"] == "2.50"
        assert admin_client.get(f"{SEATS}{pk}/").status_code == 200
        assert admin_client.delete(f"{SEATS}{pk}/").status_code == 204

    def test_duplicate_position_rejected(self, admin_client, venue):
        resp = admin_client.post(
            SEATS,
            {"venue": venue.pk, "section": "Main", "row": "A", "number": 1},
            format="json",
        )
        assert_error(resp, 400, "validation_error")

    def test_venue_capacity_enforced(self, admin_client):
        small = Venue.objects.create(name="Tiny", city="Nowhere", capacity=1)
        ok = admin_client.post(
            SEATS,
            {"venue": small.pk, "section": "S", "row": "A", "number": 1},
            format="json",
        )
        assert ok.status_code == 201
        full = admin_client.post(
            SEATS,
            {"venue": small.pk, "section": "S", "row": "A", "number": 2},
            format="json",
        )
        error = assert_error(full, 400, "validation_error")
        assert "venue" in error["details"]

    def test_invalid_seat_number(self, admin_client, venue):
        resp = admin_client.post(
            SEATS,
            {"venue": venue.pk, "section": "S", "row": "Q", "number": 0},
            format="json",
        )
        assert_error(resp, 400, "validation_error")


@pytest.mark.django_db
class TestSeatGeneration:
    def url(self, venue):
        return f"{VENUES}{venue.pk}/seats/generate/"

    def test_generates_block(self, admin_client):
        venue = Venue.objects.create(name="Gen", city="Pretoria", capacity=30)
        resp = admin_client.post(
            self.url(venue),
            {
                "section": "Floor",
                "rows": ["A", "B"],
                "seats_per_row": 10,
                "price_multiplier": "2.00",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json() == {"created": 20}
        assert Seat.objects.filter(venue=venue).count() == 20

    def test_conflict_with_existing_seats(self, admin_client, venue):
        resp = admin_client.post(
            self.url(venue),
            {"section": "Main", "rows": ["A"], "seats_per_row": 2},
            format="json",
        )
        error = assert_error(resp, 409, "seats_exist")
        assert error["details"]["existing"]

    def test_over_capacity(self, admin_client, venue):
        resp = admin_client.post(
            self.url(venue),
            {"section": "Balcony", "rows": ["A", "B"], "seats_per_row": 10},
            format="json",
        )
        assert_error(resp, 400, "validation_error")

    def test_duplicate_rows_rejected(self, admin_client, venue):
        resp = admin_client.post(
            self.url(venue),
            {"section": "X", "rows": ["A", "A"], "seats_per_row": 1},
            format="json",
        )
        assert_error(resp, 400, "validation_error")

    def test_standard_user_forbidden(self, user_client, venue):
        resp = user_client.post(
            self.url(venue),
            {"section": "X", "rows": ["A"], "seats_per_row": 1},
            format="json",
        )
        assert_error(resp, 403)
