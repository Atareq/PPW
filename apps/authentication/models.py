from django.contrib.auth.models import AbstractUser
from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from apps.general.models import BaseModel

# ------------------------------
# User / Identity model
# ------------------------------


class UserAccount(AbstractUser, BaseModel):
    phone_number = PhoneNumberField(unique=True, db_index=True)
    secondary_phone_number = PhoneNumberField(
        null=True,
        blank=True,
        db_index=True,
    )
    identity_data_last_changed_at = models.DateTimeField(null=True, blank=True)
    identity_data_last_changed_by_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    def __str__(self):
        return f" #{self.pk} userAccount{str(self.phone_number)} "

    @property
    def active_profile(self):
        """Return the currently active profile for this user."""
        profile = self.profiles.filter(
            is_active=True,
            is_valid=True,
            activated=True,
            deleted__isnull=True,
        ).first()
        if profile is None:
            raise PermissionDenied(_("No active profile found for this user."))
        from apps.users.constants import ProfileType

        if profile.type == ProfileType.MANAGER:
            return profile.managerprofile
        if profile.type == ProfileType.SELLER:
            return profile.sellerprofile
        if profile.type == ProfileType.CUSTOMER:
            return profile.customerprofile
        return profile

    @property
    def role(self):
        """Return the type of the active profile."""
        return self.active_profile.type

    @property
    def is_activated(self):
        """Check if the active profile is activated."""
        return self.active_profile.activated
