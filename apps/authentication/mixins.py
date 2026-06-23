import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat
from rest_framework import serializers


class PhoneValidationMixin:
    """
    Reusable mixin for validating and normalizing phone numbers.

    Expected serializer fields:
        - phone_number
        - region

    Output:
        - attrs["phone_number"] => normalized E.164 phone number
        - attrs["region"] => normalized uppercase region
    """

    @staticmethod
    def normalize_phone(phone_number: str, region: str) -> str:
        if not phone_number:
            raise ValueError("Phone number is required.")

        if not region:
            raise ValueError("Region is required.")

        phone_number = str(phone_number).strip()
        region = str(region).strip().upper()

        try:
            parsed = phonenumbers.parse(phone_number, region)
        except NumberParseException:
            raise ValueError("Invalid phone number format.")

        if not phonenumbers.is_possible_number(parsed):
            raise ValueError("Phone number is not possible.")

        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("Phone number is not valid.")

        return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)

    def validate_phone(self, attrs: dict) -> dict:
        phone_number = attrs.get("phone_number")
        region = attrs.get("region")

        if not region:
            raise serializers.ValidationError({"region": "Region is required."})

        region = str(region).strip().upper()

        try:
            phone_number_e164 = self.normalize_phone(
                phone_number=phone_number,
                region=region,
            )
        except ValueError as e:
            raise serializers.ValidationError({"phone_number": str(e)})

        attrs["phone_number"] = phone_number_e164
        attrs["region"] = region
        return attrs
