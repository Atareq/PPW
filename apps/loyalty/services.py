from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from apps.authentication.models import UserAccount
from apps.users.constants import ProfileType
from apps.users.models import Profile
from rest_framework.exceptions import ValidationError

from apps.loyalty.constants import (
    BADGE_DEFINITIONS,
    BUYER_LEVEL_TABLE,
    DEFAULT_REWARD_RULES,
    LEVEL_BENEFIT_BLOCKER_CODE,
    LEVEL_METRIC_CURRENCY_BLOCKER_CODE,
    REWARD_RULE_TUNABLE_FIELDS,
    SELLER_LEVEL_TABLE,
    BadgeType,
    PermanentLevelSpec,
    PointTransactionType,
    RewardRuleCode,
    RewardSourceType,
)

from apps.loyalty.models import (
    BadgeAward,
    PointTransaction,
    PointWallet,
    RewardRuleConfig,
)
from apps.loyalty.validators import BadgeValidator, PointValidator


class Level:
    def __init__(self, code, label):
        self.code = code
        self.label = label


LEVEL_THRESHOLDS = [
    (10000, Level("legendary", _("Legendary"))),
    (5000, Level("elite", _("Elite"))),
    (1000, Level("golden", _("Golden"))),
    (500, Level("reliable", _("Reliable"))),
    (100, Level("verified", _("Verified"))),
]
BASIC_LEVEL = Level("basic", _("Basic"))


def normalize_optional_string(value):
    if value is None:
        return ""
    return str(value)


def normalize_idempotency_key(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _resolve_reward_recipient_profile(user, profile=None):
    if (
        profile is not None
        and profile.is_active
        and profile.activated
        and profile.is_valid
        and getattr(profile, "deleted", None) is None
    ):
        return profile
    if user is not None:
        active = Profile.objects.filter(
            user=user,
            is_active=True,
            activated=True,
            is_valid=True,
            deleted__isnull=True,
        ).first()
        if active is not None:
            return active
    return profile


class PointService:
    """Ledger-style point wallet mutations with optional idempotency keys."""

    @staticmethod
    def get_or_create_wallet(user):
        user_id = getattr(user, "pk", user)
        user = UserAccount.objects.get(pk=user_id)
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

        user_id = getattr(user, "pk", user)
        user = UserAccount.objects.get(pk=user_id)
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

        existing_transaction = PointService._get_idempotent_transaction(idempotency_key)
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
        existing_transaction = PointService._get_idempotent_transaction(idempotency_key)
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
        next_balance = PointService._next_balance(
            wallet.balance,
            transaction_type,
            points,
        )

        wallet.balance = next_balance
        wallet.save(update_fields=["balance", "modified"])

        point_tx = PointTransaction.objects.create(
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
        if transaction_type == PointTransactionType.EARN and point_tx.points > 0:
            PointService._create_points_earned_notification(point_tx)
        return point_tx

    @staticmethod
    def _build_points_earned_body(points: int, source_type: str | None) -> str:
        normalized_source = str(source_type or "").strip()
        if normalized_source == RewardSourceType.ONBOARDING:
            return f"Congratulations! You earned {points} PP Coins for joining."
        if normalized_source in {
            RewardSourceType.REFERRAL_APPROVED,
            RewardSourceType.REFERRAL_ACCEPTED,
            RewardSourceType.REFERRAL_FIRST_BID,
        }:
            return (
                f"Congratulations! You earned {points} PP Coins from a referral."
            )
        if normalized_source in {
            RewardSourceType.DEAL_COMPLETED,
            RewardSourceType.DEAL_COMPLETION_BONUS,
            RewardSourceType.SUCCESSFUL_SALE,
            RewardSourceType.AUCTION_COMPLETED,
            RewardSourceType.ORDER_COMPLETED_AMOUNT,
            RewardSourceType.ORDER_COMPLETED_BONUS,
        }:
            return (
                f"Congratulations! You earned {points} PP Coins for a completed deal."
            )
        if normalized_source == RewardSourceType.SEASON_WINNER:
            return (
                f"Congratulations! You earned {points} PP Coins as a season winner."
            )
        if normalized_source == RewardSourceType.LUCKY_WHEEL:
            return (
                f"Congratulations! You earned {points} PP Coins from Lucky Wheel."
            )
        return f"Congratulations! You earned {points} PP Coins."

    @staticmethod
    def _create_points_earned_notification(point_tx):
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        recipient_profile = _resolve_reward_recipient_profile(
            point_tx.user,
            point_tx.profile,
        )
        if recipient_profile is None:
            return None

        return NotificationService.create_notification(
            recipient_profile=recipient_profile,
            notification_type=NotificationType.POINTS_EARNED,
            title=_("Congratulations! Points earned"),
            body=PointService._build_points_earned_body(
                point_tx.points,
                point_tx.source_type,
            ),
            object_type="point_transaction",
            object_id=point_tx.pk,
            metadata={
                "point_transaction_id": point_tx.pk,
                "points": point_tx.points,
                "source_type": point_tx.source_type,
                "source_id": point_tx.source_id,
            },
            idempotency_key=f"points_earned:{point_tx.pk}",
        )


    @staticmethod
    def _next_balance(current_balance, transaction_type, points):
        if transaction_type == PointTransactionType.EARN:
            return current_balance + points

        if current_balance < points:
            raise ValidationError(
                {"points": _("Insufficient point balance for this action.")}
            )

        if transaction_type in {
            PointTransactionType.SPEND,
            PointTransactionType.EXPIRE,
        }:
            return current_balance - points

        raise ValidationError({"transaction_type": _("Unsupported type.")})

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
                        _("Idempotency key was already used for a different "
                        "transaction.")
                    )
                }
            )


