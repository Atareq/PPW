from rest_framework.exceptions import ValidationError

from apps.users.constants import ProfileType
from django.utils.translation import gettext_lazy as _


class PaymentRequestValidator:
    @staticmethod
    def ensure_customer(profile):
        if getattr(profile, "type", None) != ProfileType.CUSTOMER:
            raise ValidationError(_("Only customer profiles can create payment requests."))

    @staticmethod
    def ensure_seller(profile):
        if getattr(profile, "type", None) != ProfileType.SELLER:
            raise ValidationError(_("Only seller profiles can manage payment requests."))
