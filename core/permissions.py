"""Role-based permission classes (admin vs standard user)."""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def _is_admin(user) -> bool:
    return bool(user and user.is_authenticated and user.is_admin_role)


class IsAdminRole(BasePermission):
    """Only users with the admin role."""

    message = "Administrator role required."

    def has_permission(self, request, view):
        return _is_admin(request.user)


class IsAdminOrReadOnly(BasePermission):
    """Any authenticated user may read; only admins may write."""

    message = "Administrator role required for this operation."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return _is_admin(user)


class IsOwnerOrAdmin(BasePermission):
    """Object-level: the owning user or an admin."""

    message = "You do not have access to this resource."

    def has_object_permission(self, request, view, obj):
        return _is_admin(request.user) or obj.user_id == request.user.id
