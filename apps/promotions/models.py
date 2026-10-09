from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.assets.models import AssetCategory
from apps.general.models import BaseModel


class OfferTriggerType(models.TextChoices):
    GLOBAL = "global", _("Global")
    LUCKY_WHEEL = "lucky_wheel", _("Lucky Wheel")
    ONBOARDING = "onboarding", _("Onboarding")
    REFERRAL = "referral", _("Referral")
    SEASON = "season", _("Season")
    MANAGER_GRANT = "manager_grant", _("Manager Grant")


class OfferValueType(models.TextChoices):
    PERCENTAGE = "percentage", _("Percentage")
    FIXED_AMOUNT = "fixed_amount", _("Fixed Amount")


class OfferTargetType(models.TextChoices):
    ALL = "all", _("All")
    CATEGORY = "category", _("Category")
    ASSET = "asset", _("Asset")


class CashbackTransactionType(models.TextChoices):
    EARNED = "earned", _("Earned")
    REDEEMED = "redeemed", _("Redeemed")
    REFUNDED = "refunded", _("Refunded")
    ADMIN_ADJUSTMENT = "admin_adjustment", _("Admin Adjustment")
    POINTS_PURCHASE = "points_purchase", _("Points Purchase")


class UserPromotionGrantType(models.TextChoices):
    DISCOUNT = "discount", _("Discount")
    CASHBACK = "cashback", _("Cashback")


class UserPromotionGrantStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    REDEEMED = "redeemed", _("Redeemed")
    EXPIRED = "expired", _("Expired")
    CANCELLED = "cancelled", _("Cancelled")


class OfferBase(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    value_type = models.CharField(
        max_length=20,
        choices=OfferValueType.choices,
    )
    value = models.DecimalField(max_digits=10, decimal_places=2)
    target_type = models.CharField(
        max_length=20,
        choices=OfferTargetType.choices,
        default=OfferTargetType.ALL,
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="%(class)ss",
        null=True,
        blank=True,
    )
    category = models.CharField(
        max_length=20,
        choices=AssetCategory.choices,
        null=True,
        blank=True,
    )
    minimum_order_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    trigger_type = models.CharField(
        max_length=30,
        choices=OfferTriggerType.choices,
        default=OfferTriggerType.GLOBAL,
        db_index=True,
    )
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="created_%(class)ss",
        null=True,
        blank=True,
    )

    IMMUTABLE_ALLOWLIST = {
        "is_active",
        "deleted",
        "deleted_by_cascade",
    }

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        errors = {}
        self._validate_value(errors)
        self._validate_target(errors)
        self._validate_immutable_update(errors)
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def _validate_value(self, errors):
        if self.value is None or self.value <= 0:
            errors["value"] = "Offer value must be greater than 0."

        if (
            self.value_type == OfferValueType.PERCENTAGE
            and self.value is not None
            and self.value > Decimal("100.00")
        ):
            errors["value"] = "Percentage offer value cannot exceed 100."

        if self.minimum_order_price is not None and self.minimum_order_price < 0:
            errors["minimum_order_price"] = "Minimum order price cannot be negative."

    def _validate_target(self, errors):
        if self.target_type == OfferTargetType.ASSET:
            if self.asset_id is None:
                errors["asset"] = "Asset is required for asset-targeted offers."
            if self.category:
                errors["category"] = "Category must be empty for asset-targeted offers."
            return

        if self.target_type == OfferTargetType.CATEGORY:
            if not self.category:
                errors["category"] = (
                    "Category is required for category-targeted offers."
                )
            if self.asset_id is not None:
                errors["asset"] = "Asset must be empty for category-targeted offers."
            return

        if self.target_type == OfferTargetType.ALL:
            if self.asset_id is not None or self.category:
                errors["target_type"] = (
                    "Asset and category must be empty for all-targeted offers."
                )
            return

        errors["target_type"] = "Unsupported offer target type."

    def _validate_immutable_update(self, errors):
        if self.pk is None:
            return

        try:
            current = type(self).objects.get(pk=self.pk)
        except type(self).DoesNotExist:
            return

        changed_fields = []
        for field in self._meta.fields:
            if field.name in self.IMMUTABLE_ALLOWLIST:
                continue
            if field.name in {"id", "created", "modified"}:
                continue
            if getattr(current, field.attname) != getattr(self, field.attname):
                changed_fields.append(field.name)

        if changed_fields:
            errors["detail"] = (
                "Offers are immutable after creation except activation state."
            )


