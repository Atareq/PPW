from typing import Optional

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.general.constants import CUSTOMER_PERMISSIONS, SELLER_PERMISSIONS
from apps.users.constants import ProfileType, StaffRole, STAFF_ROLE_PERMISSIONS


class StaffPermissions:
    """Pure helper — checks whether a staff_role may perform action on resource."""

    @staticmethod
    def can_access(staff_role: str, resource: Optional[str], action: Optional[str]) -> bool:
        role_rules = STAFF_ROLE_PERMISSIONS.get(staff_role)

        if role_rules is None:
            return False

        if role_rules == "__all__":
            # Admin blocks destroy; Super Admin allows everything
            if staff_role == StaffRole.ADMIN and action == "destroy":
                return False
            return True

        # Fine-grained resource/action dict
        if resource is None:
            return False

        allowed_actions = role_rules.get(resource)
        if allowed_actions is None:
            return False

        return (action or "list") in allowed_actions


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
            from apps.users.models import ManagerProfile
            try:
                active = user.active_profile
            except Exception:
                return False

            manager_profile = ManagerProfile.objects.filter(
                pk=active.pk,
                deleted__isnull=True,
            ).first()

            if not manager_profile:
                return False

            return StaffPermissions.can_access(
                staff_role=manager_profile.staff_role,
                resource=resource,
                action=action,
            )

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


class HasStaffRole(BasePermission):
    """
    Requires an authenticated Manager profile with a role listed in
    view.required_staff_roles (writes) or view.read_staff_roles (SAFE_METHODS).
    If read_staff_roles is not set, required_staff_roles is used for reads too.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        try:
            role = user.role
        except Exception:
            return False

        if role != ProfileType.MANAGER:
            return False

        from apps.users.models import ManagerProfile
        try:
            active = user.active_profile
        except Exception:
            return False

        manager_profile = ManagerProfile.objects.filter(
            pk=active.pk,
            deleted__isnull=True,
        ).first()

        if not manager_profile:
            return False

        if request.method in SAFE_METHODS:
            allowed = getattr(view, "read_staff_roles", None) or getattr(view, "required_staff_roles", [])
        else:
            allowed = getattr(view, "required_staff_roles", [])

        return manager_profile.staff_role in allowed
