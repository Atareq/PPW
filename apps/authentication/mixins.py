class PhoneValidationMixin:
    def normalize_phone(self, attrs):
        from rest_framework import serializers
        from apps.authentication.constants import PhoneCode
        from phonenumber_field.phonenumber import to_python

        code = attrs.pop("phone_code", None)
        number = attrs.get("phone_number")

        if code not in PhoneCode.MAP:
            raise serializers.ValidationError(
                {"phone_code": "Invalid phone code."}
            )

        full_number = f"{code}{number}"
        phone = to_python(full_number)

        if not phone.is_valid():
            raise serializers.ValidationError(
                {"phone_number": "Invalid phone number"}
            )

        attrs["phone_number"] = phone
        return attrs