class DiscountOffer(OfferBase):
    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["is_active", "trigger_type", "target_type"],
                name="disc_offer_active_target_idx",
            ),
            models.Index(
                fields=["priority", "-created"],
                name="disc_offer_prio_created_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(value__gt=0),
                name="disc_offer_value_gt_0_ck",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(value_type=OfferValueType.FIXED_AMOUNT)
                    | models.Q(value__lte=100)
                ),
                name="disc_offer_pct_lte_100_ck",
            ),
            models.CheckConstraint(
                check=models.Q(minimum_order_price__gte=0),
                name="disc_offer_min_order_gte_0_ck",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        target_type=OfferTargetType.ALL,
                        asset__isnull=True,
                        category__isnull=True,
                    )
                    | models.Q(
                        target_type=OfferTargetType.ASSET,
                        asset__isnull=False,
                        category__isnull=True,
                    )
                    | models.Q(
                        target_type=OfferTargetType.CATEGORY,
                        asset__isnull=True,
                        category__isnull=False,
                    )
                ),
                name="disc_offer_target_shape_ck",
            ),
        ]

    def __str__(self):
        return f"DiscountOffer #{self.pk} {self.title}"


class CashbackOffer(OfferBase):
    allowed_redemption_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["is_active", "trigger_type", "target_type"],
                name="cb_offer_active_target_idx",
            ),
            models.Index(
                fields=["priority", "-created"],
                name="cb_offer_prio_created_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(value__gt=0),
                name="cb_offer_value_gt_0_ck",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(value_type=OfferValueType.FIXED_AMOUNT)
                    | models.Q(value__lte=100)
                ),
                name="cb_offer_pct_lte_100_ck",
            ),
            models.CheckConstraint(
                check=models.Q(minimum_order_price__gte=0),
                name="cb_offer_min_order_gte_0_ck",
            ),
            models.CheckConstraint(
                check=models.Q(allowed_redemption_amount__gte=0),
                name="cb_offer_redeem_gte_0_ck",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        target_type=OfferTargetType.ALL,
                        asset__isnull=True,
                        category__isnull=True,
                    )
                    | models.Q(
                        target_type=OfferTargetType.ASSET,
                        asset__isnull=False,
                        category__isnull=True,
                    )
                    | models.Q(
                        target_type=OfferTargetType.CATEGORY,
                        asset__isnull=True,
                        category__isnull=False,
                    )
                ),
                name="cb_offer_target_shape_ck",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.allowed_redemption_amount is not None
            and self.allowed_redemption_amount < 0
        ):
            raise ValidationError(
                {
                    "allowed_redemption_amount": (
                        _("Allowed redemption amount cannot be negative.")
                    )
                }
            )

    def __str__(self):
        return f"CashbackOffer #{self.pk} {self.title}"


