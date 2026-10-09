from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError


class CompletedDealValidator:
    @staticmethod
    def validate_parties(*, buyer_user, buyer_profile, seller_user, seller_profile):
        """Profiles are deal modes; they must belong to the recorded accounts."""

        if buyer_profile.user_id != buyer_user.pk:
            raise ValidationError(
                {"buyer_profile": _("Buyer profile must belong to the buyer account.")}
            )
        if seller_profile.user_id != seller_user.pk:
            raise ValidationError(
                {
                    "seller_profile": _(
                        "Seller profile must belong to the seller account."
                    )
                }
            )
