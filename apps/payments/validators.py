from rest_framework.exceptions import ValidationError

from apps.users.constants import ProfileType


class PaymentRequestValidator:
    @staticmethod
    def ensure_customer(profile):
        if getattr(profile, "type", None) != ProfileType.CUSTOMER:
            raise ValidationError("Only customer profiles can create payment requests.")

    @staticmethod
    def ensure_seller(profile):
        if getattr(profile, "type", None) != ProfileType.SELLER:
            raise ValidationError("Only seller profiles can manage payment requests.")