class UserPromotionGrant(BaseModel):
    """User-owned non-global offer grant, typically from wheel/referral/season."""

    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="promotion_grants",
    )
    profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="promotion_grants",
        null=True,
        blank=True,
    )
    grant_type = models.CharField(
        max_length=20,
        choices=UserPromotionGrantType.choices,
    )
    discount_offer = models.ForeignKey(
        DiscountOffer,
        on_delete=models.PROTECT,
        related_name="user_grants",
        null=True,
        blank=True,
    )
    cashback_offer = models.ForeignKey(
        CashbackOffer,
        on_delete=models.PROTECT,
        related_name="user_grants",
        null=True,
        blank=True,
    )
    source_type = models.CharField(max_length=100, db_index=True)
    source_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=UserPromotionGrantStatus.choices,
        default=UserPromotionGrantStatus.ACTIVE,
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.SET_NULL,
        related_name="promotion_grants",
        null=True,
        blank=True,
    )
    idempotency_key = models.CharField(max_length=255, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["user", "status"], name="promo_grant_user_status_idx"),
            models.Index(
                fields=["profile", "status"],
                name="promo_grant_profile_status_idx",
            ),
            models.Index(
                fields=["source_type", "source_id"],
                name="promo_grant_source_idx",
            ),
            models.Index(fields=["expires_at"], name="promo_grant_expires_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    (
                        models.Q(discount_offer__isnull=False)
                        & models.Q(cashback_offer__isnull=True)
                        & models.Q(grant_type=UserPromotionGrantType.DISCOUNT)
                    )
                    | (
                        models.Q(discount_offer__isnull=True)
                        & models.Q(cashback_offer__isnull=False)
                        & models.Q(grant_type=UserPromotionGrantType.CASHBACK)
                    )
                ),
                name="promo_grant_exactly_one_matching_offer_ck",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if bool(self.discount_offer_id) == bool(self.cashback_offer_id):
            errors["detail"] = "Exactly one promotion offer must be selected."
        if (
            self.discount_offer_id
            and self.grant_type != UserPromotionGrantType.DISCOUNT
        ):
            errors["grant_type"] = "Grant type must match discount offer."
        if (
            self.cashback_offer_id
            and self.grant_type != UserPromotionGrantType.CASHBACK
        ):
            errors["grant_type"] = "Grant type must match cashback offer."
        if not self.idempotency_key:
            errors["idempotency_key"] = "Idempotency key is required."
        if errors:
            raise ValidationError(errors)

    @property
    def consumed_at(self):
        return self.redeemed_at

    @property
    def is_onboarding_highlight_benefit(self) -> bool:
        if (
            self.user_id
            and self.idempotency_key
            == f"promotion:onboarding_highlight:user:{self.user_id}"
        ):
            return True
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        return (
            metadata.get("benefit_code")
            == "onboarding_first_auction_highlight_20_pct"
        )

    @property
    def discount_percent(self) -> Decimal:
        if (
            self.grant_type == UserPromotionGrantType.DISCOUNT
            and self.discount_offer_id
            and self.discount_offer is not None
        ):
            if self.discount_offer.value_type == OfferValueType.PERCENTAGE:
                return Decimal(str(self.discount_offer.value)).quantize(
                    Decimal("0.01")
                )
            return Decimal("0.00")
        if self.is_onboarding_highlight_benefit:
            return Decimal("20.00")
        return Decimal("0.00")

    @property
    def is_available(self) -> bool:
        if self.status != UserPromotionGrantStatus.ACTIVE:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True

    @property
    def is_consumed(self) -> bool:
        return self.status == UserPromotionGrantStatus.REDEEMED

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class CashbackTransaction(BaseModel):
    customer = models.ForeignKey(
        "users.CustomerProfile",
        on_delete=models.PROTECT,
        related_name="cashback_transactions",
    )
    cashback_offer = models.ForeignKey(
        CashbackOffer,
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.PROTECT,
        related_name="cashback_transactions",
        null=True,
        blank=True,
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=CashbackTransactionType.choices,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    adjusted_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="cashback_adjustments",
        null=True,
        blank=True,
    )
    note = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["customer", "-created"],
                name="cb_txn_customer_created_idx",
            ),
            models.Index(
                fields=["transaction_type"],
                name="cb_txn_type_idx",
            ),
            models.Index(
                fields=["order_item"],
                name="cb_txn_order_item_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(balance_after__gte=0),
                name="cb_txn_balance_after_gte_0_ck",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(transaction_type=CashbackTransactionType.ADMIN_ADJUSTMENT)
                    & ~models.Q(amount=0)
                )
                | (
                    ~models.Q(transaction_type=CashbackTransactionType.ADMIN_ADJUSTMENT)
                    & models.Q(amount__gt=0)
                ),
                name="cb_txn_amount_sign_ck",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if not self.idempotency_key:
            errors["idempotency_key"] = "Idempotency key is required."

        if self.balance_after is not None and self.balance_after < 0:
            errors["balance_after"] = "Balance after cannot be negative."

        if self.transaction_type == CashbackTransactionType.ADMIN_ADJUSTMENT:
            if self.amount == 0:
                errors["amount"] = "Admin adjustment amount cannot be zero."
        elif self.amount is None or self.amount <= 0:
            errors["amount"] = "Cashback transaction amount must be greater than 0."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"CashbackTransaction #{self.pk} " f"{self.transaction_type} {self.amount}"
        )


