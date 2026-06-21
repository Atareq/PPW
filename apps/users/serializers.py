from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from apps.authentication.models import UserAccount
from apps.authentication.mixins import PhoneValidationMixin
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile
from apps.users.services import ManagerPermissionService


# ---------------- Dashboard Serializer ----------------
class SellerProfileAdminSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = SellerProfile
        fields = "__all__"


class CustomerProfileAdminSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = CustomerProfile
        fields = "__all__"


# ---------------- Mobile Serializer ----------------
class SellerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = SellerProfile
        fields = [
            'id', 'type', 'nickname', 'username', 'phone_number',
            'country', 'currency', 'recoded_voice',
            'avg_rating', 'ratings_count',
            'activated', 'is_active', 'is_valid', 'created',
        ]
        read_only_fields = [
            'id', 'type', 'username', 'phone_number',
            'avg_rating', 'ratings_count',
            'activated', 'is_active', 'is_valid', 'created',
        ]


class CustomerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = CustomerProfile
        exclude = ['user', 'balance']
        read_only_fields = [
            'id', 'type', 'username', 'phone_number',
            'activated', 'is_active', 'is_valid',
            'cashback_balance', 'created', 'modified', 'deleted',
        ]


class ManagerProfileadminSerializer(serializers.ModelSerializer):
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = ManagerProfile
        exclude = ["type", "user"]

    def validate_permissions(self, value):
        return ManagerPermissionService.normalize_permissions(value)


class ManagerProfileAdminDetailSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    phone_number = serializers.CharField(
        source="user.phone_number", read_only=True
    )
    email = serializers.EmailField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)

    class Meta:
        model = ManagerProfile
        fields = [
            "id",
            "username",
            "phone_number",
            "email",
            "first_name",
            "last_name",
            "permissions",
            "activated",
            "is_active",
            "is_valid",
            "created",
            "modified",
        ]


class ManagerAdminCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = UserAccount
        fields = [
            "username",
            "phone_number",
            "email",
            "first_name",
            "last_name",
            "password",
            "permissions",
        ]

    def validate_password(self, value):
        user = UserAccount(
            username=self.initial_data.get("username", ""),
            phone_number=self.initial_data.get("phone_number", ""),
            email=self.initial_data.get("email", ""),
        )
        validate_password(password=value, user=user)
        return value

    def validate_permissions(self, value):
        return ManagerPermissionService.normalize_permissions(value)

    def create(self, validated_data):
        permissions = validated_data.pop("permissions", [])
        with transaction.atomic():
            user = UserAccount.objects.create_user(
                **validated_data,
                is_staff=True,
            )
            return ManagerProfile.objects.create(
                user=user,
                is_active=True,
                activated=True,
                permissions=permissions,
            )


class ManagerAdminUpdateSerializer(
    PhoneValidationMixin, serializers.Serializer
):
    username = serializers.CharField(required=False)
    phone_number = serializers.CharField(required=False)
    region = serializers.CharField(required=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    activated = serializers.BooleanField(required=False)
    is_valid = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if "phone_number" in attrs:
            region = attrs.get("region")
            if region:
                attrs = self.validate_phone(attrs)
        return attrs

    def validate_phone_number(self, value):
        instance = getattr(self, "instance", None)
        queryset = UserAccount.objects.filter(phone_number=value)
        if instance is not None:
            queryset = queryset.exclude(pk=instance.user_id)
        if queryset.exists():
            raise serializers.ValidationError(
                "user account with this phone number already exists."
            )
        return value

    def update(self, instance, validated_data):
        user = instance.user
        user_fields = {"username", "phone_number", "email", "first_name", "last_name"}
        profile_fields = {"activated", "is_valid", "is_active"}

        for field in user_fields:
            if field in validated_data:
                setattr(user, field, validated_data[field])

        for field in profile_fields:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        with transaction.atomic():
            if any(field in validated_data for field in user_fields):
                user.save()
            if any(field in validated_data for field in profile_fields):
                instance.save()
        return instance


class ManagerPermissionUpdateSerializer(serializers.Serializer):
    permissions = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=True,
    )

    def validate_permissions(self, value):
        return ManagerPermissionService.normalize_permissions(value)

    def update(self, instance, validated_data):
        instance.permissions = validated_data["permissions"]
        instance.save(update_fields=["permissions"])
        return instance
