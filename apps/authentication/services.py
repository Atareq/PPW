from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import Profile


def switch_user_profile(user, new_profile_type: str) -> bool:
    with transaction.atomic():

        if user.role != new_profile_type and user.role != "MANAGER":
            Profile.objects.filter(user=user, is_active=True).update(
                is_active=False
            )
            profile = Profile.objects.filter(
                user=user, type=new_profile_type
            ).first()
            if profile:
                profile.is_active = True
                profile.save(update_fields=["is_active"])
                return True
            else:
                transaction.set_rollback(True)
                return False
        else:
            return False


def build_token_for_user(user):
    profile = Profile.objects.filter(user=user, is_active=True).first()
    if not profile:
        raise ValidationError("No active profile found for this user.")

    return build_token_for_user_role(user, profile)


def build_token_for_user_role(user, profile):
    token = RefreshToken.for_user(user)
    token["profile"] = profile.type
    token["user_id"] = user.id
    token["is_activated"] = profile.activated
    return token
