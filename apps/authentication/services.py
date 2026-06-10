from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.constants import ProfileType
from apps.users.models import Profile


def switch_user_profile(user, new_profile_type: str) -> tuple[bool, str]:
    """Move a non-manager user to another profile type in one transaction."""

    with transaction.atomic():

        if user.role != new_profile_type and user.role != ProfileType.MANAGER:
            Profile.objects.filter(user=user, is_active=True).update(
                is_active=False
            )
            profile = Profile.objects.filter(
                user=user, type=new_profile_type
            ).first()
            if profile:
                profile.is_active = True
                profile.save(update_fields=["is_active"])
                return True, profile.type
            else:
                transaction.set_rollback(True)
                return False, None
        else:
            return False, None


def build_token_for_user(user):
    """Build a JWT pair for the user's current valid active profile."""

    return build_token_for_user_role(user, user.active_profile)


def build_token_for_user_role(user, profile):
    """Embed profile state in JWTs so middleware can sync active profiles."""

    token = RefreshToken.for_user(user)
    token["profile"] = profile.type
    token["user_id"] = user.id
    token["is_activated"] = profile.activated
    return token