class PromotionCampaignType(models.TextChoices):
    MONTHLY = "MONTHLY", _("Monthly")
    SEASONAL = "SEASONAL", _("Seasonal")
    FLASH = "FLASH", _("Flash")


class PromotionCampaignStatus(models.TextChoices):
    DISABLED = "DISABLED", _("Disabled")
    SCHEDULED = "SCHEDULED", _("Scheduled")
    ACTIVE = "ACTIVE", _("Active")
    ENDED = "ENDED", _("Ended")


class PromotionCampaign(BaseModel):
    """Admin-managed scheduled promotion campaign window (BE-PRO-01).

    Owns campaign family classification (MONTHLY, SEASONAL, FLASH), timezone-aware
    half-open scheduling [starts_at, ends_at), and enable/disable state.
    Service-level economic effects and target application belong to BE-PRO-02.
    """

    name = models.CharField(max_length=255)
    campaign_type = models.CharField(
        max_length=20,
        choices=PromotionCampaignType.choices,
        db_index=True,
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_enabled = models.BooleanField(default=True, db_index=True)
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="created_promotion_campaigns",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["starts_at", "id"]
        indexes = [
            models.Index(
                fields=["is_enabled", "starts_at", "ends_at"],
                name="promo_campaign_active_win_idx",
            ),
            models.Index(
                fields=["campaign_type", "starts_at", "ends_at"],
                name="promo_campaign_type_win_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(starts_at__lt=models.F("ends_at")),
                name="promo_campaign_starts_before_ends_ck",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.campaign_type})"

    def clean(self):
        super().clean()
        errors = {}
        if not self.name or not str(self.name).strip():
            errors["name"] = _("Campaign name is required.")
        if self.campaign_type not in PromotionCampaignType.values:
            errors["campaign_type"] = _("Invalid promotion campaign type.")
        if self.starts_at is not None and timezone.is_naive(self.starts_at):
            errors["starts_at"] = _("Campaign start time must be timezone-aware.")
        if self.ends_at is not None and timezone.is_naive(self.ends_at):
            errors["ends_at"] = _("Campaign end time must be timezone-aware.")
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and "starts_at" not in errors
            and "ends_at" not in errors
            and self.starts_at >= self.ends_at
        ):
            errors["ends_at"] = _(
                "Campaign start time must be earlier than end time."
            )
        if self.metadata is not None and not isinstance(self.metadata, dict):
            errors["metadata"] = _("Campaign metadata must be a JSON object.")
        if errors:
            raise ValidationError(errors)

    def effective_status(self, now=None) -> str:
        """Derive effective lifecycle status at `now` using half-open [starts_at, ends_at)."""
        now = now or timezone.now()
        if timezone.is_naive(now):
            raise ValidationError(
                {"now": _("Evaluation time must be timezone-aware.")}
            )
        if not self.is_enabled:
            return PromotionCampaignStatus.DISABLED
        if now < self.starts_at:
            return PromotionCampaignStatus.SCHEDULED
        if self.starts_at <= now < self.ends_at:
            return PromotionCampaignStatus.ACTIVE
        return PromotionCampaignStatus.ENDED

    def is_active_at(self, now=None) -> bool:
        return self.effective_status(now=now) == PromotionCampaignStatus.ACTIVE

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)



class PromotionEffectType(models.TextChoices):
    PERCENT_DISCOUNT = "PERCENT_DISCOUNT", _("Percentage Discount")


