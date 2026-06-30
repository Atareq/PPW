from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from apps.loyalty.constants import RewardSourceType
from apps.loyalty.services import PointService
from apps.referrals.models import LinkSharingCode
from apps.users.models import Profile


REFERRAL_CODE_TTL_HOURS = 24
REFERRAL_NEW_ACCOUNT_WINDOW_HOURS = 2
REFERRAL_INVITER_POINTS = 30


class ReferralService:
    """Referral code lifecycle and inviter PP Coin reward application."""

    @staticmethod
    @transaction.atomic
    def create_code(owner_profile):
        """Create a short-lived share code for the active profile."""

        owner = Profile.objects.select_related("user").get(pk=owner_profile.pk)
        expires_at = timezone.now() + timezone.timedelta(hours=REFERRAL_CODE_TTL_HOURS)
        for _ in range(5):
            try:
                code = LinkSharingCode.objects.create(
                    owner_profile=owner,
                    code=uuid4().hex[:12],
                    expires_at=expires_at,
                )
                return code
            except IntegrityError:
                continue
        raise ValidationError({"code": _("Could not create a unique referral code.")})

    @staticmethod
    @transaction.atomic
    def redeem_code(active_profile, raw_code):
        """Redeem one locked code for a new account and reward the inviter once."""

        code_value = str(raw_code or "").strip()
        if not code_value:
            raise ValidationError({"code": _("This field is required.")})

        redeemer = Profile.objects.select_related("user").get(pk=active_profile.pk)
        account_age = timezone.now() - redeemer.user.date_joined
        if account_age > timezone.timedelta(hours=REFERRAL_NEW_ACCOUNT_WINDOW_HOURS):
            raise ValidationError(
                {"code": _("Referral code is only valid for new accounts.")}
            )

        try:
            code = (
                LinkSharingCode.objects.select_for_update()
                .select_related("owner_profile", "owner_profile__user")
                .get(code=code_value)
            )
        except LinkSharingCode.DoesNotExist as exc:
            raise ValidationError({"code": _("Referral code was not found.")}) from exc

        if code.is_used:
            raise ValidationError({"code": _("Referral code was already used.")})
        if code.expires_at <= timezone.now():
            raise ValidationError({"code": _("Referral code has expired.")})
        if (
            code.owner_profile_id == redeemer.pk
            or code.owner_profile.user_id == redeemer.user_id
        ):
            raise ValidationError({"code": _("Cannot redeem your own referral code.")})

        code.is_used = True
        code.used_by_profile = redeemer
        code.used_at = timezone.now()
        code.save(update_fields=["is_used", "used_by_profile", "used_at", "modified"])

        PointService.earn_points(
            user=code.owner_profile.user,
            profile=code.owner_profile,
            points=REFERRAL_INVITER_POINTS,
            reason="Referral accepted",
            source_type=RewardSourceType.REFERRAL_ACCEPTED,
            source_id=str(code.pk),
            idempotency_key=(
                f"points:referral:{code.pk}:inviter:{code.owner_profile_id}"
            ),
            metadata={"referral_code": code.code, "used_by_profile_id": redeemer.pk},
        )
        return code

    @staticmethod
    def share_url(code):
        return f"{settings.PUBLIC_FRONTEND_BASE_URL}/referrals/{code.code}"
