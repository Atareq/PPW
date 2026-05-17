from django.contrib.auth.models import AbstractUser
from django.core.exceptions import PermissionDenied
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from apps.authentication.constants import AppPermissions
from apps.general.models import BaseModel

# ------------------------------
# User / Identity model
# ------------------------------


class UserAccount(AbstractUser, BaseModel):
    phone_number = PhoneNumberField(unique=True, db_index=True)
    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []
    permissions = models.JSONField(default=list, blank=True)

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
            raise PermissionDenied("No active profile found for this user.")
        return profile

    @property
    def role(self):
        """Return the type of the active profile."""
        return self.active_profile.type

    @property
    def is_activated(self):
        """Check if the active profile is activated."""
        return self.active_profile.activated

    # --------- Permission API -------------

    def get_manager_permissions(self) -> set[str]:
        return set(self.permissions or [])

    def has_app_permission(self, perm: str) -> bool:
        return perm in self.get_manager_permissions()

    def set_permissions(self, perms: list[str]):
        self._validate_permissions(perms)
        self.permissions = list(set(perms))  # remove duplicates

    def add_permission(self, perm: str):
        self._validate_permissions([perm])
        perms = self.get_manager_permissions()
        perms.add(perm)
        self.permissions = list(perms)

    def remove_permission(self, perm: str):
        perms = self.get_manager_permissions()
        if perm in perms:
            perms.remove(perm)
            self.permissions = list(perms)

    def _validate_permissions(self, perms: list[str]):
        invalid = set(perms) - AppPermissions.ALL
        if invalid:
            raise ValueError(f"Invalid permissions: {invalid}")
