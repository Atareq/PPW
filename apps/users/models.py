from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, UniqueConstraint

from apps.authentication.models import UserAccount
from apps.general.models import BaseModel
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.manager_permissions import normalize_manager_permissions


class Profile(BaseModel):
    user = models.ForeignKey(
        UserAccount,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="profiles",
    )

    type = models.CharField(max_length=20, choices=ProfileType.choices)
    activated = models.BooleanField(
        default=True,  # For development reasons
        help_text="Only updated to True by admin once the profile created",
    )
    is_active = models.BooleanField(
        default=False, help_text="Indecate which profile is the main now"
    )
    is_valid = models.BooleanField(
        default=True,
        help_text='will be "False" if profile is under punishment',
    )
    national_id = models.CharField(max_length=50, null=True, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        unique_together = ("user", "type")
        constraints = [
            UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="unique_active_profile_per_user",
            )
        ]

    def save(self, *args, **kwargs):
        if isinstance(self, SellerProfile):
            self.type = ProfileType.SELLER
        elif isinstance(self, CustomerProfile):
            self.type = ProfileType.CUSTOMER
        elif isinstance(self, ManagerProfile):
            self.type = ProfileType.MANAGER
            self.permissions = normalize_manager_permissions(
                getattr(self, "permissions", None)
            )

        self.clean()

        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not self.user_id or not self.type:
            return

        live_profiles = Profile.all_objects.filter(user_id=self.user_id).exclude(
            pk=self.pk
        )
        other_live_types = set(
            live_profiles.filter(deleted__isnull=True).values_list("type", flat=True)
        )

        if self.type == ProfileType.MANAGER and (
            ProfileType.CUSTOMER in other_live_types
            or ProfileType.SELLER in other_live_types
        ):
            raise ValidationError(
                {
                    "user": [
                        "Manager accounts cannot coexist with customer or seller profiles."
                    ]
                }
            )

        if self.type in {ProfileType.CUSTOMER, ProfileType.SELLER} and (
            ProfileType.MANAGER in other_live_types
        ):
            raise ValidationError(
                {
                    "user": [
                        "Customer or seller profiles cannot be created for a manager account."
                    ]
                }
            )

    def __str__(self):
        return f" #{self.pk} - {self.type} - {self.user.username}"


class SellerProfile(Profile):
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    nickname = models.CharField(max_length=150, blank=True)
    recoded_voice = models.CharField(max_length=150, blank=True)
    country = models.CharField(max_length=2, choices=CountryOptions.choices)
    currency = models.CharField(max_length=3, choices=CurrencyOptions.choices)
    avg_rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    ratings_count = models.PositiveIntegerField(default=0)


class CustomerProfile(Profile):
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    cashback_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    country = models.CharField(max_length=2, choices=CountryOptions.choices)
    currency = models.CharField(max_length=3, choices=CurrencyOptions.choices)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(cashback_balance__gte=0),
                name="users_customer_cashback_balance_gte_0",
            ),
        ]


class ManagerProfile(Profile):
    permissions = models.JSONField(default=list, blank=True)

    def clean(self):
        super().clean()
        self.permissions = normalize_manager_permissions(self.permissions)


class ProfileBalanceTransactionType(models.TextChoices):
    MANAGER_ADJUSTMENT = "manager_adjustment", "Manager Adjustment"
    SYSTEM_CREDIT = "system_credit", "System Credit"
    SYSTEM_DEBIT = "system_debit", "System Debit"


class ProfileBalanceTransaction(BaseModel):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.PROTECT,
        related_name="balance_transactions",
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=ProfileBalanceTransactionType.choices,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    adjusted_by = models.ForeignKey(
        ManagerProfile,
        on_delete=models.SET_NULL,
        related_name="profile_balance_adjustments",
        null=True,
        blank=True,
    )
    note = models.TextField(blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["profile", "-created"],
                name="profile_bal_txn_prof_idx",
            ),
            models.Index(
                fields=["transaction_type"],
                name="profile_bal_txn_type_idx",
            ),
            models.Index(
                fields=["source_type", "source_id"],
                name="profile_bal_txn_src_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=~Q(amount=0),
                name="profile_balance_txn_amount_not_zero_ck",
            ),
            models.CheckConstraint(
                check=Q(balance_after__gte=0),
                name="profile_balance_txn_after_gte_0_ck",
            ),
        ]