class PromotionTargetType(models.TextChoices):
    SELLER_PACKAGE = "SELLER_PACKAGE", _("Seller Package")
    AUCTION_HIGHLIGHT = "AUCTION_HIGHLIGHT", _("Auction Highlight")
    LOFT_SUBSCRIPTION = "LOFT_SUBSCRIPTION", _("Loft Subscription")


class PromotionCampaignEffect(BaseModel):
    """Service-level percentage discount effect belonging to a PromotionCampaign (BE-PRO-02).

    Editable only while `timezone.now() < campaign.starts_at`. Once the campaign
    starts, effect parameters and targets are frozen.
    """

    campaign = models.ForeignKey(
        PromotionCampaign,
        on_delete=models.CASCADE,
        related_name="effects",
        db_index=True,
    )
    effect_type = models.CharField(
        max_length=30,
        choices=PromotionEffectType.choices,
        default=PromotionEffectType.PERCENT_DISCOUNT,
        db_index=True,
    )
    target_type = models.CharField(
        max_length=30,
        choices=PromotionTargetType.choices,
        db_index=True,
    )
    target_package = models.ForeignKey(
        "packages.Package",
        on_delete=models.PROTECT,
        related_name="promotion_campaign_effects",
        null=True,
        blank=True,
    )
    target_subscription_duration = models.ForeignKey(
        "loft_subscriptions.SubscriptionDurationOption",
        on_delete=models.PROTECT,
        related_name="promotion_campaign_effects",
        null=True,
        blank=True,
    )
    target_code = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_index=True,
    )
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01")),
            MaxValueValidator(Decimal("100.00")),
        ],
    )
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="created_promotion_campaign_effects",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-discount_percent", "id"]
        indexes = [
            models.Index(
                fields=["campaign", "target_type", "effect_type"],
                name="promo_eff_camp_target_idx",
            ),
            models.Index(
                fields=["target_type", "discount_percent", "id"],
                name="promo_eff_target_disc_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    discount_percent__gt=Decimal("0.00"),
                    discount_percent__lte=Decimal("100.00"),
                ),
                name="promo_eff_discount_pct_range_ck",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        target_type="SELLER_PACKAGE",
                        target_subscription_duration__isnull=True,
                    )
                    | models.Q(
                        target_type="LOFT_SUBSCRIPTION",
                        target_package__isnull=True,
                    )
                    | models.Q(
                        target_type="AUCTION_HIGHLIGHT",
                        target_package__isnull=True,
                        target_subscription_duration__isnull=True,
                    )
                ),
                name="promo_eff_target_fk_matches_type_ck",
            ),
        ]

    def __str__(self):
        return (
            f"PromotionCampaignEffect #{self.pk} ({self.target_type} "
            f"-{self.discount_percent}%)"
        )

    def clean(self):
        super().clean()
        errors = {}

        if not self.campaign_id:
            errors["campaign"] = _("Promotion campaign is required.")

        if self.effect_type not in PromotionEffectType.values:
            errors["effect_type"] = _("Invalid promotion effect type.")

        if self.target_type not in PromotionTargetType.values:
            errors["target_type"] = _("Invalid promotion target type.")

        if isinstance(self.discount_percent, float):
            errors["discount_percent"] = _(
                "Discount percentage must use Decimal precision, not float."
            )
        elif self.discount_percent is None:
            errors["discount_percent"] = _("Discount percentage is required.")
        else:
            try:
                pct = Decimal(str(self.discount_percent))
            except Exception:
                errors["discount_percent"] = _("Invalid discount percentage.")
            else:
                if pct <= Decimal("0.00") or pct > Decimal("100.00"):
                    errors["discount_percent"] = _(
                        "Discount percentage must be greater than 0 and at most 100."
                    )

        if self.target_type == PromotionTargetType.SELLER_PACKAGE:
            if self.target_subscription_duration_id is not None:
                errors["target_subscription_duration"] = _(
                    "Subscription duration target is only valid for LOFT_SUBSCRIPTION effects."
                )
            if self.target_code:
                errors["target_code"] = _(
                    "Seller package effects use target_package instead of target_code."
                )
        elif self.target_type == PromotionTargetType.LOFT_SUBSCRIPTION:
            if self.target_package_id is not None:
                errors["target_package"] = _(
                    "Seller package target is only valid for SELLER_PACKAGE effects."
                )
            if (
                self.target_subscription_duration_id is not None
                and self.target_subscription_duration is not None
            ):
                if (
                    self.target_code
                    and self.target_code != self.target_subscription_duration.code
                ):
                    errors["target_code"] = _(
                        "Target code does not match the selected subscription duration."
                    )
                self.target_code = self.target_subscription_duration.code
        elif self.target_type == PromotionTargetType.AUCTION_HIGHLIGHT:
            if self.target_package_id is not None:
                errors["target_package"] = _(
                    "Seller package target is not valid for AUCTION_HIGHLIGHT effects."
                )
            if self.target_subscription_duration_id is not None:
                errors["target_subscription_duration"] = _(
                    "Subscription duration target is not valid for AUCTION_HIGHLIGHT effects."
                )

        if self.metadata is not None and not isinstance(self.metadata, dict):
            errors["metadata"] = _("Effect metadata must be a JSON object.")

        if not errors and self.campaign_id:
            dup_qs = PromotionCampaignEffect.objects.filter(
                campaign_id=self.campaign_id,
                deleted__isnull=True,
                effect_type=self.effect_type,
                target_type=self.target_type,
                target_package_id=self.target_package_id,
                target_subscription_duration_id=self.target_subscription_duration_id,
                target_code=self.target_code or "",
            )
            if self.pk:
                dup_qs = dup_qs.exclude(pk=self.pk)
            if dup_qs.exists():
                errors["target_type"] = _(
                    "A promotion effect for this target already exists in the campaign."
                )

        if errors:
            raise ValidationError(errors)

    def _assert_campaign_not_started(self, now=None):
        if not self.campaign_id:
            return
        now = now or timezone.now()
        if now >= self.campaign.starts_at:
            raise ValidationError(
                {
                    "campaign": _(
                        "Campaign effects cannot be modified after the campaign starts."
                    )
                }
            )

    def save(self, *args, now=None, **kwargs):
        if isinstance(self.discount_percent, float):
            raise ValidationError(
                {
                    "discount_percent": _(
                        "Discount percentage must use Decimal precision, not float."
                    )
                }
            )
        self._assert_campaign_not_started(now=now)
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, now=None, **kwargs):
        self._assert_campaign_not_started(now=now)
        return super().delete(*args, **kwargs)


