from typing import Optional

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.general.constants import CUSTOMER_PERMISSIONS, SELLER_PERMISSIONS
from apps.users.constants import ProfileType
from apps.users.services import ManagerPermissionService

MANAGER_RESOURCE_MODULE_MAP = {
    "BIRD": "assets",
    "PRODUCT_ASSET": "assets",
    "AUCTION": "auctions",
    "ORDER": "orders",
    "CART": "orders",
    "CHECKOUT": "orders",
    "ORDER_ITEM": "orders",
    "PAYMENT_REQUEST": "payments",
    "COMPLAINT": "complaints",
    "COMPLAINT_ADMIN": "complaints",
    "PACKAGE": "packages",
    "MY_SELLER_PACKAGE": "packages",
    "SELLER_PACKAGE": "packages",
    "DISCOUNT_OFFER": "promotions",
    "CASHBACK_OFFER": "promotions",
    "DISCOUNT_OFFER_ADMIN": "promotions",
    "CASHBACK_OFFER_ADMIN": "promotions",
    "CASHBACK": "promotions",
    "CASHBACK_TRANSACTION_ADMIN": "promotions",
    "CASHBACK_ADJUSTMENT_ADMIN": "promotions",
    "RACE": "races",
    "RACE_RESULT": "races",
    "PEDIGREE_DOCUMENT": "pedigrees",
    "FEED_BLOCK": "feed",
    "CHAT": "chat",
    "ASSET_COMMENT": "ratings",
    "SELLER_COMMENT": "ratings",
    "ASSET_RATING": "ratings",
    "SELLER_RATING": "ratings",
    "NOTIFICATION": "notifications",
}


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
    ) -> bool:

        if user.is_superuser:
            return True

        role = user.role

        if role == ProfileType.MANAGER:
            module = MANAGER_RESOURCE_MODULE_MAP.get(resource)
            if module is None or request_method is None:
                return False
            required_permission = (
                ManagerPermissionService.get_required_permission(
                    module=module,
                    request_method=request_method,
                )
            )
            return ManagerPermissionService.has_permission(
                user.active_profile,
                required_permission,
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

        action = getattr(view, "action", None) or request.method.lower()
        resource = getattr(view, "permission_resource", None)
        queryset = getattr(view, "queryset", None)
        app_label = queryset.model._meta.app_label if queryset is not None else ""

        return AuthorizationService.can_access_app(
            user=user,
            app_label=app_label,
            action=action,
            resource=resource,
            request_method=request.method,
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        action = getattr(view, "action", None) or request.method.lower()
        resource = getattr(view, "permission_resource", None)

        can_do_action = AuthorizationService.can_access_app(
            user=user,
            app_label=obj._meta.app_label,
            action=action,
            resource=resource,
            request_method=request.method,
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
            request_method=request.method,
            obj=obj,
            owner_field=getattr(view, "permission_owner_field", "owner_id"),
        )
