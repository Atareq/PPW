from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.constants import ProfileType
from apps.users.models import Profile


def switch_user_profile(user, new_profile_type: str) -> tuple[bool, str]:
    """Switch by profile type — targets the earliest-created profile of that type."""

    with transaction.atomic():
        if user.role == new_profile_type and new_profile_type != ProfileType.MANAGER:
            return False, None

        Profile.objects.filter(user=user, is_active=True).update(is_active=False)
        profile = (
            Profile.objects.filter(user=user, type=new_profile_type)
            .order_by("created")
            .first()
        )
        if profile:
            profile.is_active = True
            profile.save(update_fields=["is_active"])
            return True, profile.type
        else:
            transaction.set_rollback(True)
            return False, None


def switch_user_profile_by_id(user, profile_id: int) -> tuple[bool, str]:
    """Switch to a specific profile by ID — used for room switching."""

    with transaction.atomic():
        profile = (
            Profile.objects.filter(user=user, id=profile_id)
            .select_for_update()
            .first()
        )
        if not profile:
            return False, None

        Profile.objects.filter(user=user, is_active=True).update(is_active=False)
        profile.is_active = True
        profile.save(update_fields=["is_active"])
        return True, profile.type


def build_token_for_user(user):
    """Build a JWT pair for the user's current valid active profile."""

    return build_token_for_user_role(user, user.active_profile)


def build_token_for_user_role(user, profile):
    """Embed profile state in JWTs so middleware can sync active profiles."""

    token = RefreshToken.for_user(user)
    token["profile"] = profile.type
    token["user_id"] = user.id
    token["is_activated"] = profile.activated

    from apps.users.constants import ProfileType
    if profile.type == ProfileType.MANAGER:
        from apps.users.models import ManagerProfile
        mp = ManagerProfile.objects.filter(pk=profile.pk).values("staff_role").first()
        token["staff_role"] = mp["staff_role"] if mp else ""

    return token