class SellerPackagePromotionSnapshot(BaseModel):
    """Durable snapshot of the promotion discount resolved when a SellerPackage was requested (BE-PRO-02)."""

    seller_package = models.OneToOneField(
        "packages.SellerPackage",
        on_delete=models.CASCADE,
        related_name="promotion_snapshot",
    )
    package = models.ForeignKey(
        "packages.Package",
        on_delete=models.PROTECT,
        related_name="promotion_snapshots",
    )
    promotion_campaign = models.ForeignKey(
        PromotionCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seller_package_snapshots",
    )
    promotion_effect = models.ForeignKey(
        PromotionCampaignEffect,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seller_package_snapshots",
    )
    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
            MaxValueValidator(Decimal("100.00")),
        ],
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    final_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    class Meta:
        ordering = ["-created"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    base_price__gte=Decimal("0.00"),
                    discount_percent__gte=Decimal("0.00"),
                    discount_percent__lte=Decimal("100.00"),
                    discount_amount__gte=Decimal("0.00"),
                    final_price__gte=Decimal("0.00"),
                ),
                name="promo_pkg_snap_amounts_non_neg_ck",
            ),
        ]

    def as_dict(self):
        return {
            "promotion_campaign_id": self.promotion_campaign_id,
            "promotion_effect_id": self.promotion_effect_id,
            "base_price": f"{self.base_price:.2f}",
            "discount_percent": f"{self.discount_percent:.2f}",
            "discount_amount": f"{self.discount_amount:.2f}",
            "final_price": f"{self.final_price:.2f}",
        }
