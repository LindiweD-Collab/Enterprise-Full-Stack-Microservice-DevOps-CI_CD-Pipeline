import pytest
from django.contrib.auth import get_user_model

from tests.conftest import PASSWORD, assert_error

User = get_user_model()
REGISTER = "/api/v1/auth/register/"


@pytest.mark.django_db
class TestRegistration:
    payload = {
        "username": "carol",
        "email": "Carol@Example.com",
        "password": PASSWORD,
        "first_name": "Carol",
    }

    def test_register_creates_standard_user(self, api_client):
        resp = api_client.post(REGISTER, self.payload, format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["role"] == "user"
        assert body["email"] == "carol@example.com"
        assert "password" not in body

    def test_cannot_self_assign_admin_role(self, api_client):
        resp = api_client.post(
            REGISTER, {**self.payload, "role": "admin"}, format="json"
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "user"

    def test_duplicate_email_is_case_insensitive(self, api_client, user):
        resp = api_client.post(
            REGISTER,
            {**self.payload, "email": "ALICE@example.com"},
            format="json",
        )
        error = assert_error(resp, 400, "validation_error")
        assert "email" in error["details"]

    def test_weak_password_rejected(self, api_client):
        resp = api_client.post(
            REGISTER, {**self.payload, "password": "12345678"}, format="json"
        )
        error = assert_error(resp, 400, "validation_error")
        assert "password" in error["details"]

    def test_missing_fields(self, api_client):
        error = assert_error(api_client.post(REGISTER, {}, format="json"), 400)
        assert {"username", "email", "password"} <= set(error["details"])


@pytest.mark.django_db
class TestAuthentication:
    def test_token_flow_and_me(self, api_client, user):
        resp = api_client.post(
            "/api/v1/auth/token/",
            {"username": "alice", "password": PASSWORD},
            format="json",
        )
        assert resp.status_code == 200
        tokens = resp.json()
        assert {"access", "refresh"} <= set(tokens)

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        me = api_client.get("/api/v1/auth/me/")
        assert me.status_code == 200
        assert me.json()["username"] == "alice"

        refreshed = api_client.post(
            "/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        assert refreshed.status_code == 200
        assert "access" in refreshed.json()

    def test_wrong_password(self, api_client, user):
        resp = api_client.post(
            "/api/v1/auth/token/",
            {"username": "alice", "password": "nope"},
            format="json",
        )
        assert_error(resp, 401, "no_active_account")

    def test_bad_refresh_token(self, api_client):
        resp = api_client.post(
            "/api/v1/auth/token/refresh/", {"refresh": "garbage"}, format="json"
        )
        assert_error(resp, 401, "token_not_valid")

    def test_missing_token_gives_401_envelope(self, api_client):
        assert_error(api_client.get("/api/v1/auth/me/"), 401, "not_authenticated")

    def test_invalid_bearer_token(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        assert_error(api_client.get("/api/v1/auth/me/"), 401, "token_not_valid")


@pytest.mark.django_db
class TestProfile:
    def test_update_own_profile_but_not_role(self, user_client):
        resp = user_client.patch(
            "/api/v1/auth/me/",
            {"first_name": "Alicia", "role": "admin"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["first_name"] == "Alicia"
        assert resp.json()["role"] == "user"

    def test_change_email_to_taken_one(self, user_client, other_user):
        resp = user_client.patch(
            "/api/v1/auth/me/", {"email": "bob@example.com"}, format="json"
        )
        assert_error(resp, 400, "validation_error")


@pytest.mark.django_db
class TestUserAdministration:
    url = "/api/v1/users/"

    def test_standard_user_forbidden(self, user_client):
        assert_error(user_client.get(self.url), 403, "permission_denied")

    def test_anonymous_unauthorised(self, api_client):
        assert_error(api_client.get(self.url), 401)

    def test_admin_lists_and_filters(self, admin_client, user):
        resp = admin_client.get(self.url, {"role": "user"})
        assert resp.status_code == 200
        assert [u["username"] for u in resp.json()["results"]] == ["alice"]

    def test_admin_crud(self, admin_client):
        created = admin_client.post(
            self.url,
            {
                "username": "dave",
                "email": "dave@example.com",
                "password": PASSWORD,
                "role": "admin",
            },
            format="json",
        )
        assert created.status_code == 201
        pk = created.json()["id"]
        assert created.json()["role"] == "admin"
        assert User.objects.get(pk=pk).check_password(PASSWORD)

        assert admin_client.get(f"{self.url}{pk}/").status_code == 200

        patched = admin_client.patch(
            f"{self.url}{pk}/",
            {"first_name": "Dave", "password": "An0ther-Pass#2026"},
            format="json",
        )
        assert patched.status_code == 200
        assert User.objects.get(pk=pk).check_password("An0ther-Pass#2026")

        assert admin_client.delete(f"{self.url}{pk}/").status_code == 204

    def test_admin_create_requires_password(self, admin_client):
        resp = admin_client.post(
            self.url, {"username": "eve", "email": "eve@example.com"}, format="json"
        )
        error = assert_error(resp, 400, "validation_error")
        assert "password" in error["details"]

    def test_admin_create_rejects_weak_password(self, admin_client):
        resp = admin_client.post(
            self.url,
            {"username": "eve", "email": "eve@example.com", "password": "short"},
            format="json",
        )
        assert_error(resp, 400, "validation_error")

    def test_admin_cannot_delete_self(self, admin_client, admin_user):
        resp = admin_client.delete(f"{self.url}{admin_user.pk}/")
        assert_error(resp, 409, "cannot_delete_self")

    def test_deleting_user_with_reservations_is_conflict(
        self, admin_client, user, book, seats
    ):
        assert book([seats[0].pk]).status_code == 201
        resp = admin_client.delete(f"{self.url}{user.pk}/")
        assert_error(resp, 409, "protected")


@pytest.mark.django_db
class TestUserModel:
    def test_superuser_is_admin_role(self):
        su = User.objects.create_superuser("root", "ROOT@example.com", PASSWORD)
        assert su.role == User.Role.ADMIN
        assert su.is_admin_role
        assert su.email == "root@example.com"

    def test_standard_user_is_not_admin(self, user):
        assert not user.is_admin_role
        assert str(user) == "alice"
