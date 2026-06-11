from rest_framework import serializers

from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    DiscountOffer,
)
from apps.promotions.services import CashbackService
from apps.users.models import CustomerProfile


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
                "Customer profile was not found."
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
