from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.constants import ProfileType
from apps.users.models import Profile


def switch_user_profile(user, new_profile_type: str) -> tuple[bool, str]:
    """Move a non-manager user to another profile type in one transaction."""

    with transaction.atomic():
        if user.role == ProfileType.MANAGER:
            return False, None

        if new_profile_type == ProfileType.MANAGER:
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
    token["user_id"] = user.id
    token["is_activated"] = profile.activated
    return token


def notify_managers_new_user_registered(user, profile):
    """Notify active managers after a new customer account is committed."""

    from apps.notifications.models import NotificationType
    from apps.notifications.services import NotificationService
    from apps.users.models import ManagerProfile

    def _notify():
        manager_ids = ManagerProfile.objects.filter(
            is_active=True,
            activated=True,
            is_valid=True,
            deleted__isnull=True,
        ).values_list("id", flat=True)
        for manager_id in manager_ids:
            NotificationService.safe_create_notification(
                recipient_profile=manager_id,
                notification_type=NotificationType.NEW_USER_REGISTERED,
                title=_("New user registered"),
                body=_("A new user registered in Pigeon Planet."),
                object_type="user",
                object_id=user.pk,
                metadata={
                    "user_id": user.pk,
                    "profile_id": profile.pk,
                    "profile_type": profile.type,
                    "phone_number": str(user.phone_number),
                },
                idempotency_key=f"new_user_registered:{user.pk}:{profile.pk}",
            )

    transaction.on_commit(_notify)
