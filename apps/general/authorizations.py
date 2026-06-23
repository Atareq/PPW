from typing import Optional

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.general.constants import CUSTOMER_PERMISSIONS, SELLER_PERMISSIONS
from apps.users.constants import ProfileType
from apps.users.manager_permissions import is_manager_permission_allowed
from apps.users.services import ManagerPermissionService


class AuthorizationService:
    """Central role/action permission matrix used by HasAppAccess."""

    @staticmethod
    def can_access_app(
        user,
        app_label: str,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        request_method: Optional[str] = None,
        obj: Optional[object] = None,
        owner_field: str = "owner_id",
        required_manager_permission: Optional[str] = None,
    ) -> bool:

        if user.is_superuser:
            return True

        role = user.role

        if role == ProfileType.MANAGER:
            if not is_manager_permission_allowed(required_manager_permission):
                return False
            return ManagerPermissionService.has_permission(
                user.active_profile,
                required_manager_permission,
            )

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
                return getattr(obj, owner_field, None) == user.active_profile.id

            return True

        return False


class HasAppAccess(BasePermission):
    """DRF permission that maps view actions to profile-level resources."""

    def _get_action(self, request, view) -> str:
        return getattr(view, "action", None) or request.method.lower()

    def _get_required_manager_permission(self, request, view) -> str | None:
        resolver = getattr(view, "get_required_manager_permission", None)
        if callable(resolver):
            resolved_permission = resolver()
            if resolved_permission:
                return resolved_permission

        action = self._get_action(request, view)
        permission_map = getattr(view, "manager_permission_map", {}) or {}
        mapped_permission = permission_map.get(action) or permission_map.get(
            request.method.lower()
        )
        if mapped_permission:
            return mapped_permission

        namespace = getattr(view, "manager_permission_namespace", None)
        if namespace:
            return ManagerPermissionService.get_required_permission(
                namespace=namespace,
                action=action,
            )

        return None

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        action = self._get_action(request, view)
        resource = getattr(view, "permission_resource", None)
        queryset = getattr(view, "queryset", None)
        app_label = queryset.model._meta.app_label if queryset is not None else ""

        return AuthorizationService.can_access_app(
            user=user,
            app_label=app_label,
            action=action,
            resource=resource,
            request_method=request.method,
            required_manager_permission=self._get_required_manager_permission(
                request,
                view,
            ),
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        action = self._get_action(request, view)
        resource = getattr(view, "permission_resource", None)
        required_manager_permission = self._get_required_manager_permission(
            request,
            view,
        )

        can_do_action = AuthorizationService.can_access_app(
            user=user,
            app_label=obj._meta.app_label,
            action=action,
            resource=resource,
            request_method=request.method,
            required_manager_permission=required_manager_permission,
        )
        if not can_do_action:
            return False

        if user.role == ProfileType.MANAGER:
            return True

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
            request_method=request.method,
            obj=obj,
            owner_field=getattr(view, "permission_owner_field", "owner_id"),
            required_manager_permission=required_manager_permission,
        )
