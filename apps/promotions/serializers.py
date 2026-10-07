from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from decimal import Decimal

from apps.loft_subscriptions.models import SubscriptionDurationOption
from apps.packages.models import Package
from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    DiscountOffer,
    PromotionCampaign,
    PromotionCampaignEffect,
    PromotionCampaignStatus,
    PromotionCampaignType,
    PromotionEffectType,
    PromotionTargetType,
    UserPromotionGrant,
)
from apps.promotions.services import CashbackService
from apps.users.models import CustomerProfile
from django.utils.translation import gettext_lazy as _


class DiscountOfferSerializer(serializers.ModelSerializer):
    created_by_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = DiscountOffer
        fields = [
            "id",
            "title",
            "description",
            "value_type",
            "value",
            "target_type",
            "asset",
            "category",
            "minimum_order_price",
            "trigger_type",
            "is_active",
            "priority",
            "created_by_id",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "is_active",
            "created_by_id",
            "created",
            "modified",
        ]


class CashbackOfferSerializer(serializers.ModelSerializer):
    created_by_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = CashbackOffer
        fields = [
            "id",
            "title",
            "description",
            "value_type",
            "value",
            "target_type",
            "asset",
            "category",
            "minimum_order_price",
            "trigger_type",
            "allowed_redemption_amount",
            "is_active",
            "priority",
            "created_by_id",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "is_active",
            "created_by_id",
            "created",
            "modified",
        ]


class CashbackTransactionSerializer(serializers.ModelSerializer):
    customer_id = serializers.IntegerField(read_only=True)
    cashback_offer_id = serializers.IntegerField(read_only=True)
    cashback_offer_title = serializers.CharField(
        source="cashback_offer.title",
        read_only=True,
    )
    order_item_id = serializers.IntegerField(read_only=True, allow_null=True)
    adjusted_by_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = CashbackTransaction
        fields = [
            "id",
            "customer_id",
            "cashback_offer_id",
            "cashback_offer_title",
            "order_item_id",
            "transaction_type",
            "amount",
            "balance_after",
            "adjusted_by_id",
            "note",
            "idempotency_key",
            "created",
            "modified",
        ]
        read_only_fields = fields


class UserPromotionGrantSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)
    profile_id = serializers.IntegerField(read_only=True, allow_null=True)
    discount_offer_title = serializers.CharField(
        source="discount_offer.title",
        read_only=True,
        allow_null=True,
    )
    cashback_offer_title = serializers.CharField(
        source="cashback_offer.title",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = UserPromotionGrant
        fields = [
            "id",
            "user_id",
            "profile_id",
            "grant_type",
            "discount_offer",
            "discount_offer_title",
            "cashback_offer",
            "cashback_offer_title",
            "source_type",
            "source_id",
            "status",
            "expires_at",
            "redeemed_at",
            "order_item",
            "metadata",
            "created",
            "modified",
        ]
        read_only_fields = fields


class CashbackBalanceSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(read_only=True)
    cashback_balance = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )


class CashbackAdjustmentSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )

    def validate_customer_id(self, value):
        try:
            return CustomerProfile.objects.get(pk=value)
        except CustomerProfile.DoesNotExist as exc:
            raise serializers.ValidationError(
                _("Customer profile was not found.")
            ) from exc

    def create(self, validated_data):
        request = self.context["request"]
        idempotency_key = validated_data.get("idempotency_key") or None
        return CashbackService.admin_adjust(
            customer_profile=validated_data["customer_id"],
            amount=validated_data["amount"],
            admin_profile=request.user.active_profile,
            note=validated_data.get("note", ""),
            idempotency_key=idempotency_key,
        )



class TimezoneAwareDateTimeField(serializers.DateTimeField):
    """DateTimeField that rejects naive strings/datetimes lacking a timezone offset."""

    def enforce_timezone(self, value):
        if timezone.is_naive(value):
            raise serializers.ValidationError(
                _("Datetime value must be timezone-aware.")
            )
        return super().enforce_timezone(value)


class PromotionCampaignSerializer(serializers.ModelSerializer):
    """Admin serializer for scheduled promotion campaigns (BE-PRO-01)."""

    name = serializers.CharField(
        max_length=255,
        allow_blank=False,
        trim_whitespace=True,
    )
    campaign_type = serializers.ChoiceField(
        choices=PromotionCampaignType.choices,
    )
    starts_at = TimezoneAwareDateTimeField()
    ends_at = TimezoneAwareDateTimeField()
    is_enabled = serializers.BooleanField(required=False, default=True)
    effective_status = serializers.SerializerMethodField(read_only=True)
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    metadata = serializers.JSONField(required=False, default=dict)

    class Meta:
        model = PromotionCampaign
        fields = [
            "id",
            "name",
            "campaign_type",
            "starts_at",
            "ends_at",
            "is_enabled",
            "effective_status",
            "description",
            "metadata",
            "created_by",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "effective_status",
            "created_by",
            "created",
            "modified",
        ]

    @extend_schema_field(
        serializers.ChoiceField(choices=PromotionCampaignStatus.choices)
    )
    def get_effective_status(self, obj) -> str:
        return obj.effective_status()

    def validate_metadata(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                _("Campaign metadata must be a JSON object.")
            )
        return value

    def validate(self, attrs):
        starts_at = attrs.get(
            "starts_at",
            getattr(self.instance, "starts_at", None),
        )
        ends_at = attrs.get(
            "ends_at",
            getattr(self.instance, "ends_at", None),
        )
        if starts_at is not None and ends_at is not None and starts_at >= ends_at:
            raise serializers.ValidationError(
                {
                    "ends_at": _(
                        "Campaign start time must be earlier than end time."
                    )
                }
            )
        return attrs


