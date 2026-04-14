from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.authentication.mixins import PhoneValidationMixin
from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.users.models import CustomerProfile, ManagerProfile
from apps.users.serializers import (
    CustomerProfileMobileSerializer,
    ManagerProfileadminSerializer,
)


class CustomerTokenSerializer(PhoneValidationMixin, TokenObtainPairSerializer):

    phone_number = serializers.CharField()
    password = serializers.CharField(write_only=True)

    @classmethod
    def get_token(cls, user):
        return build_token_for_user(user)


class CustomerRegisterSerializer(
    PhoneValidationMixin, serializers.ModelSerializer
):
    password = serializers.CharField(write_only=True)
    customer_profile = CustomerProfileMobileSerializer()
    region = serializers.CharField(max_length=2, read_only=True)

    class Meta:
        model = UserAccount
        fields = [
            "username",
            "phone_number",
            "region",
            "password",
            "customer_profile",
        ]
        read_only_fields = ["id", "is_staff", "is_superuser", "last_login"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_password(self, value):
        user = UserAccount(
            username=self.initial_data.get("username", ""),
            phone_number=self.initial_data.get("phone_number", ""),
        )

        validate_password(password=value, user=user)
        return value

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
    manager_profile = ManagerProfileadminSerializer()
    region = serializers.CharField(max_length=2)

    region = serializers.CharField(max_length=2, read_only=True)

    class Meta:
        model = UserAccount
        fields = [
            "username",
            "phone_number",
            "region",
            "password",
            "manager_profile",
        ]
        read_only_fields = ["id", "is_staff", "is_superuser", "last_login"]

    def validate_password(self, value):
        user = UserAccount(
            username=self.initial_data.get("username", ""),
            phone_number=self.initial_data.get("phone_number", ""),
        )

        validate_password(password=value, user=user)
        return value

    def create(self, validated_data):
        profile_data = validated_data.pop("manager_profile")

        with transaction.atomic():
            user = UserAccount.objects.create_user(
                **validated_data, is_staff=True
            )
            ManagerProfile.objects.create(
                user=user,
                is_active=True,  # Already made by admins
                activated=True,
                **profile_data
            )

        return user
