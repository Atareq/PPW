from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, UniqueConstraint

from apps.authentication.models import UserAccount
from apps.general.models import BaseModel
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType


class Profile(BaseModel):
    user = models.ForeignKey(
        UserAccount,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="profiles",
    )

    type = models.CharField(max_length=20, choices=ProfileType.choices)
    photo = models.URLField(max_length=500, blank=True, default="")
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

        super().save(*args, **kwargs)

    def __str__(self):
        return f" #{self.pk} - {self.type} - {self.user.username}"


class SellerProfile(Profile):
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
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
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
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
    pass
