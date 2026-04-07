from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from rest_framework import serializers

from apps.authentication.constants import PhoneCode
from apps.authentication.mixins import PhoneValidationMixin
from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user

from apps.users.models import CustomerProfile, ManagerProfile

from django.db import transaction

from apps.users.serializers import CustomerProfileMobileSerializer, ManagerProfileadminSerializer


class CustomTokenSerializer(PhoneValidationMixin, TokenObtainPairSerializer):

    phone_code = serializers.CharField()
    phone_number = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        attrs = self.normalize_phone(attrs)
        return super().validate(attrs)

    @classmethod
    def get_token(cls, user):
        return build_token_for_user(user)


class CustomerRegisterSerializer(
    PhoneValidationMixin, serializers.ModelSerializer
):
    password = serializers.CharField(write_only=True)
    customer_profile = CustomerProfileMobileSerializer()
    phone_code = serializers.ChoiceField(choices=PhoneCode.MAP)

    class Meta:
        model = UserAccount
        fields = [
            "username",
            "phone_number",
            "phone_code",
            "password",
            "customer_profile",
        ]
        read_only_fields = ["id", "is_staff", "is_superuser", "last_login"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate(self, attrs):
        attrs = self.normalize_phone(attrs)
        return attrs

    def create(self, validated_data):
        profile_data = validated_data.pop("customer_profile")

        with transaction.atomic():
            user = UserAccount.objects.create_user(**validated_data)
            CustomerProfile.objects.create(
                user=user,
                is_active=True,  # The user's first profile
                **profile_data
            )

        return user


class ManagerRegisterSerializer(
    PhoneValidationMixin, serializers.ModelSerializer
):
    password = serializers.CharField(write_only=True)
    manager_profile = ManagerProfileadminSerializer()
    phone_code = serializers.ChoiceField(choices=PhoneCode.MAP)

    class Meta:
        model = UserAccount
        fields = [
            "username",
            "phone_number",
            "phone_code",
            "password",
            "manager_profile",
        ]
        read_only_fields = ["id", "is_staff", "is_superuser", "last_login"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate(self, attrs):
        attrs = self.normalize_phone(attrs)
        return attrs

    def create(self, validated_data):
        profile_data = validated_data.pop("manager_profile")

        with transaction.atomic():
            user = UserAccount.objects.create_user(**validated_data)
            ManagerProfile.objects.create(
                user=user,
                is_active=True,  # Already made by admins
                activated=True,
                **profile_data
            )

        return user
