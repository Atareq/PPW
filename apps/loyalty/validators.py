from rest_framework.exceptions import ValidationError

from apps.loyalty.constants import BADGE_DEFINITIONS, BADGE_ELIGIBLE_ANY
from django.utils.translation import gettext_lazy as _


class PointValidator:
    @staticmethod
    def validate_profile_belongs_to_user(profile, user):
        if profile is None:
            return None

        if not getattr(profile, "pk", None):
            raise ValidationError({"profile": _("Profile was not found.")})

        user_id = getattr(user, "pk", user)
        if profile.user_id != user_id:
            raise ValidationError({"profile": _("Profile must belong to the user.")})

        return profile

    @staticmethod
    def validate_points(points):
        if points is None or int(points) <= 0:
            raise ValidationError({"points": _("Points must be greater than 0.")})
        return int(points)

    @staticmethod
    def validate_reason(reason):
        if not str(reason or "").strip():
            raise ValidationError({"reason": _("Reason is required.")})
        return reason


class BadgeValidator:
    @staticmethod
    def validate_badge_eligibility(profile, badge_type):
        definition = BADGE_DEFINITIONS[badge_type]
        eligible_type = definition["eligible_profile_type"]
        if eligible_type not in {BADGE_ELIGIBLE_ANY, profile.type}:
            raise ValidationError(
                {"badge_type": _("Badge is not eligible for this profile type.")}
            )
        return True
