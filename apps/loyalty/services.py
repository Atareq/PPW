from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from apps.authentication.models import UserAccount
from apps.users.models import Profile
from rest_framework.exceptions import ValidationError

from apps.loyalty.constants import (
    BADGE_DEFINITIONS,
    BadgeType,
    PointTransactionType,
)
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.loyalty.validators import BadgeValidator, PointValidator


def normalize_optional_string(value):
    if value is None:
        return ""
    return str(value)


def normalize_idempotency_key(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


class PointService:
    """Ledger-style point wallet mutations with optional idempotency keys."""

    @staticmethod
    def get_or_create_wallet(user):
        user = UserAccount.objects.get(pk=user.pk)
        try:
            wallet, _ = PointWallet.objects.get_or_create(user=user)
        except IntegrityError:
            wallet = PointWallet.objects.get(user=user)
        return wallet

    @staticmethod
    def get_balance(user):
        return PointService.get_or_create_wallet(user).balance

    @staticmethod
    def earn_points(
        user,
        profile,
        points,
        reason,
        source_type=None,
        source_id=None,
        idempotency_key=None,
        metadata=None,
    ):
        return PointService._create_transaction(
            user=user,
            profile=profile,
            transaction_type=PointTransactionType.EARN,
            points=points,
            reason=reason,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    @staticmethod
    def spend_points(
        user,
        profile,
        points,
        reason,
        source_type=None,
        source_id=None,
        idempotency_key=None,
        metadata=None,
    ):
        return PointService._create_transaction(
            user=user,
            profile=profile,
            transaction_type=PointTransactionType.SPEND,
            points=points,
            reason=reason,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    @staticmethod
    def expire_points(
        user,
        profile,
        points,
        reason,
        source_type=None,
        source_id=None,
        idempotency_key=None,
        metadata=None,
    ):
        return PointService._create_transaction(
            user=user,
            profile=profile,
            transaction_type=PointTransactionType.EXPIRE,
            points=points,
            reason=reason,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    @staticmethod
    @transaction.atomic
    def _create_transaction(
        *,
        user,
        profile,
        transaction_type,
        points,
        reason,
        source_type=None,
        source_id=None,
        idempotency_key=None,
        metadata=None,
    ):
        """Create one transaction and update the wallet balance atomically."""

        user = UserAccount.objects.get(pk=user.pk)
        profile = PointValidator.validate_profile_belongs_to_user(
            profile,
            user,
        )
        points = PointValidator.validate_points(points)
        reason = PointValidator.validate_reason(reason)
        metadata = metadata or {}
        source_type = normalize_optional_string(source_type)
        source_id = normalize_optional_string(source_id)
        idempotency_key = normalize_idempotency_key(idempotency_key)

        existing_transaction = PointService._get_idempotent_transaction(
            idempotency_key
        )
        if existing_transaction is not None:
            PointService._validate_idempotent_match(
                existing_transaction,
                user=user,
                profile=profile,
                transaction_type=transaction_type,
                points=points,
                reason=reason,
                source_type=source_type,
                source_id=source_id,
                metadata=metadata,
            )
            return existing_transaction

        wallet = PointService.get_or_create_wallet(user)
        wallet = PointWallet.objects.select_for_update().get(pk=wallet.pk)
        next_balance = PointService._next_balance(
            wallet.balance,
            transaction_type,
            points,
        )

        wallet.balance = next_balance
        wallet.save(update_fields=["balance", "modified"])

        return PointTransaction.objects.create(
            wallet=wallet,
            user=user,
            profile=profile,
            transaction_type=transaction_type,
            points=points,
            balance_after=next_balance,
            reason=reason,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    @staticmethod
    def _next_balance(current_balance, transaction_type, points):
        if transaction_type == PointTransactionType.EARN:
            return current_balance + points

        if current_balance < points:
            raise ValidationError(
                {"points": "Insufficient point balance for this action."}
            )

        if transaction_type in {
            PointTransactionType.SPEND,
            PointTransactionType.EXPIRE,
        }:
            return current_balance - points

        raise ValidationError({"transaction_type": "Unsupported type."})

    @staticmethod
    def _get_idempotent_transaction(idempotency_key):
        if idempotency_key is None:
            return None

        return (
            PointTransaction.objects.select_related(
                "wallet",
                "user",
                "profile",
            )
            .filter(idempotency_key=idempotency_key)
            .first()
        )

    @staticmethod
    def _validate_idempotent_match(
        transaction,
        *,
        user,
        profile,
        transaction_type,
        points,
        reason,
        source_type,
        source_id,
        metadata,
    ):
        """Reject key reuse when the requested transaction payload differs."""

        profile_id = profile.pk if profile else None
        matches = (
            transaction.user_id == user.pk
            and transaction.profile_id == profile_id
            and transaction.transaction_type == transaction_type
            and transaction.points == points
            and transaction.reason == reason
            and transaction.source_type == source_type
            and transaction.source_id == source_id
            and transaction.metadata == metadata
        )
        if not matches:
            raise ValidationError(
                {
                    "idempotency_key": (
                        "Idempotency key was already used for a different "
                        "transaction."
                    )
                }
            )


class BadgeService:
    """Badge catalog and explicit manager-award helpers."""

    @staticmethod
    def get_badge_catalog():
        return [
            dict(BADGE_DEFINITIONS[badge_type])
            for badge_type in BadgeType.values
        ]

    @staticmethod
    @transaction.atomic
    def award_badge(
        profile,
        badge_type,
        source_type=None,
        source_id=None,
        idempotency_key=None,
        expires_at=None,
        metadata=None,
        one_per_profile=True,
    ):
        """Award a badge idempotently, preserving manager one-per-profile behavior."""

        profile = Profile.objects.get(pk=profile.pk)
        BadgeValidator.validate_badge_eligibility(profile, badge_type)
        idempotency_key = normalize_idempotency_key(idempotency_key)
        created_new = False

        if idempotency_key is not None:
            existing = BadgeAward.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing is not None:
                return existing

        if one_per_profile:
            existing = (
                BadgeAward.objects.filter(
                    profile=profile,
                    badge_type=badge_type,
                    deleted__isnull=True,
                )
                .order_by("awarded_at", "created")
                .first()
            )
            if existing is not None:
                return existing

        defaults = {
            "source_type": normalize_optional_string(source_type),
            "source_id": normalize_optional_string(source_id),
            "idempotency_key": idempotency_key,
            "expires_at": expires_at,
            "metadata": metadata or {},
        }
        try:
            award = BadgeAward.objects.create(
                profile=profile,
                badge_type=badge_type,
                **defaults,
            )
            created_new = True
        except IntegrityError:
            if idempotency_key is not None:
                award = BadgeAward.objects.get(
                    idempotency_key=idempotency_key,
                )
            else:
                award = BadgeAward.objects.filter(
                    profile=profile,
                    badge_type=badge_type,
                    deleted__isnull=True,
                ).first()
                if award is None:
                    raise
        if created_new:
            BadgeService._schedule_badge_awarded_notification(award)
        return award

    @staticmethod
    def get_earned_badges(profile, *, include_expired=True):
        profile = Profile.objects.get(pk=profile.pk)
        queryset = (
            BadgeAward.objects.select_related("profile", "profile__user")
            .filter(profile=profile)
            .order_by("-awarded_at", "-created")
        )
        if not include_expired:
            queryset = queryset.filter(
                revoked_at__isnull=True,
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            )
        return queryset

    @staticmethod
    def get_active_badges(profile):
        return BadgeService.get_earned_badges(
            profile,
            include_expired=False,
        )

    @staticmethod
    def evaluate_badges(profile):
        Profile.objects.get(pk=profile.pk)
        return []

    @staticmethod
    def _schedule_badge_awarded_notification(award):
        from apps.notifications.services import NotificationService

        badge_name = BADGE_DEFINITIONS[award.badge_type]["name"]
        transaction.on_commit(
            lambda: NotificationService.create_notification(
                recipient_profile=award.profile,
                notification_type="badge_awarded",
                title="Badge awarded",
                body=f"You earned the {badge_name} badge.",
                object_type="badge_award",
                object_id=award.pk,
                idempotency_key=f"badge_awarded:{award.pk}",
            )
        )
