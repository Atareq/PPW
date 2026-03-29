from django.db import models
from django.db.models import Q, UniqueConstraint

from apps.general.models import OwnedEntityModel
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType

# Still needs to add the rest of data for the two profile types
# need to make sure the flutter will upload to s3 and then send key for the profile picture for backend to store


class Profile(OwnedEntityModel):

    country = models.CharField(max_length=2, choices=CountryOptions.choices)
    currency = models.CharField(max_length=3, choices=CurrencyOptions.choices)

    type = models.CharField(max_length=20, choices=ProfileType.choices)
    activated = models.BooleanField(
        default=False
    )  # Only updated to True by admin once the profile created
    is_active = models.BooleanField(
        default=False
    )  # Indecate which profile is the main now

    class Meta:
        unique_together = ("owner", "type")
        constraints = [
            UniqueConstraint(
                fields=["owner"],
                condition=Q(is_active=True),
                name="unique_active_profile_per_owner",
            )
        ]

    def save(self, *args, **kwargs):
        if isinstance(self, SellerProfile):
            self.type = ProfileType.SELLER
        elif isinstance(self, CustomerProfile):
            self.type = ProfileType.CUSTOMER

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.owner.username} - {self.type}"


class SellerProfile(Profile):
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )
    nickname = models.CharField(max_length=150, blank=True)
    recoded_voice =  models.CharField(max_length=150, blank=True)


class CustomerProfile(Profile):
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00
    )

class ManagerProfile(Profile):
    pass
