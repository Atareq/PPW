from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from rest_framework.exceptions import PermissionDenied

from apps.users.constants import ProfileType
from apps.users.manager_permissions import (
    is_manager_permission_allowed,
    normalize_manager_permissions,
    validate_manager_permissions,
)


class ManagerPermissionService:
    DEFAULT_ACTION_MAP = {
        "list": "list",
        "retrieve": "retrieve",
        "create": "create",
        "update": "update",
        "partial_update": "update",
        "destroy": "delete",
    }

    @staticmethod
    def normalize_permissions(permissions) -> list[str]:
        return normalize_manager_permissions(permissions)

    @staticmethod
    def validate_permissions(permissions) -> list[str]:
        return validate_manager_permissions(permissions)

    @classmethod
    def has_permission(cls, manager_profile, required_permission: str) -> bool:
        if not is_manager_permission_allowed(required_permission):
            return False

        assigned_permissions = getattr(manager_profile, "permissions", []) or []
        if not isinstance(assigned_permissions, list):
            return False

        allowed_assigned_permissions = {
            permission
            for permission in assigned_permissions
            if is_manager_permission_allowed(permission)
        }
        return required_permission in allowed_assigned_permissions

    @classmethod
    def require_permission(cls, user, required_permission: str) -> bool:
        if not user or not getattr(user, "is_authenticated", False):
            raise PermissionDenied("Authentication credentials were not provided.")

        if user.is_superuser:
            return True

        try:
            active_profile = user.active_profile
        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc)) from exc

        if active_profile.type != ProfileType.MANAGER:
            raise PermissionDenied(
                "Only an active manager profile can use manager permissions."
            )

        if not cls.has_permission(active_profile, required_permission):
            raise PermissionDenied(
                f"Missing required manager permission: {required_permission}"
            )

        return True

    @classmethod
    def get_required_permission(cls, namespace: str, action: str) -> str | None:
        normalized_action = cls.DEFAULT_ACTION_MAP.get(action, action)
        permission_code = f"{namespace}.{normalized_action}"
        if not is_manager_permission_allowed(permission_code):
            return None
        return permission_code
