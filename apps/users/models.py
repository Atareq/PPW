from django.db import models
from django.db.models import Q, UniqueConstraint

from apps.general.models import BaseModel
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType

# Still needs to add the rest of data for the two profile types
# need to make sure the flutter will upload to s3 and then send key 
# for the profile picture for backend to store


class Profile(BaseModel):
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        db_index=True,
        related_name="profiles",
    )

    type = models.CharField(max_length=20, choices=ProfileType.choices)
    activated = models.BooleanField(
        default=False
    )  # Only updated to True by admin once the profile created
    is_active = models.BooleanField(
        default=False
    )  # Indecate which profile is the main now

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
        return f"{self.user.username} - {self.type}"


class SellerProfile(Profile):
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    nickname = models.CharField(max_length=150, blank=True)
    recoded_voice = models.CharField(max_length=150, blank=True)
    country = models.CharField(max_length=2, choices=CountryOptions.choices)
    currency = models.CharField(max_length=3, choices=CurrencyOptions.choices)


class CustomerProfile(Profile):
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    country = models.CharField(max_length=2, choices=CountryOptions.choices)
    currency = models.CharField(max_length=3, choices=CurrencyOptions.choices)


class ManagerProfile(Profile):
    pass
