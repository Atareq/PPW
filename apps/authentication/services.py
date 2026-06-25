from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.constants import ProfileType
from apps.users.models import Profile


def switch_user_profile(
    user,
    new_profile_type: str = None,
    profile_id: int = None,
) -> tuple[bool, str]:
    """Switch active profile either by specific ID (for seller rooms) or by type."""

    with transaction.atomic():
        if user.role == ProfileType.MANAGER:
            return False, None

        if profile_id is not None:
            profile = Profile.objects.filter(
                pk=profile_id,
                user=user,
                activated=True,
                is_valid=True,
                deleted__isnull=True,
            ).first()
            if not profile or profile.type == ProfileType.MANAGER:
                return False, None
            Profile.objects.filter(user=user, is_active=True).update(is_active=False)
            profile.is_active = True
            profile.save(update_fields=["is_active"])
            return True, profile.type

        if new_profile_type is None or new_profile_type == ProfileType.MANAGER:
            return False, None

        if user.role != new_profile_type:
            Profile.objects.filter(user=user, is_active=True).update(is_active=False)
            profile = Profile.objects.filter(
                user=user,
                type=new_profile_type,
                activated=True,
                is_valid=True,
                deleted__isnull=True,
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
    token["profile_id"] = profile.id
    token["user_id"] = user.id
    token["is_activated"] = profile.activated
    return token
