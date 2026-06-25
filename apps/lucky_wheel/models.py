from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.general.models import BaseModel
from apps.loyalty.constants import BadgeType
from apps.promotions.models import OfferTriggerType


class LuckyWheelGrantSourceType(models.TextChoices):
    ONBOARDING = "onboarding", "Onboarding"
    AUCTION_COMPLETED = "auction_completed", "Auction Completed"
    REFERRAL = "referral", "Referral"
    MANAGER_GRANT = "manager_grant", "Manager Grant"
    SEASON = "season", "Season"


class LuckyWheelPrizeType(models.TextChoices):
    PP_COINS = "pp_coins", "PP Coins"
    DISCOUNT_OFFER = "discount_offer", "Discount Offer"
    CASHBACK_OFFER = "cashback_offer", "Cashback Offer"
    BADGE = "badge", "Badge"
    NO_PRIZE = "no_prize", "No Prize"


class WheelAudience(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    SELLER = "seller", "Seller"
    BOTH = "both", "Both"


class LuckyWheelSpinStatus(models.TextChoices):
    AWARDED = "awarded", "Awarded"
    NO_PRIZE = "no_prize", "No Prize"
    FAILED = "failed", "Failed"


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
    target_profile_type = models.CharField(
        max_length=10,
        choices=WheelAudience.choices,
        default=WheelAudience.BOTH,
        db_index=True,
    )
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
        reward_fields = [
            bool(self.points_amount),
            bool(self.discount_offer_id),
            bool(self.cashback_offer_id),
            bool(self.badge_type),
        ]
        if self.prize_type == LuckyWheelPrizeType.PP_COINS:
            if not self.points_amount:
                errors["points_amount"] = "PP coin prizes require points amount."
        elif self.prize_type == LuckyWheelPrizeType.DISCOUNT_OFFER:
            if not self.discount_offer_id:
                errors["discount_offer"] = "Discount offer prize requires an offer."
            elif self.discount_offer.trigger_type != OfferTriggerType.LUCKY_WHEEL:
                errors["discount_offer"] = "Offer trigger type must be lucky_wheel."
        elif self.prize_type == LuckyWheelPrizeType.CASHBACK_OFFER:
            if not self.cashback_offer_id:
                errors["cashback_offer"] = "Cashback offer prize requires an offer."
            elif self.cashback_offer.trigger_type != OfferTriggerType.LUCKY_WHEEL:
                errors["cashback_offer"] = "Offer trigger type must be lucky_wheel."
        elif self.prize_type == LuckyWheelPrizeType.BADGE:
            if not self.badge_type:
                errors["badge_type"] = "Badge prize requires badge type."
            elif self.badge_type not in BadgeType.values:
                errors["badge_type"] = "Badge type is not in BadgeType."
        elif self.prize_type == LuckyWheelPrizeType.NO_PRIZE:
            if any(reward_fields):
                errors["detail"] = "No-prize rows cannot define reward fields."
        if (
            self.quantity_total is not None
            and self.quantity_awarded > self.quantity_total
        ):
            errors["quantity_awarded"] = (
                "Quantity awarded cannot exceed quantity total."
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