class BadgeService:
    """Badge catalog and explicit manager-award helpers."""

    @staticmethod
    def get_badge_catalog():
        return [dict(BADGE_DEFINITIONS[badge_type]) for badge_type in BadgeType.values]

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
            with transaction.atomic():
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
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
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
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        recipient_profile = _resolve_reward_recipient_profile(
            award.profile.user,
            award.profile,
        )
        if recipient_profile is None:
            return None

        badge_name = BADGE_DEFINITIONS[award.badge_type]["name"]
        return NotificationService.create_notification(
            recipient_profile=recipient_profile,
            notification_type=NotificationType.BADGE_AWARDED,
            title=_("Badge awarded"),
            body=f"Congratulations! You earned the {badge_name} badge.",
            object_type="badge_award",
            object_id=award.pk,
            metadata={
                "badge_award_id": award.pk,
                "profile_id": award.profile_id,
                "badge_type": award.badge_type,
            },
            idempotency_key=f"badge_awarded:{award.pk}",
        )


@dataclass(frozen=True)
class RewardRule:
    code: str
    label: str
    enabled: bool
    points: int | None
    multiplier: Decimal | None
    threshold: int | None
    configuration: dict
    required_fields: tuple
    is_implemented: bool
    is_overridden: bool
    updated_by_id: int | None = None
    updated_at: datetime | None = None


class RewardRuleService:
    """Resolve reward rule values: manager override row, else canonical default.

    Codes are system-defined in ``RewardRuleCode``; unknown codes raise instead
    of silently resolving to zero. Changes only affect rewards granted after
    the change, because awarded ``PointTransaction`` rows are never rewritten.
    """

    @staticmethod
    def is_known_code(code):
        return code in RewardRuleCode.values

    @staticmethod
    def list_rules():
        overrides = {row.code: row for row in RewardRuleConfig.objects.all()}
        return [
            RewardRuleService._build_rule(code, overrides.get(code))
            for code in RewardRuleCode.values
        ]

    @staticmethod
    def get_rule(code):
        code = RewardRuleService._require_known_code(code)
        override = RewardRuleConfig.objects.filter(code=code).first()
        return RewardRuleService._build_rule(code, override)

    @staticmethod
    def is_enabled(code):
        return RewardRuleService.get_rule(code).enabled

    @staticmethod
    def get_points(code):
        return RewardRuleService.get_rule(code).points

    @staticmethod
    def get_threshold(code):
        return RewardRuleService.get_rule(code).threshold

    @staticmethod
    @transaction.atomic
    def update_rule(code, *, updated_by, **changes):
        """Create or update the override row, starting from the current default."""

        code = RewardRuleService._require_known_code(code)
        default = DEFAULT_REWARD_RULES[code]
        config, _created = (
            RewardRuleConfig.objects.select_for_update().get_or_create(
                code=code,
                defaults={
                    field: deepcopy(default[field])
                    for field in REWARD_RULE_TUNABLE_FIELDS
                },
            )
        )
        for field, value in changes.items():
            if field not in REWARD_RULE_TUNABLE_FIELDS:
                raise ValidationError({field: _("This field cannot be changed.")})
            setattr(config, field, value)
        config.updated_by = updated_by
        config.save()
        return RewardRuleService._build_rule(code, config)

    @staticmethod
    def _require_known_code(code):
        if not RewardRuleService.is_known_code(code):
            raise ValidationError({"code": _("Unknown reward rule code.")})
        return str(code)

    @staticmethod
    def _build_rule(code, override):
        default = DEFAULT_REWARD_RULES[code]
        if override is None:
            values = {
                field: deepcopy(default[field]) for field in REWARD_RULE_TUNABLE_FIELDS
            }
        else:
            values = {
                field: getattr(override, field) for field in REWARD_RULE_TUNABLE_FIELDS
            }
        return RewardRule(
            code=code,
            label=str(RewardRuleCode(code).label),
            required_fields=default["required_fields"],
            is_implemented=default["is_implemented"],
            is_overridden=override is not None,
            updated_by_id=getattr(override, "updated_by_id", None),
            updated_at=getattr(override, "modified", None),
            **values,
        )


