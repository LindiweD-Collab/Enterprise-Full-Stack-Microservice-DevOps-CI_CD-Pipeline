from drf_spectacular.utils import extend_schema
from rest_framework import generics, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated

from accounts.models import User
from accounts.serializers import (
    AdminUserSerializer,
    RegisterSerializer,
    UserSerializer,
)
from core.exceptions import ConflictError
from core.permissions import IsAdminRole


@extend_schema(tags=["auth"], summary="Register a standard user account")
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes: list = []


@extend_schema(tags=["auth"], summary="View or update your own profile")
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


@extend_schema(tags=["users"])
class UserViewSet(viewsets.ModelViewSet):
    """Administrator-only user management."""

    queryset = User.objects.all().order_by("id")
    serializer_class = AdminUserSerializer
    permission_classes = [IsAdminRole]
    filterset_fields = ["role", "is_active"]
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering_fields = ["id", "username", "date_joined"]

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            raise ConflictError(
                "Administrators cannot delete their own account.",
                code="cannot_delete_self",
            )
        instance.delete()
