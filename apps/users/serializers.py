from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from apps.authentication.models import UserAccount
from apps.users.constants import StaffRole
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile


# ---------------- Dashboard / Admin Serializers ----------------

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


class ManagerProfileadminSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerProfile
        exclude = ["type", "user"]


# ---------------- Staff Management Serializers ----------------

class StaffMemberListSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = ManagerProfile
        fields = [
            'id', 'username', 'phone_number',
            'staff_role', 'is_active', 'is_valid', 'activated',
            'created', 'modified',
        ]
        read_only_fields = fields


class StaffMemberCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    phone_number = serializers.CharField(max_length=20)
    password = serializers.CharField(write_only=True)
    staff_role = serializers.ChoiceField(choices=StaffRole.choices)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_phone_number(self, value):
        if UserAccount.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("A user with this phone number already exists.")
        return value

    def validate_username(self, value):
        if UserAccount.objects.filter(username=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        staff_role = validated_data.pop('staff_role')
        user = UserAccount.objects.create_user(
            username=validated_data['username'],
            phone_number=validated_data['phone_number'],
            password=validated_data['password'],
            is_staff=True,
        )
        profile = ManagerProfile.objects.create(
            user=user,
            is_active=True,
            activated=True,
            staff_role=staff_role,
        )
        return profile


class StaffMemberUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerProfile
        fields = ['staff_role', 'is_valid']

    def validate(self, attrs):
        instance = self.instance
        if instance and instance.staff_role == StaffRole.SUPER_ADMIN:
            new_role = attrs.get('staff_role', instance.staff_role)
            if new_role != StaffRole.SUPER_ADMIN:
                remaining = ManagerProfile.objects.filter(
                    staff_role=StaffRole.SUPER_ADMIN,
                    is_valid=True,
                    deleted__isnull=True,
                ).exclude(pk=instance.pk).count()
                if remaining == 0:
                    raise serializers.ValidationError(
                        "Cannot change the role of the last Super Admin."
                    )
        return attrs


# ---------------- Mobile Serializers ----------------

class SellerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)
    avatar_url = serializers.URLField(source='photo', read_only=False, allow_blank=True, required=False)

    class Meta:
        model = SellerProfile
        fields = [
            'id', 'type', 'nickname', 'description', 'username', 'phone_number',
            'country', 'currency', 'recoded_voice',
            'avg_rating', 'ratings_count',
            'activated', 'is_active', 'is_valid', 'created',
            'avatar_url',
        ]
        read_only_fields = [
            'id', 'type', 'username', 'phone_number',
            'avg_rating', 'ratings_count',
            'activated', 'is_active', 'is_valid', 'created',
        ]


class CustomerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)
    avatar_url = serializers.URLField(source='photo', read_only=False, allow_blank=True, required=False)

    class Meta:
        model = CustomerProfile
        exclude = ['user', 'balance']
        read_only_fields = [
            'id', 'type', 'username', 'phone_number',
            'activated', 'is_active', 'is_valid',
            'cashback_balance', 'created', 'modified', 'deleted',
        ]