@dataclass(frozen=True)
class PermanentLevelStatus:
    """Immutable permanent buyer/seller level evaluation result (BE-LTY-01A).

    - Below 100 (`< 100`): `current_level`, `level_code`, `level_name`, and
      `benefit_label` are `None` (no invented `"Bronze"` or `"Beginner"` level).
    - Operational benefits are all `BLOCKED` pending product rules (`LEVEL BENEFIT — BLOCKED`);
      `benefit_label` is display-only metadata.
    - When `canonical_currency` is omitted or `CompletedDeal` history spans
      multiple/mismatched currencies, automatic threshold evaluation returns
      `is_blocked=True` with `blocker_code="LEVEL METRIC CURRENCY UNIT — BLOCKED"`.
    """

    current_level: int | None
    level_code: str | None
    level_name: str | None
    benefit_label: str | None
    benefit_status: str
    progress_total: Decimal | None
    next_level_threshold: Decimal | None
    currency: str | None = None
    is_blocked: bool = False
    blocker_code: str | None = None


def _resolve_permanent_level_for_total(
    total_amount,
    level_table: tuple[PermanentLevelSpec, ...],
    *,
    currency: str | None = None,
) -> PermanentLevelStatus:
    normalized_total = Decimal(str(total_amount if total_amount is not None else "0.00"))
    if normalized_total < Decimal("0"):
        raise ValidationError({"total_amount": _("Cumulative total cannot be negative.")})

    earned_spec: PermanentLevelSpec | None = None
    next_threshold: Decimal | None = None
    for spec in level_table:
        if normalized_total >= spec.threshold:
            earned_spec = spec
        elif next_threshold is None:
            next_threshold = spec.threshold

    if earned_spec is None:
        return PermanentLevelStatus(
            current_level=None,
            level_code=None,
            level_name=None,
            benefit_label=None,
            benefit_status=LEVEL_BENEFIT_BLOCKER_CODE,
            progress_total=normalized_total,
            next_level_threshold=next_threshold,
            currency=currency,
            is_blocked=False,
            blocker_code=None,
        )

    return PermanentLevelStatus(
        current_level=earned_spec.level_number,
        level_code=earned_spec.code,
        level_name=earned_spec.level_name,
        benefit_label=earned_spec.benefit_label,
        benefit_status=LEVEL_BENEFIT_BLOCKER_CODE,
        progress_total=normalized_total,
        next_level_threshold=next_threshold,
        currency=currency,
        is_blocked=False,
        blocker_code=None,
    )


def _validate_level_profile(profile, *, expected_profile_type: str) -> int:
    if profile is None or getattr(profile, "pk", None) is None:
        raise ValidationError({"profile": _("A persisted profile is required.")})
    profile_type = getattr(profile, "type", None)
    if profile_type != expected_profile_type:
        raise ValidationError(
            {
                "profile": _(
                    f"Expected {expected_profile_type} profile for level evaluation."
                )
            }
        )
    return int(profile.pk)


def _completed_deal_totals_by_currency(
    profile,
    *,
    expected_profile_type: str,
    deal_role_field: str,
) -> dict[str, Decimal]:
    from apps.deals.models import CompletedDeal

    profile_id = _validate_level_profile(
        profile,
        expected_profile_type=expected_profile_type,
    )
    rows = (
        CompletedDeal.objects.filter(**{deal_role_field: profile_id})
        .values("currency")
        .annotate(total=Sum("amount"))
        .order_by("currency")
    )
    return {
        (row["currency"] or ""): (row["total"] or Decimal("0.00"))
        for row in rows
    }


