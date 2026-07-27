from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.authentication.models import UserAccount
from apps.authentication.mixins import PhoneValidationMixin
from apps.loyalty.services import CustomerLevelService, SellerLevelService
from apps.users.models import (
    CustomerProfile,
    ManagerProfile,
    Profile,
    ProfileBalanceTransaction,
    SellerProfile,
)
from apps.users.services import ManagerPermissionService, ProfileBalanceService


class UserSecondaryPhoneMixin(serializers.Serializer):
    secondary_phone_number = serializers.CharField(
        source="user.secondary_phone_number",
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    def create(self, validated_data):
        user_data = validated_data.pop("user", {})
        instance = super().create(validated_data)
        if "secondary_phone_number" in user_data:
            instance.user.secondary_phone_number = (
                user_data["secondary_phone_number"] or None
            )
            instance.user.save(update_fields=["secondary_phone_number"])
        return instance

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        profile_instance = super().update(instance, validated_data)
        if "secondary_phone_number" in user_data:
            profile_instance.user.secondary_phone_number = (
                user_data["secondary_phone_number"] or None
            )
            profile_instance.user.save(update_fields=["secondary_phone_number"])
        return profile_instance


# ---------------- Dashboard Serializer ----------------
class SellerProfileAdminSerializer(UserSecondaryPhoneMixin, serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    successful_invites_count = serializers.SerializerMethodField()
    completed_orders_count = serializers.SerializerMethodField()
    seller_level = serializers.SerializerMethodField()
    seller_level_label = serializers.SerializerMethodField()

    class Meta:
        model = SellerProfile
        fields = "__all__"

    def get_successful_invites_count(self, obj):
        return _successful_invites_count(obj)

    def get_completed_orders_count(self, obj):
        return _completed_seller_orders_count(obj)

    def get_seller_level(self, obj):
        return SellerLevelService.get_level(self.get_completed_orders_count(obj)).code

    def get_seller_level_label(self, obj):
        return SellerLevelService.get_level(self.get_completed_orders_count(obj)).label


class CustomerProfileAdminSerializer(UserSecondaryPhoneMixin, serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    successful_invites_count = serializers.SerializerMethodField()
    completed_orders_count = serializers.SerializerMethodField()
    customer_level = serializers.SerializerMethodField()
    customer_level_label = serializers.SerializerMethodField()
    level_discount_percent = serializers.SerializerMethodField()
    level_cashback_percent = serializers.SerializerMethodField()

    class Meta:
        model = CustomerProfile
        fields = "__all__"

    def get_successful_invites_count(self, obj):
        return _successful_invites_count(obj)

    def get_completed_orders_count(self, obj):
        return _completed_customer_orders_count(obj)

    def get_customer_level(self, obj):
        return CustomerLevelService.get_level(self.get_completed_orders_count(obj)).code

    def get_customer_level_label(self, obj):
        return CustomerLevelService.get_level(
            self.get_completed_orders_count(obj)
        ).label

    def get_level_discount_percent(self, obj):
        return CustomerLevelService.get_discount_percent(
            self.get_completed_orders_count(obj)
        )

    def get_level_cashback_percent(self, obj):
        return CustomerLevelService.get_cashback_percent(
            self.get_completed_orders_count(obj)
        )


# ---------------- Mobile Serializer ----------------
class SellerProfileMobileSerializer(UserSecondaryPhoneMixin, serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    successful_invites_count = serializers.SerializerMethodField()
    completed_orders_count = serializers.SerializerMethodField()
    seller_level = serializers.SerializerMethodField()
    seller_level_label = serializers.SerializerMethodField()

    class Meta:
        model = SellerProfile
        fields = [
            "id",
            "type",
            "nickname",
            "username",
            "phone_number",
            "country",
            "currency",
            "recoded_voice",
            "avg_rating",
            "ratings_count",
            "national_id",
            "address",
            "loft_name",
            "loft_picture",
            "secondary_phone_number",
            "successful_invites_count",
            "completed_orders_count",
            "seller_level",
            "seller_level_label",
            "activated",
            "is_active",
            "is_valid",
            "created",
        ]
        read_only_fields = [
            "id",
            "type",
            "username",
            "phone_number",
            "avg_rating",
            "ratings_count",
            "successful_invites_count",
            "completed_orders_count",
            "seller_level",
            "seller_level_label",
            "activated",
            "is_active",
            "is_valid",
            "created",
        ]

    def get_successful_invites_count(self, obj):
        return _successful_invites_count(obj)

    def get_completed_orders_count(self, obj):
        return _completed_seller_orders_count(obj)

    def get_seller_level(self, obj):
        return SellerLevelService.get_level(self.get_completed_orders_count(obj)).code

    def get_seller_level_label(self, obj):
        return SellerLevelService.get_level(self.get_completed_orders_count(obj)).label


class CustomerProfileMobileSerializer(UserSecondaryPhoneMixin, serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    successful_invites_count = serializers.SerializerMethodField()
    completed_orders_count = serializers.SerializerMethodField()
    customer_level = serializers.SerializerMethodField()
    customer_level_label = serializers.SerializerMethodField()
    level_discount_percent = serializers.SerializerMethodField()
    level_cashback_percent = serializers.SerializerMethodField()

    class Meta:
        model = CustomerProfile
        exclude = ["user", "balance"]
        read_only_fields = [
            "id",
            "type",
            "username",
            "phone_number",
            "activated",
            "is_active",
            "is_valid",
            "cashback_balance",
            "created",
            "modified",
            "deleted",
            "successful_invites_count",
            "completed_orders_count",
            "customer_level",
            "customer_level_label",
            "level_discount_percent",
            "level_cashback_percent",
        ]

    def get_successful_invites_count(self, obj):
        return _successful_invites_count(obj)

    def get_completed_orders_count(self, obj):
        return _completed_customer_orders_count(obj)

    def get_customer_level(self, obj):
        return CustomerLevelService.get_level(self.get_completed_orders_count(obj)).code

    def get_customer_level_label(self, obj):
        return CustomerLevelService.get_level(
            self.get_completed_orders_count(obj)
        ).label

    def get_level_discount_percent(self, obj):
        return CustomerLevelService.get_discount_percent(
            self.get_completed_orders_count(obj)
        )

    def get_level_cashback_percent(self, obj):
        return CustomerLevelService.get_cashback_percent(
            self.get_completed_orders_count(obj)
        )


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
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    secondary_phone_number = serializers.CharField(
        source="user.secondary_phone_number",
        read_only=True,
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
            "secondary_phone_number",
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


class ManagerAdminUpdateSerializer(PhoneValidationMixin, serializers.Serializer):
    username = serializers.CharField(required=False)
    phone_number = serializers.CharField(required=False)
    secondary_phone_number = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
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
                _("user account with this phone number already exists.")
            )
        return value

    def update(self, instance, validated_data):
        user = instance.user
        user_fields = {
            "username",
            "phone_number",
            "secondary_phone_number",
            "email",
            "first_name",
            "last_name",
        }
        profile_fields = {"activated", "is_valid", "is_active"}

        for field in user_fields:
            if field in validated_data:
                value = validated_data[field]
                if field == "secondary_phone_number" and not value:
                    value = None
                setattr(user, field, value)

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


class ProfileBalanceTransactionSerializer(serializers.ModelSerializer):
    profile_id = serializers.IntegerField(read_only=True)
    adjusted_by_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = ProfileBalanceTransaction
        fields = [
            "id",
            "profile_id",
            "transaction_type",
            "amount",
            "balance_after",
            "adjusted_by_id",
            "note",
            "source_type",
            "source_id",
            "idempotency_key",
            "metadata",
            "created",
        ]
        read_only_fields = fields


class ProfileBalanceAdjustmentSerializer(serializers.Serializer):
    profile_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField()

    def validate_profile_id(self, value):
        try:
            return Profile.objects.get(pk=value)
        except Profile.DoesNotExist as exc:
            raise serializers.ValidationError(_("Profile was not found.")) from exc

    def create(self, validated_data):
        return ProfileBalanceService.manager_adjust(
            profile=validated_data["profile_id"],
            amount=validated_data["amount"],
            manager_profile=self.context["request"].user.active_profile,
            note=validated_data.get("note", ""),
            idempotency_key=validated_data["idempotency_key"],
        )


def _successful_invites_count(profile):
    from apps.referrals.models import LinkSharingCode

    return LinkSharingCode.objects.filter(
        owner_profile_id=profile.pk,
        is_used=True,
    ).count()


def _completed_seller_orders_count(profile):
    from apps.orders.models import OrderItem, OrderItemStatus

    return getattr(
        profile,
        "completed_orders_count",
        OrderItem.objects.filter(
            seller_id=profile.pk,
            status=OrderItemStatus.COMPLETED,
            deleted__isnull=True,
        ).count(),
    )


def _completed_customer_orders_count(profile):
    from apps.orders.models import OrderItem, OrderItemStatus

    return getattr(
        profile,
        "completed_orders_count",
        OrderItem.objects.filter(
            order__owner_id=profile.pk,
            status=OrderItemStatus.COMPLETED,
            deleted__isnull=True,
        ).count(),
    )
