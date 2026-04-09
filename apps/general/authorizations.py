from typing import Optional

from rest_framework.permissions import BasePermission

from apps.general.constants import CUSTOMERPERMISSIONS, SELLERPERMISSIONS


class AuthorizationService:
    @staticmethod
    def can_access_app(
        user,
        app_label: str,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        obj: Optional[object] = None,
    ) -> bool:

        if user.role == "Manager":
            return True

        if user.is_superuser:
            return False

        action = action or "list"

        role_permissions_map = {
            "Customer": CUSTOMERPERMISSIONS,
            "Seller": SELLERPERMISSIONS,
        }
        if user.role in role_permissions_map:
            permissions_dict = role_permissions_map[user.role]

            allowed_actions = permissions_dict.get(resource, [])

            if action not in allowed_actions:
                return False

            if obj:
                # Because resources are tied with the profiles not with the users.
                return getattr(obj, "owner_id", None) == user.active_profile.id

            return True

        return False


class HasAppAccess(BasePermission):
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
        return AuthorizationService.can_access_app(
            user=user,
            app_label=obj._meta.app_label,
            action=action,
            resource=resource,
            obj=obj,
        )
