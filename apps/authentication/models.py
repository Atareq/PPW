from django.contrib.auth.models import AbstractUser
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from apps.general.models import BaseModel
from apps.authentication.constants import  PhoneCode, AppPermissions


# ------------------------------
# User / Identity model
# ------------------------------

class UserAccount(AbstractUser, BaseModel):
    phone_code = models.CharField(max_length=6,choices=PhoneCode.MAP)
    phone_number = PhoneNumberField(
        unique=True,
        db_index=True
    )
    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []
    permissions = models.JSONField(default=list, blank=True)

    @property
    def active_profile(self):
        """Return the currently active profile for this user."""
        return self.owned_profiles.filter(is_active=True).first()

    @property
    def role(self):
        """Return the type of the active profile."""
        profile = self.active_profile
        return profile.type if profile else None

    @property
    def is_activated(self):
        """Check if the active profile is activated."""
        profile = self.active_profile
        return profile.activated if profile else False
    
    #---------- Permission API ----------

    def get_permissions(self) -> set[str]:
        return set(self.permissions or [])

    def has_app_permission(self, perm: str) -> bool:
        return perm in self.get_permissions()

    def set_permissions(self, perms: list[str]):
        self._validate_permissions(perms)
        self.permissions = list(set(perms))  # remove duplicates

    def add_permission(self, perm: str):
        self._validate_permissions([perm])
        perms = self.get_permissions()
        perms.add(perm)
        self.permissions = list(perms)

    def remove_permission(self, perm: str):
        perms = self.get_permissions()
        if perm in perms:
            perms.remove(perm)
            self.permissions = list(perms)

    def _validate_permissions(self, perms: list[str]):
        invalid = set(perms) - AppPermissions.ALL
        if invalid:
            raise ValueError(f"Invalid permissions: {invalid}")