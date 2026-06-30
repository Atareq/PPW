from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.loyalty.constants import RewardSourceType
from apps.users.constants import ProfileType
from apps.users.manager_permissions import (
    get_manager_permission_codes,
    is_manager_permission_allowed,
    normalize_manager_permissions,
    validate_manager_permissions,
)
from apps.users.models import (
    CustomerProfile,
    ManagerProfile,
    Profile,
    ProfileBalanceTransaction,
    ProfileBalanceTransactionType,
    SellerProfile,
)


MONEY_QUANT = Decimal("0.01")


def quantize_money(value):
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


class ProfileBalanceService:
    """Ledger-backed normal balance adjustments for customer/seller profiles."""

    @staticmethod
    @transaction.atomic
    def manager_adjust(*, profile, amount, manager_profile, note, idempotency_key):
        """Lock the concrete profile, mutate balance, and write one audit row."""

        if manager_profile.type != ProfileType.MANAGER:
            raise PermissionDenied(_("Only manager profiles can adjust balances."))
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise ValidationError({"idempotency_key": _("This field is required.")})

        amount = quantize_money(amount)
        if amount == 0:
            raise ValidationError({"amount": _("Adjustment amount cannot be zero.")})

        existing = ProfileBalanceTransaction.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing

        base_profile = Profile.objects.select_for_update().get(pk=profile.pk)
        if base_profile.type == ProfileType.CUSTOMER:
            concrete_profile = CustomerProfile.objects.select_for_update().get(
                pk=base_profile.pk
            )
        elif base_profile.type == ProfileType.SELLER:
            concrete_profile = SellerProfile.objects.select_for_update().get(
                pk=base_profile.pk
            )
        else:
            raise ValidationError(
                {"profile_id": _("Only customer and seller balances are adjustable.")}
            )

        next_balance = quantize_money(concrete_profile.balance + amount)
        if next_balance < 0:
            raise ValidationError(
                {"amount": _("Adjustment would make profile balance negative.")}
            )

        concrete_profile.balance = next_balance
        concrete_profile.save(update_fields=["balance", "modified"])
        return ProfileBalanceTransaction.objects.create(
            profile=base_profile,
            transaction_type=ProfileBalanceTransactionType.MANAGER_ADJUSTMENT,
            amount=amount,
            balance_after=next_balance,
            adjusted_by=ManagerProfile.objects.get(pk=manager_profile.pk),
            note=note or "",
            source_type=RewardSourceType.MANAGER_ADJUSTMENT,
            idempotency_key=idempotency_key,
            metadata={},
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

    @staticmethod
    def get_effective_permissions(user) -> set[str]:
        if user.is_superuser:
            return get_manager_permission_codes()

        try:
            active_profile = user.active_profile
        except DjangoPermissionDenied:
            return set()

        if active_profile.type != ProfileType.MANAGER:
            return set()

        assigned_permissions = getattr(active_profile, "permissions", []) or []
        if not isinstance(assigned_permissions, list):
            return set()
        return {
            permission
            for permission in assigned_permissions
            if is_manager_permission_allowed(permission)
        }

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
            raise PermissionDenied(_("Authentication credentials were not provided."))

        if user.is_superuser:
            return True

        try:
            active_profile = user.active_profile
        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc)) from exc

        if active_profile.type != ProfileType.MANAGER:
            raise PermissionDenied(
                _("Only an active manager profile can use manager permissions.")
            )

        if not cls.has_permission(active_profile, required_permission):
            raise PermissionDenied(
                _("Missing required manager permission: %(permission)s")
                % {"permission": required_permission}
            )

        return True

    @classmethod
    def get_required_permission(cls, namespace: str, action: str) -> str | None:
        normalized_action = cls.DEFAULT_ACTION_MAP.get(action, action)
        permission_code = f"{namespace}.{normalized_action}"
        if not is_manager_permission_allowed(permission_code):
            return None
        return permission_code
