from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

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
    SellerRoom,
    SellerRoomFollow,
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
SENSITIVE_PROFILE_PAYMENT_FIELDS = {
    "bank_account",
    "wallet",
    "transfer_phone",
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
            if field in SENSITIVE_PROFILE_PAYMENT_FIELDS:
                continue
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
    """Shared account-level first-profile onboarding boundary (BE-PRO-03A)."""

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


class SellerRoomService:
    """Owner-scoped CRUD and public query helpers for SellerRoom (BE-ROOM-01)."""

    @staticmethod
    def require_active_seller(user) -> SellerProfile:
        if user is None or not getattr(user, "is_authenticated", False):
            raise PermissionDenied(_("Authentication credentials were not provided."))
        try:
            active_profile = user.active_profile
        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc)) from exc
        if active_profile.type != ProfileType.SELLER:
            raise PermissionDenied(_("Only seller profiles can manage seller rooms."))
        return SellerProfile.objects.get(pk=active_profile.pk)

    @staticmethod
    def rooms_for_owner(seller_profile_or_id):
        seller_id = getattr(seller_profile_or_id, "pk", seller_profile_or_id)
        return (
            SellerRoom.objects.filter(
                seller_id=seller_id,
                deleted__isnull=True,
            )
            .select_related("seller")
            .order_by("-created", "-id")
        )

    @staticmethod
    def public_rooms_for_seller(seller_profile_or_id):
        seller_id = getattr(seller_profile_or_id, "pk", seller_profile_or_id)
        return (
            SellerRoom.objects.filter(
                seller_id=seller_id,
                deleted__isnull=True,
                seller__deleted__isnull=True,
                seller__is_active=True,
                seller__activated=True,
                seller__is_valid=True,
            )
            .select_related("seller")
            .order_by("-created", "-id")
        )

    @classmethod
    @transaction.atomic
    def create_room(cls, *, user, validated_data: dict) -> SellerRoom:
        seller_profile = cls.require_active_seller(user)
        room = SellerRoom(
            seller=seller_profile,
            name=validated_data.get("name", ""),
            description=validated_data.get("description") or "",
        )
        try:
            room.save()
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            ) from exc
        return room

    @classmethod
    @transaction.atomic
    def update_room(cls, *, room: SellerRoom, user, validated_data: dict) -> SellerRoom:
        seller_profile = cls.require_active_seller(user)
        locked_room = (
            SellerRoom.objects.select_for_update()
            .select_related("seller")
            .filter(pk=room.pk, deleted__isnull=True)
            .first()
        )
        if locked_room is None:
            raise NotFound(_("Seller room was not found."))
        if locked_room.seller_id != seller_profile.pk:
            raise PermissionDenied(_("Sellers can only update their own rooms."))

        for field in ("name", "description"):
            if field in validated_data:
                setattr(locked_room, field, validated_data[field])
        try:
            locked_room.save()
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            ) from exc
        return locked_room

    @classmethod
    @transaction.atomic
    def delete_room(cls, *, room: SellerRoom, user) -> None:
        seller_profile = cls.require_active_seller(user)
        locked_room = (
            SellerRoom.objects.select_for_update()
            .filter(pk=room.pk, deleted__isnull=True)
            .first()
        )
        if locked_room is None:
            raise NotFound(_("Seller room was not found."))
        if locked_room.seller_id != seller_profile.pk:
            raise PermissionDenied(_("Sellers can only delete their own rooms."))
        locked_room.delete()


class SellerRoomFollowService:
    """UserAccount-scoped follow/unfollow service for SellerRoom (BE-ROOM-01)."""

    @staticmethod
    def _require_non_manager_user(user):
        if user is None or not getattr(user, "is_authenticated", False):
            raise PermissionDenied(_("Authentication credentials were not provided."))
        if getattr(user, "role", None) == ProfileType.MANAGER or user.profiles.filter(
            type=ProfileType.MANAGER,
            deleted__isnull=True,
        ).exists():
            raise PermissionDenied(_("Manager accounts cannot follow seller rooms."))
        return user

    @staticmethod
    def _get_followable_room(room_id: int, *, actor_user=None) -> SellerRoom:
        room = (
            SellerRoom.objects.select_related("seller")
            .filter(
                pk=room_id,
                deleted__isnull=True,
                seller__deleted__isnull=True,
                seller__activated=True,
                seller__is_valid=True,
            )
            .first()
        )
        if room is None:
            raise NotFound(_("Seller room was not found."))
        if actor_user is not None and room.seller.user_id == actor_user.pk:
            raise ValidationError(
                {"room": [_("Users cannot follow their own seller room.")]}
            )
        if not room.seller.is_active:
            raise NotFound(_("Seller room was not found."))
        return room

    @classmethod
    @transaction.atomic
    def follow(cls, *, user, room_id: int) -> SellerRoomFollow:
        actor_user = cls._require_non_manager_user(user)
        room = cls._get_followable_room(room_id, actor_user=actor_user)

        from apps.feed.models import SellerCustomerBlock

        if SellerCustomerBlock.objects.filter(
            customer__user_id=actor_user.pk,
            seller_id=room.seller_id,
            is_active=True,
            deleted__isnull=True,
        ).exists():
            raise ValidationError(
                {"room": [_("Cannot follow this seller room while a block is active.")]}
            )

        existing = (
            SellerRoomFollow.all_objects.select_for_update()
            .select_related("room", "room__seller")
            .filter(room=room, user=actor_user)
            .first()
        )
        if existing is not None:
            if existing.deleted is not None:
                existing.undelete()
            return existing

        try:
            with transaction.atomic():
                return SellerRoomFollow.objects.create(room=room, user=actor_user)
        except IntegrityError:
            existing = SellerRoomFollow.all_objects.select_related(
                "room", "room__seller"
            ).get(room=room, user=actor_user)
            if existing.deleted is not None:
                existing.undelete()
            return existing

    @classmethod
    @transaction.atomic
    def unfollow(cls, *, user, room_id: int) -> SellerRoom:
        actor_user = cls._require_non_manager_user(user)
        room = (
            SellerRoom.objects.select_related("seller")
            .filter(pk=room_id, deleted__isnull=True)
            .first()
        )
        if room is None:
            raise NotFound(_("Seller room was not found."))

        follows = SellerRoomFollow.objects.select_for_update().filter(
            room=room,
            user=actor_user,
            deleted__isnull=True,
        )
        for follow in follows:
            follow.delete()
        return room

    @staticmethod
    def is_following(*, user, room) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        room_id = getattr(room, "pk", room)
        return SellerRoomFollow.objects.filter(
            room_id=room_id,
            user_id=user.pk,
            deleted__isnull=True,
        ).exists()

