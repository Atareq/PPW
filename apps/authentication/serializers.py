from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from rest_framework import serializers

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user

from apps.users.models import CustomerProfile

from django.db import transaction

from apps.users.serializers import CustomerProfileMobileSerializer


class CustomTokenSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        return build_token_for_user(user)


class CustomerRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    customer_profile = CustomerProfileMobileSerializer()

    class Meta:
        model = UserAccount
        fields = "__all__"
        read_only_fields = ["id", "is_staff", "is_superuser", "last_login"]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        profile_data = validated_data.pop("customer_profile")

        with transaction.atomic():
            user = UserAccount.objects.create_user(**validated_data)
            CustomerProfile.objects.create(
                owner=user,
                is_active=True,  # The user's first profile
                **profile_data
            )

        return user
