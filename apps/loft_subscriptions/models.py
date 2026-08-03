from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel, OwnerBaseModel
from apps.loft_subscriptions.constants import (
    INCLUDED_AI_OPERATIONS_PER_MONTH,
    LOFT_SUBSCRIPTION_CURRENCY,
)


class SubscriptionDurationOption(BaseModel):
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=150)
    duration_days = models.PositiveIntegerField(null=True, blank=True)
    duration_months = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=3, default=LOFT_SUBSCRIPTION_CURRENCY)
    is_trial = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["is_active", "sort_order"])]
        constraints = [
            models.CheckConstraint(
                check=Q(price__gte=0),
                name="loft_sub_duration_price_gte_0",
            ),
            models.CheckConstraint(
                check=(
                    Q(duration_days__isnull=False, duration_months__isnull=True)
                    | Q(duration_days__isnull=True, duration_months__isnull=False)
                ),
                name="loft_sub_duration_exactly_one_period",
            ),
        ]

    def clean(self):
        if (self.duration_days is None) == (self.duration_months is None):
            raise ValidationError(
                _("Exactly one of duration_days or duration_months must be set.")
            )
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": _("Price must not be negative.")})

    def __str__(self):
        return f"{self.name} ({self.code})"


class PedigreeTemplatePackage(BaseModel):
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=150)
    template_limit = models.PositiveIntegerField(null=True, blank=True)
    is_unlimited = models.BooleanField(default=False)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=3, default=LOFT_SUBSCRIPTION_CURRENCY)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["is_active", "sort_order"])]
        constraints = [
            models.CheckConstraint(
                check=Q(price__gte=0),
                name="loft_sub_template_pkg_price_gte_0",
            ),
            models.CheckConstraint(
                check=(
                    Q(is_unlimited=True, template_limit__isnull=True)
                    | Q(is_unlimited=False, template_limit__gt=0)
                ),
                name="loft_sub_template_pkg_limit_valid",
            ),
        ]

    def clean(self):
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": _("Price must not be negative.")})
        if self.is_unlimited and self.template_limit is not None:
            raise ValidationError(
                {"template_limit": _("Unlimited packages cannot have a limit.")}
            )
        if not self.is_unlimited and not self.template_limit:
            raise ValidationError(
                {"template_limit": _("Limited packages must have a positive limit.")}
            )

    def __str__(self):
        return f"{self.name} ({self.code})"


class SubscriptionAddon(BaseModel):
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=3, default=LOFT_SUBSCRIPTION_CURRENCY)
    is_active = models.BooleanField(default=True)
    is_implemented = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["is_active", "is_implemented", "sort_order"])]
        constraints = [
            models.CheckConstraint(
                check=Q(price__gte=0),
                name="loft_sub_addon_price_gte_0",
            ),
        ]

    def clean(self):
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": _("Price must not be negative.")})

    def __str__(self):
        return f"{self.name} ({self.code})"


class AIQuotaPackage(BaseModel):
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=150)
    operation_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=3, default=LOFT_SUBSCRIPTION_CURRENCY)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["is_active", "sort_order"])]
        constraints = [
            models.CheckConstraint(
                check=Q(operation_count__gt=0),
                name="loft_sub_ai_pkg_ops_gt_0",
            ),
            models.CheckConstraint(
                check=Q(price__gte=0),
                name="loft_sub_ai_pkg_price_gte_0",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class SubscriptionInvoiceStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    CANCELLED = "cancelled", _("Cancelled")
    EXPIRED = "expired", _("Expired")
    REFUNDED = "refunded", _("Refunded")


class SubscriptionInvoice(OwnerBaseModel):
    status = models.CharField(
        max_length=20,
        choices=SubscriptionInvoiceStatus.choices,
        default=SubscriptionInvoiceStatus.PENDING,
    )
    currency = models.CharField(max_length=3, default=LOFT_SUBSCRIPTION_CURRENCY)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    duration = models.ForeignKey(
        SubscriptionDurationOption,
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    pedigree_package = models.ForeignKey(
        PedigreeTemplatePackage,
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    ai_package = models.ForeignKey(
        AIQuotaPackage,
        on_delete=models.PROTECT,
        related_name="invoices",
        null=True,
        blank=True,
    )
    selected_template_ids = models.JSONField(default=list, blank=True)
    addon_codes = models.JSONField(default=list, blank=True)
    selection_snapshot = models.JSONField(default=dict, blank=True)
    pricing_snapshot = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["owner", "-created"], name="loft_inv_owner_created_idx"
            ),
            models.Index(fields=["status"], name="loft_inv_status_idx"),
            models.Index(fields=["expires_at"], name="loft_inv_expires_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(subtotal__gte=0),
                name="loft_inv_subtotal_gte_0",
            ),
            models.CheckConstraint(
                check=Q(total__gte=0),
                name="loft_inv_total_gte_0",
            ),
        ]

    def __str__(self):
        return f"SubscriptionInvoice #{self.pk} ({self.status})"


class SubscriptionInvoiceLine(BaseModel):
    invoice = models.ForeignKey(
        SubscriptionInvoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item_type = models.CharField(max_length=40)
    item_code = models.CharField(max_length=80)
    item_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_amount = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["invoice", "item_type"])]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0),
                name="loft_inv_line_qty_gt_0",
            ),
            models.CheckConstraint(
                check=Q(unit_amount__gte=0),
                name="loft_inv_line_unit_gte_0",
            ),
            models.CheckConstraint(
                check=Q(total_amount__gte=0),
                name="loft_inv_line_total_gte_0",
            ),
        ]

    def __str__(self):
        return f"SubscriptionInvoiceLine #{self.pk} ({self.item_code})"


