from typing import Optional

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.general.constants import CUSTOMER_PERMISSIONS, SELLER_PERMISSIONS
from apps.users.constants import ProfileType


class AuthorizationService:
    """Central role/action permission matrix used by HasAppAccess."""

    @staticmethod
    def can_access_app(
        user,
        app_label: str,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        obj: Optional[object] = None,
        owner_field: str = "owner_id",
    ) -> bool:

        role = user.role

        if role == ProfileType.MANAGER:
            return True

        if user.is_superuser:
            return False

        action = action or "list"

        role_permissions_map = {
            ProfileType.CUSTOMER: CUSTOMER_PERMISSIONS,
            ProfileType.SELLER: SELLER_PERMISSIONS,
        }
        if role in role_permissions_map:
            permissions_dict = role_permissions_map[role]

            allowed_actions = permissions_dict.get(resource, [])

            if action not in allowed_actions:
                return False

            if obj:
                # Ownership checks are profile-scoped, not account-scoped.
                return (
                    getattr(obj, owner_field, None) == user.active_profile.id
                )

            return True

        return False


class HasAppAccess(BasePermission):
    """DRF permission that maps view actions to profile-level resources."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        action = getattr(view, "action", None)
        resource = getattr(view, "permission_resource", None)
        app_label = view.queryset.model._meta.app_label

        return AuthorizationService.can_access_app(
            user=user,
            app_label=app_label,
            action=action,
            resource=resource,
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        action = getattr(view, "action", None)
        resource = getattr(view, "permission_resource", None)

        can_do_action = AuthorizationService.can_access_app(
            user=user,
            app_label=obj._meta.app_label,
            action=action,
            resource=resource,
        )
        if not can_do_action:
            return False

        if (
            getattr(view, "allow_scoped_read_access", False)
            and request.method in SAFE_METHODS
        ):
            return True

        return AuthorizationService.can_access_app(
            user=user,
            app_label=obj._meta.app_label,
            action=action,
            resource=resource,
            obj=obj,
            owner_field=getattr(view, "permission_owner_field", "owner_id"),
        )
