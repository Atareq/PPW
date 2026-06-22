from rest_framework.permissions import BasePermission

from apps.users.constants import ProfileType


class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and getattr(user, "is_superuser", False)
        )


class IsNotManagerProfile(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_superuser", False):
            return True
        return user.active_profile.type != ProfileType.MANAGER
