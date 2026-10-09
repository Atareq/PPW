from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel
from apps.loyalty.constants import BadgeType
from apps.promotions.models import OfferTriggerType


class LuckyWheelGrantSourceType(models.TextChoices):
    ONBOARDING = "onboarding", _("Onboarding")
    AUCTION_COMPLETED = "auction_completed", _("Auction Completed")
    REFERRAL = "referral", _("Referral")
    MANAGER_GRANT = "manager_grant", _("Manager Grant")
    SEASON = "season", _("Season")


class LuckyWheelPrizeType(models.TextChoices):
    PP_COINS = "pp_coins", _("PP Coins")
    DISCOUNT_OFFER = "discount_offer", _("Discount Offer")
    CASHBACK_OFFER = "cashback_offer", _("Cashback Offer")
    BADGE = "badge", _("Badge")
    NO_PRIZE = "no_prize", _("No Prize")


class LuckyWheelSpinStatus(models.TextChoices):
    AWARDED = "awarded", _("Awarded")
    NO_PRIZE = "no_prize", _("No Prize")
    FAILED = "failed", _("Failed")


class LuckyWheelGrant(BaseModel):
    """Attempt ledger row; the user owns coins while profile records context."""

    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="lucky_wheel_grants",
    )
    profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="lucky_wheel_grants",
        null=True,
        blank=True,
    )
    source_type = models.CharField(
        max_length=50,
        choices=LuckyWheelGrantSourceType.choices,
        db_index=True,
    )
    source_id = models.CharField(max_length=100, blank=True)
    total_attempts = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    remaining_attempts = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["expires_at", "created"]
        indexes = [
            models.Index(
                fields=["user", "is_active"], name="wheel_grant_user_active_idx"
            ),
            models.Index(
                fields=["profile", "is_active"],
                name="wheel_grant_profile_active_idx",
            ),
            models.Index(
                fields=["source_type", "source_id"], name="wheel_grant_source_idx"
            ),
            models.Index(fields=["expires_at"], name="wheel_grant_expires_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(total_attempts__gt=0),
                name="wheel_grant_total_attempts_gt_0_ck",
            ),
            models.CheckConstraint(
                check=Q(remaining_attempts__lte=models.F("total_attempts")),
                name="wheel_grant_remaining_lte_total_ck",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.total_attempts <= 0:
            errors["total_attempts"] = "Total attempts must be greater than 0."
        if self.remaining_attempts > self.total_attempts:
            errors["remaining_attempts"] = (
                "Remaining attempts cannot exceed total attempts."
            )
        if not self.idempotency_key:
            errors["idempotency_key"] = "Idempotency key is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class LuckyWheelPrize(BaseModel):
    """Weighted backend prize definition with optional finite inventory."""

    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    prize_type = models.CharField(max_length=30, choices=LuckyWheelPrizeType.choices)
    weight = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    is_active = models.BooleanField(default=True)
    points_amount = models.PositiveIntegerField(null=True, blank=True)
    discount_offer = models.ForeignKey(
        "promotions.DiscountOffer",
        on_delete=models.PROTECT,
        related_name="lucky_wheel_prizes",
        null=True,
        blank=True,
    )
    cashback_offer = models.ForeignKey(
        "promotions.CashbackOffer",
        on_delete=models.PROTECT,
        related_name="lucky_wheel_prizes",
        null=True,
        blank=True,
    )
    badge_type = models.CharField(max_length=50, blank=True)
    quantity_total = models.PositiveIntegerField(null=True, blank=True)
    quantity_awarded = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["label", "id"]
        indexes = [
            models.Index(
                fields=["is_active", "prize_type"], name="wheel_prize_active_type_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(weight__gt=0),
                name="wheel_prize_weight_gt_0_ck",
            ),
            models.CheckConstraint(
                check=(
                    Q(quantity_total__isnull=True)
                    | Q(quantity_awarded__lte=models.F("quantity_total"))
                ),
                name="wheel_prize_awarded_lte_total_ck",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.weight is None
            or isinstance(self.weight, bool)
            or self.weight <= 0
        ):
            errors["weight"] = _("Prize weight must be greater than 0.")

        if (
            self.prize_type != LuckyWheelPrizeType.PP_COINS
            and self.points_amount == 0
        ):
            self.points_amount = None

        has_points = self.points_amount is not None
        reward_fields = [
            bool(self.points_amount),
            bool(self.discount_offer_id),
            bool(self.cashback_offer_id),
            bool(self.badge_type),
        ]

        if self.prize_type == LuckyWheelPrizeType.PP_COINS:
            if (
                self.points_amount is None
                or isinstance(self.points_amount, bool)
                or self.points_amount <= 0
            ):
                errors["points_amount"] = _(
                    "PP coin prizes require points amount."
                )
            if self.discount_offer_id is not None:
                errors["discount_offer"] = _(
                    "PP coin prizes cannot define a discount offer."
                )
            if self.cashback_offer_id is not None:
                errors["cashback_offer"] = _(
                    "PP coin prizes cannot define a cashback offer."
                )
            if self.badge_type:
                errors["badge_type"] = _(
                    "PP coin prizes cannot define a badge type."
                )
        elif self.prize_type == LuckyWheelPrizeType.DISCOUNT_OFFER:
            if not self.discount_offer_id:
                errors["discount_offer"] = _(
                    "Discount offer prize requires an offer."
                )
            elif (
                self.discount_offer.deleted is not None
                or not self.discount_offer.is_active
            ):
                errors["discount_offer"] = _(
                    "Discount offer must be active."
                )
            elif self.discount_offer.trigger_type != OfferTriggerType.LUCKY_WHEEL:
                errors["discount_offer"] = _(
                    "Offer trigger type must be lucky_wheel."
                )
            if has_points:
                errors["points_amount"] = _(
                    "Discount offer prizes cannot define points amount."
                )
            if self.cashback_offer_id is not None:
                errors["cashback_offer"] = _(
                    "Discount offer prizes cannot define a cashback offer."
                )
            if self.badge_type:
                errors["badge_type"] = _(
                    "Discount offer prizes cannot define a badge type."
                )
        elif self.prize_type == LuckyWheelPrizeType.CASHBACK_OFFER:
            if not self.cashback_offer_id:
                errors["cashback_offer"] = _(
                    "Cashback offer prize requires an offer."
                )
            elif (
                self.cashback_offer.deleted is not None
                or not self.cashback_offer.is_active
            ):
                errors["cashback_offer"] = _(
                    "Cashback offer must be active."
                )
            elif self.cashback_offer.trigger_type != OfferTriggerType.LUCKY_WHEEL:
                errors["cashback_offer"] = _(
                    "Offer trigger type must be lucky_wheel."
                )
            if has_points:
                errors["points_amount"] = _(
                    "Cashback offer prizes cannot define points amount."
                )
            if self.discount_offer_id is not None:
                errors["discount_offer"] = _(
                    "Cashback offer prizes cannot define a discount offer."
                )
            if self.badge_type:
                errors["badge_type"] = _(
                    "Cashback offer prizes cannot define a badge type."
                )
        elif self.prize_type == LuckyWheelPrizeType.BADGE:
            if not self.badge_type:
                errors["badge_type"] = _("Badge prize requires badge type.")
            elif self.badge_type not in BadgeType.values:
                errors["badge_type"] = _("Badge type is not in BadgeType.")
            if has_points:
                errors["points_amount"] = _(
                    "Badge prizes cannot define points amount."
                )
            if self.discount_offer_id is not None:
                errors["discount_offer"] = _(
                    "Badge prizes cannot define a discount offer."
                )
            if self.cashback_offer_id is not None:
                errors["cashback_offer"] = _(
                    "Badge prizes cannot define a cashback offer."
                )
        elif self.prize_type == LuckyWheelPrizeType.NO_PRIZE:
            if any(reward_fields):
                errors["detail"] = _(
                    "No-prize rows cannot define reward fields."
                )
        else:
            errors["prize_type"] = _("Unsupported prize type.")

        if self.quantity_total is not None:
            if isinstance(self.quantity_total, bool) or self.quantity_total <= 0:
                errors["quantity_total"] = _(
                    "Quantity total must be greater than 0 when specified."
                )
            elif self.quantity_awarded > self.quantity_total:
                msg = _("Quantity awarded cannot exceed quantity total.")
                errors["quantity_awarded"] = msg
                errors["quantity_total"] = msg

        if isinstance(self.metadata, dict) and "validity_days" in self.metadata:
            validity_days = self.metadata.get("validity_days")
            if validity_days is not None and (
                isinstance(validity_days, bool)
                or not isinstance(validity_days, int)
                or validity_days <= 0
            ):
                errors["metadata"] = _(
                    "Prize validity days must be a positive integer."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class LuckyWheelSpin(BaseModel):
    """Immutable spin result snapshot for idempotent client retries."""

    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="lucky_wheel_spins",
    )
    profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="lucky_wheel_spins",
        null=True,
        blank=True,
    )
    grant = models.ForeignKey(
        LuckyWheelGrant,
        on_delete=models.PROTECT,
        related_name="spins",
    )
    prize = models.ForeignKey(
        LuckyWheelPrize,
        on_delete=models.PROTECT,
        related_name="spins",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=LuckyWheelSpinStatus.choices)
    prize_snapshot = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    awarded_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["user", "-created"], name="wheel_spin_user_created_idx"
            ),
            models.Index(
                fields=["profile", "-created"],
                name="wheel_spin_profile_created_idx",
            ),
        ]
