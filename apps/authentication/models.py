from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.general.models import BaseModel

# ------------------------------
# User / Identity model
# ------------------------------


class UserAccount(AbstractUser, BaseModel):
    USERNAME_FIELD = "username"

    @property
    def active_profile(self):
        """Return the currently active profile for this user."""
        return self.profiles.filter(is_active=True).first()

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
