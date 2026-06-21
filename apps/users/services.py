from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from rest_framework.exceptions import PermissionDenied

from apps.users.constants import ProfileType
from apps.users.manager_permissions import (
    MANAGER_PERMISSION_LEVELS,
    build_manager_permission_code,
    normalize_manager_permissions,
    permission_level_satisfies,
    validate_manager_permissions,
)


class ManagerPermissionService:
    METHOD_LEVEL_MAP = {
        "GET": "list",
        "HEAD": "list",
        "OPTIONS": "list",
        "POST": "edit",
        "PUT": "edit",
        "PATCH": "edit",
        "DELETE": "full_control",
    }

    @staticmethod
    def normalize_permissions(permissions) -> list[str]:
        return normalize_manager_permissions(permissions)

    @staticmethod
    def validate_permissions(permissions) -> list[str]:
        return validate_manager_permissions(permissions)

    @classmethod
    def has_permission(cls, manager_profile, required_permission: str) -> bool:

        normalized_permissions = cls.normalize_permissions(
            getattr(manager_profile, "permissions", [])
        )
        return any(
            permission_level_satisfies(permission, required_permission)
            for permission in normalized_permissions
        )

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
    def get_required_permission(
        cls, module: str, request_method: str
    ) -> str:
        level = cls.METHOD_LEVEL_MAP.get(request_method.upper(), "list")
        if level not in MANAGER_PERMISSION_LEVELS:
            level = "list"
        return build_manager_permission_code(module, level)