class PublicPromotionCampaignSerializer(serializers.ModelSerializer):
    """Public/authenticated read-only serializer for currently active promotion campaigns."""

    effective_status = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PromotionCampaign
        fields = [
            "id",
            "name",
            "campaign_type",
            "starts_at",
            "ends_at",
            "effective_status",
            "description",
        ]
        read_only_fields = fields

    @extend_schema_field(
        serializers.ChoiceField(choices=PromotionCampaignStatus.choices)
    )
    def get_effective_status(self, obj) -> str:
        return obj.effective_status()



class PromotionCampaignEffectSerializer(serializers.ModelSerializer):
    """Admin serializer for service-level promotion campaign effects (BE-PRO-02)."""

    effect_type = serializers.ChoiceField(
        choices=PromotionEffectType.choices,
        required=False,
        default=PromotionEffectType.PERCENT_DISCOUNT,
    )
    target_type = serializers.ChoiceField(
        choices=PromotionTargetType.choices,
    )
    target_package = serializers.PrimaryKeyRelatedField(
        queryset=Package.objects.all(),
        required=False,
        allow_null=True,
    )
    target_subscription_duration = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionDurationOption.objects.all(),
        required=False,
        allow_null=True,
    )
    target_code = serializers.CharField(
        max_length=80,
        required=False,
        allow_blank=True,
    )
    discount_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
    )
    metadata = serializers.JSONField(required=False)

    class Meta:
        model = PromotionCampaignEffect
        fields = [
            "id",
            "campaign",
            "effect_type",
            "target_type",
            "target_package",
            "target_subscription_duration",
            "target_code",
            "discount_percent",
            "description",
            "metadata",
            "created_by",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "campaign",
            "created_by",
            "created",
            "modified",
        ]

    def validate_discount_percent(self, value):
        if isinstance(getattr(self, "initial_data", None), dict):
            raw = self.initial_data.get("discount_percent")
            if isinstance(raw, float):
                raise serializers.ValidationError(
                    _("Discount percentage must use Decimal precision, not float.")
                )
        if value <= Decimal("0.00") or value > Decimal("100.00"):
            raise serializers.ValidationError(
                _("Discount percentage must be greater than 0 and at most 100.")
            )
        return value

    def validate_metadata(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                _("Effect metadata must be a JSON object.")
            )
        return value

    def validate(self, attrs):
        target_type = attrs.get(
            "target_type",
            getattr(self.instance, "target_type", None),
        )
        target_package = attrs.get(
            "target_package",
            getattr(self.instance, "target_package", None),
        )
        target_subscription_duration = attrs.get(
            "target_subscription_duration",
            getattr(self.instance, "target_subscription_duration", None),
        )
        target_code = attrs.get(
            "target_code",
            getattr(self.instance, "target_code", ""),
        ) or ""

        if target_type == PromotionTargetType.SELLER_PACKAGE:
            if target_subscription_duration is not None:
                raise serializers.ValidationError(
                    {
                        "target_subscription_duration": _(
                            "Subscription duration target is only valid for LOFT_SUBSCRIPTION effects."
                        )
                    }
                )
            if target_code:
                raise serializers.ValidationError(
                    {
                        "target_code": _(
                            "Seller package effects use target_package instead of target_code."
                        )
                    }
                )
        elif target_type == PromotionTargetType.LOFT_SUBSCRIPTION:
            if target_package is not None:
                raise serializers.ValidationError(
                    {
                        "target_package": _(
                            "Seller package target is only valid for SELLER_PACKAGE effects."
                        )
                    }
                )
            if (
                target_subscription_duration is not None
                and target_code
                and target_code != target_subscription_duration.code
            ):
                raise serializers.ValidationError(
                    {
                        "target_code": _(
                            "Target code does not match the selected subscription duration."
                        )
                    }
                )
        elif target_type == PromotionTargetType.AUCTION_HIGHLIGHT:
            if target_package is not None:
                raise serializers.ValidationError(
                    {
                        "target_package": _(
                            "Seller package target is not valid for AUCTION_HIGHLIGHT effects."
                        )
                    }
                )
            if target_subscription_duration is not None:
                raise serializers.ValidationError(
                    {
                        "target_subscription_duration": _(
                            "Subscription duration target is not valid for AUCTION_HIGHLIGHT effects."
                        )
                    }
                )

        return attrs



class OnboardingHighlightBenefitSerializer(serializers.Serializer):
    """Read-only eligibility status for the account-level 20% first highlight benefit (BE-PRO-03)."""

    available = serializers.BooleanField(read_only=True)
    discount_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        read_only=True,
    )
    consumed_at = serializers.DateTimeField(read_only=True, allow_null=True)
