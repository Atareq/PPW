from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import transaction
from django.utils import timezone
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
    ProfileDataChangeLog,
    ProfileDataChangeSource,
    SellerProfile,
)


MONEY_QUANT = Decimal("0.01")
PROFILE_SELF_UPDATE_COOLDOWN_DAYS = 60
LOCKED_USER_IDENTITY_FIELDS = {
    "username",
    "phone_number",
    "secondary_phone_number",
    "email",
    "first_name",
    "last_name",
}
LOCKED_PROFILE_FIELDS = {
    "nickname",
    "country",
    "currency",
    "national_id",
    "address",
    "loft_name",
    "loft_picture",
    "recoded_voice",
}


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


class ProfileDataChangeService:
    """Audit profile/identity edits and enforce the self-update cooldown."""

    @classmethod
    def prepare_change(
        cls,
        *,
        profile,
        profile_data,
        user_data,
        source,
        actor_user=None,
        actor_profile=None,
    ):
        profile_changes = cls._changed_values(profile, profile_data)
        user_changes = cls._changed_values(profile.user, user_data)
        changed_fields = sorted(
            list(profile_changes["old"].keys()) + list(user_changes["old"].keys())
        )
        locked_profile_changed = bool(
            set(profile_changes["old"]) & LOCKED_PROFILE_FIELDS
        )
        locked_user_changed = bool(
            set(user_changes["old"]) & LOCKED_USER_IDENTITY_FIELDS
        )

        if source == ProfileDataChangeSource.SELF_UPDATE:
            cls._validate_self_update_cooldown(
                profile,
                locked_profile_changed=locked_profile_changed,
                locked_user_changed=locked_user_changed,
            )

        return {
            "changed_fields": changed_fields,
            "old_values": {
                **profile_changes["old"],
                **user_changes["old"],
            },
            "new_values": {
                **profile_changes["new"],
                **user_changes["new"],
            },
            "locked_profile_changed": locked_profile_changed,
            "locked_user_changed": locked_user_changed,
            "source": source,
            "actor_user": actor_user,
            "actor_profile": actor_profile,
        }

    @classmethod
    def commit_change(cls, *, profile, prepared_change):
        if not prepared_change["changed_fields"]:
            return None

        now = timezone.now()
        update_profile_fields = []
        update_user_fields = []
        actor_profile = prepared_change["actor_profile"]

        if prepared_change["locked_profile_changed"]:
            profile.profile_data_last_changed_at = now
            profile.profile_data_last_changed_by = actor_profile
            update_profile_fields.extend(
                [
                    "profile_data_last_changed_at",
                    "profile_data_last_changed_by",
                    "modified",
                ]
            )

        if prepared_change["locked_user_changed"]:
            profile.user.identity_data_last_changed_at = now
            profile.user.identity_data_last_changed_by_profile = actor_profile
            update_user_fields.extend(
                [
                    "identity_data_last_changed_at",
                    "identity_data_last_changed_by_profile",
                    "modified",
                ]
            )

        if update_profile_fields:
            profile.save(update_fields=update_profile_fields)
        if update_user_fields:
            profile.user.save(update_fields=update_user_fields)

        return ProfileDataChangeLog.objects.create(
            user=profile.user,
            profile=profile,
            changed_by_profile=actor_profile,
            changed_by_user=prepared_change["actor_user"],
            actor_role=getattr(actor_profile, "type", "") if actor_profile else "",
            changed_fields=prepared_change["changed_fields"],
            old_values=prepared_change["old_values"],
            new_values=prepared_change["new_values"],
            source=prepared_change["source"],
        )

    @classmethod
    def _validate_self_update_cooldown(
        cls,
        profile,
        *,
        locked_profile_changed,
        locked_user_changed,
    ):
        cutoff = timezone.now() - timedelta(days=PROFILE_SELF_UPDATE_COOLDOWN_DAYS)
        if (
            locked_profile_changed
            and profile.profile_data_last_changed_at
            and profile.profile_data_last_changed_at > cutoff
        ):
            raise ValidationError(
                _("Profile data can only be updated once every 60 days.")
            )
        if (
            locked_user_changed
            and profile.user.identity_data_last_changed_at
            and profile.user.identity_data_last_changed_at > cutoff
        ):
            raise ValidationError(
                _("Profile data can only be updated once every 60 days.")
            )

    @classmethod
    def _changed_values(cls, instance, submitted_data):
        old_values = {}
        new_values = {}
        for field, value in submitted_data.items():
            old_value = getattr(instance, field)
            if cls._normalize_value(old_value) == cls._normalize_value(value):
                continue
            old_values[field] = cls._serialize_value(old_value)
            new_values[field] = cls._serialize_value(value)
        return {"old": old_values, "new": new_values}

    @staticmethod
    def _normalize_value(value):
        return "" if value is None else str(value)

    @staticmethod
    def _serialize_value(value):
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return str(value)
        return str(value)


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


class AccountOnboardingService:
    """Shared account-level first-profile onboarding boundary (BE-PRO-03A / BE-DRW-01)."""

    @staticmethod
    def has_prior_customer_seller_profile_history(
        user,
        *,
        current_profile=None,
    ) -> bool:
        """Return True unless `user` has at most one historical Customer/Seller profile (live, non-deleted).

        Evaluates `Profile.all_objects` (including soft-deleted rows) so that:
        - Fresh customer-first or seller-first onboarding has no prior history (`False`).
        - Creating a second mode later (`Customer -> Seller` or `Seller -> Customer`) has prior history (`True`).
        - Recreating a profile after soft-deleting a prior profile has prior history (`True`).
        """

        user_id = getattr(user, "pk", user)
        current_profile_id = (
            getattr(current_profile, "pk", current_profile)
            if current_profile is not None
            else None
        )
        historical_profiles = list(
            Profile.all_objects.filter(
                user_id=user_id,
                type__in=(ProfileType.CUSTOMER, ProfileType.SELLER),
            )
            .order_by("created", "pk")
            .values("pk", "deleted")
        )
        if len(historical_profiles) != 1:
            return True
        only_profile = historical_profiles[0]
        if only_profile["deleted"] is not None:
            return True
        if (
            current_profile_id is not None
            and only_profile["pk"] != int(current_profile_id)
        ):
            return True
        return False