class LoftSubscriptionStatus(models.TextChoices):
    SCHEDULED = "scheduled", _("Scheduled")
    ACTIVE = "active", _("Active")
    EXPIRED = "expired", _("Expired")
    CANCELLED = "cancelled", _("Cancelled")


class LoftSubscription(OwnerBaseModel):
    invoice = models.OneToOneField(
        SubscriptionInvoice,
        on_delete=models.PROTECT,
        related_name="subscription",
    )
    status = models.CharField(
        max_length=20,
        choices=LoftSubscriptionStatus.choices,
        default=LoftSubscriptionStatus.ACTIVE,
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_trial = models.BooleanField(default=False)
    pedigree_template_limit = models.PositiveIntegerField(null=True, blank=True)
    pedigree_unlimited = models.BooleanField(default=False)
    selected_template_ids = models.JSONField(default=list, blank=True)
    addon_codes = models.JSONField(default=list, blank=True)
    included_ai_operations_per_month = models.PositiveIntegerField(
        default=INCLUDED_AI_OPERATIONS_PER_MONTH
    )
    purchased_ai_operations = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["owner", "status"], name="loft_sub_owner_status_idx"),
            models.Index(fields=["starts_at", "ends_at"], name="loft_sub_dates_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner"],
                condition=Q(status=LoftSubscriptionStatus.ACTIVE),
                name="one_active_loft_subscription_per_profile",
            ),
            models.CheckConstraint(
                check=Q(ends_at__gt=models.F("starts_at")),
                name="loft_sub_ends_after_starts",
            ),
        ]

    def __str__(self):
        return f"LoftSubscription #{self.pk} ({self.status})"


class LoftSubscriptionTrialUsage(BaseModel):
    user = models.OneToOneField(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        related_name="loft_subscription_trial_usage",
    )
    invoice = models.OneToOneField(
        SubscriptionInvoice,
        on_delete=models.PROTECT,
        related_name="trial_usage",
    )
    subscription = models.OneToOneField(
        LoftSubscription,
        on_delete=models.PROTECT,
        related_name="trial_usage",
    )
    consumed_at = models.DateTimeField()

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"LoftSubscriptionTrialUsage #{self.pk} user={self.user_id}"


class SubscriptionPaymentRequestStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    CANCELLED = "cancelled", _("Cancelled")


class SubscriptionPaymentRequest(BaseModel):
    invoice = models.OneToOneField(
        SubscriptionInvoice,
        on_delete=models.PROTECT,
        related_name="payment_request",
    )
    owner = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="loft_subscription_payment_requests",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=SubscriptionPaymentRequestStatus.choices,
        default=SubscriptionPaymentRequestStatus.PENDING,
    )
    buyer_note = models.TextField(blank=True)
    payment_proof = models.FileField(
        upload_to="subscription_payment_proofs/",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_loft_subscription_payment_requests",
    )

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["owner", "-created"], name="loft_pay_owner_created_idx"
            ),
            models.Index(fields=["status"], name="loft_pay_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gte=0),
                name="loft_pay_amount_gte_0",
            ),
        ]

    def __str__(self):
        return f"SubscriptionPaymentRequest #{self.pk} ({self.status})"


class SubscriptionAIUsage(BaseModel):
    subscription = models.ForeignKey(
        LoftSubscription,
        on_delete=models.CASCADE,
        related_name="ai_usage",
    )
    owner = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="loft_subscription_ai_usage",
    )
    operation_code = models.CharField(max_length=100)
    idempotency_key = models.CharField(max_length=255, unique=True)
    quota_type = models.CharField(
        max_length=20,
        choices=[("included", _("Included")), ("purchased", _("Purchased"))],
    )
    allowance_period_start = models.DateTimeField(null=True, blank=True)
    allowance_period_end = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["subscription", "quota_type"], name="loft_ai_sub_type_idx"
            ),
            models.Index(
                fields=["owner", "-created"], name="loft_ai_owner_created_idx"
            ),
        ]

    def __str__(self):
        return f"SubscriptionAIUsage #{self.pk} ({self.quota_type})"
