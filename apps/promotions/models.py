from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.assets.models import AssetCategory
from apps.general.models import BaseModel


class OfferValueType(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage"
    FIXED_AMOUNT = "fixed_amount", "Fixed Amount"


class OfferTargetType(models.TextChoices):
    ALL = "all", "All"
    CATEGORY = "category", "Category"
    ASSET = "asset", "Asset"


class CashbackTransactionType(models.TextChoices):
    EARNED = "earned", "Earned"
    REDEEMED = "redeemed", "Redeemed"
    REFUNDED = "refunded", "Refunded"
    ADMIN_ADJUSTMENT = "admin_adjustment", "Admin Adjustment"


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

        if (
            self.minimum_order_price is not None
            and self.minimum_order_price < 0
        ):
            errors["minimum_order_price"] = (
                "Minimum order price cannot be negative."
            )

    def _validate_target(self, errors):
        if self.target_type == OfferTargetType.ASSET:
            if self.asset_id is None:
                errors["asset"] = "Asset is required for asset-targeted offers."
            if self.category:
                errors["category"] = (
                    "Category must be empty for asset-targeted offers."
                )
            return

        if self.target_type == OfferTargetType.CATEGORY:
            if not self.category:
                errors["category"] = (
                    "Category is required for category-targeted offers."
                )
            if self.asset_id is not None:
                errors["asset"] = (
                    "Asset must be empty for category-targeted offers."
                )
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
                fields=["is_active", "target_type"],
                name="promotions_discount_active_target_idx",
            ),
            models.Index(
                fields=["priority", "-created"],
                name="promotions_discount_priority_created_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(value__gt=0),
                name="promotions_discount_value_gt_0",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(value_type=OfferValueType.FIXED_AMOUNT)
                    | models.Q(value__lte=100)
                ),
                name="promotions_discount_percentage_lte_100",
            ),
            models.CheckConstraint(
                check=models.Q(minimum_order_price__gte=0),
                name="promotions_discount_min_order_gte_0",
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
                name="promotions_discount_target_shape",
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
                fields=["is_active", "target_type"],
                name="promotions_cashback_active_target_idx",
            ),
            models.Index(
                fields=["priority", "-created"],
                name="promotions_cashback_priority_created_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(value__gt=0),
                name="promotions_cashback_value_gt_0",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(value_type=OfferValueType.FIXED_AMOUNT)
                    | models.Q(value__lte=100)
                ),
                name="promotions_cashback_percentage_lte_100",
            ),
            models.CheckConstraint(
                check=models.Q(minimum_order_price__gte=0),
                name="promotions_cashback_min_order_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(allowed_redemption_amount__gte=0),
                name="promotions_cashback_redemption_gte_0",
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
                name="promotions_cashback_target_shape",
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
                        "Allowed redemption amount cannot be negative."
                    )
                }
            )

    def __str__(self):
        return f"CashbackOffer #{self.pk} {self.title}"


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
                name="promotions_cashback_customer_created_idx",
            ),
            models.Index(
                fields=["transaction_type"],
                name="promotions_cashback_type_idx",
            ),
            models.Index(
                fields=["order_item"],
                name="promotions_cashback_order_item_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(balance_after__gte=0),
                name="promotions_cashback_balance_after_gte_0",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        transaction_type=CashbackTransactionType.ADMIN_ADJUSTMENT
                    )
                    & ~models.Q(amount=0)
                )
                | (
                    ~models.Q(
                        transaction_type=CashbackTransactionType.ADMIN_ADJUSTMENT
                    )
                    & models.Q(amount__gt=0)
                ),
                name="promotions_cashback_amount_valid",
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
            errors["amount"] = (
                "Cashback transaction amount must be greater than 0."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"CashbackTransaction #{self.pk} "
            f"{self.transaction_type} {self.amount}"
        )
