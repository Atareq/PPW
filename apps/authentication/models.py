from django.contrib.auth.models import AbstractUser
from django.core.exceptions import PermissionDenied
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

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

    # --------- Permission API -------------

    def get_manager_permissions(self) -> set[str]:
        if self.is_superuser:
            from apps.users.manager_permissions import (
                get_manager_permission_codes,
            )

            return get_manager_permission_codes()

        try:
            active_profile = self.active_profile
        except PermissionDenied:
            return set()

        if active_profile.type != "Manager":
            return set()

        return set(getattr(active_profile, "permissions", []) or [])

    def has_app_permission(self, perm: str) -> bool:
        from apps.users.services import ManagerPermissionService

        if self.is_superuser:
            return True

        try:
            active_profile = self.active_profile
        except PermissionDenied:
            return False

        return ManagerPermissionService.has_permission(active_profile, perm)

    def set_permissions(self, perms: list[str]):
        from apps.users.constants import ProfileType
        from apps.users.services import ManagerPermissionService

        active_profile = self.active_profile
        if active_profile.type != ProfileType.MANAGER:
            raise ValueError("Active profile is not a manager profile.")
        active_profile.permissions = ManagerPermissionService.normalize_permissions(
            perms
        )
        active_profile.save(update_fields=["permissions"])

    def add_permission(self, perm: str):
        from apps.users.services import ManagerPermissionService

        perms = self.get_manager_permissions()
        perms.add(perm)
        self.set_permissions(
            ManagerPermissionService.normalize_permissions(list(perms))
        )

    def remove_permission(self, perm: str):
        perms = self.get_manager_permissions()
        if perm in perms:
            perms.remove(perm)
            self.set_permissions(list(perms))

    def _validate_permissions(self, perms: list[str]):
        from apps.users.manager_permissions import validate_manager_permissions

        validate_manager_permissions(perms)