def _evaluate_profile_permanent_level(
    profile,
    *,
    expected_profile_type: str,
    deal_role_field: str,
    level_table: tuple[PermanentLevelSpec, ...],
    canonical_currency: str | None = None,
) -> PermanentLevelStatus:
    totals_by_currency = _completed_deal_totals_by_currency(
        profile,
        expected_profile_type=expected_profile_type,
        deal_role_field=deal_role_field,
    )
    norm_currency = (canonical_currency or "").strip().upper() or None
    if norm_currency is None:
        return PermanentLevelStatus(
            current_level=None,
            level_code=None,
            level_name=None,
            benefit_label=None,
            benefit_status=LEVEL_BENEFIT_BLOCKER_CODE,
            progress_total=None,
            next_level_threshold=None,
            currency=None,
            is_blocked=True,
            blocker_code=LEVEL_METRIC_CURRENCY_BLOCKER_CODE,
        )

    for deal_currency in totals_by_currency:
        if deal_currency != norm_currency:
            return PermanentLevelStatus(
                current_level=None,
                level_code=None,
                level_name=None,
                benefit_label=None,
                benefit_status=LEVEL_BENEFIT_BLOCKER_CODE,
                progress_total=None,
                next_level_threshold=None,
                currency=None,
                is_blocked=True,
                blocker_code=LEVEL_METRIC_CURRENCY_BLOCKER_CODE,
            )

    total_amount = totals_by_currency.get(norm_currency, Decimal("0.00"))
    return _resolve_permanent_level_for_total(
        total_amount,
        level_table,
        currency=norm_currency,
    )


class SellerLevelService:
    """Seller level foundation (BE-LTY-01A) plus legacy order-count summary helper."""

    LEVEL_TABLE = SELLER_LEVEL_TABLE

    @staticmethod
    def get_level(completed_orders_count):
        completed_orders_count = int(completed_orders_count or 0)
        for threshold, level in LEVEL_THRESHOLDS:
            if completed_orders_count >= threshold:
                return level
        return BASIC_LEVEL

    @classmethod
    def resolve_level_for_total(
        cls,
        total_sales_amount,
        *,
        currency: str | None = None,
    ) -> PermanentLevelStatus:
        return _resolve_permanent_level_for_total(
            total_sales_amount,
            cls.LEVEL_TABLE,
            currency=currency,
        )

    @staticmethod
    def get_completed_deal_totals_by_currency(seller_profile) -> dict[str, Decimal]:
        return _completed_deal_totals_by_currency(
            seller_profile,
            expected_profile_type=ProfileType.SELLER,
            deal_role_field="seller_profile_id",
        )

    @classmethod
    def evaluate_permanent_level(
        cls,
        seller_profile,
        *,
        canonical_currency: str | None = None,
    ) -> PermanentLevelStatus:
        return _evaluate_profile_permanent_level(
            seller_profile,
            expected_profile_type=ProfileType.SELLER,
            deal_role_field="seller_profile_id",
            level_table=cls.LEVEL_TABLE,
            canonical_currency=canonical_currency,
        )


class BuyerLevelService:
    """Permanent buyer level foundation (BE-LTY-01A) backed by CompletedDeal."""

    LEVEL_TABLE = BUYER_LEVEL_TABLE

    @classmethod
    def resolve_level_for_total(
        cls,
        total_purchases_amount,
        *,
        currency: str | None = None,
    ) -> PermanentLevelStatus:
        return _resolve_permanent_level_for_total(
            total_purchases_amount,
            cls.LEVEL_TABLE,
            currency=currency,
        )

    @staticmethod
    def get_completed_deal_totals_by_currency(customer_profile) -> dict[str, Decimal]:
        return _completed_deal_totals_by_currency(
            customer_profile,
            expected_profile_type=ProfileType.CUSTOMER,
            deal_role_field="buyer_profile_id",
        )

    @classmethod
    def evaluate_permanent_level(
        cls,
        customer_profile,
        *,
        canonical_currency: str | None = None,
    ) -> PermanentLevelStatus:
        return _evaluate_profile_permanent_level(
            customer_profile,
            expected_profile_type=ProfileType.CUSTOMER,
            deal_role_field="buyer_profile_id",
            level_table=cls.LEVEL_TABLE,
            canonical_currency=canonical_currency,
        )


class CustomerLevelService(BuyerLevelService):
    """Customer profile level helper preserving legacy serializer summary fields."""

    @staticmethod
    def get_level(completed_orders_count):
        completed_orders_count = int(completed_orders_count or 0)
        for threshold, level in LEVEL_THRESHOLDS:
            if completed_orders_count >= threshold:
                return level
        return BASIC_LEVEL

    @staticmethod
    def get_discount_percent(profile_or_completed_orders_count):
        count = CustomerLevelService._count_value(profile_or_completed_orders_count)
        level = CustomerLevelService.get_level(count)
        if level.code in {"reliable", "golden", "elite", "legendary"}:
            return 2
        return 0

    @staticmethod
    def get_cashback_percent(profile_or_completed_orders_count):
        count = CustomerLevelService._count_value(profile_or_completed_orders_count)
        level = CustomerLevelService.get_level(count)
        if level.code in {"golden", "elite", "legendary"}:
            return 1
        return 0

    @staticmethod
    def _count_value(profile_or_count):
        if isinstance(profile_or_count, int):
            return profile_or_count
        return getattr(profile_or_count, "completed_orders_count", 0)

