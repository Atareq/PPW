from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.payments.models import (
    AuctionPaymentRequest,
    MarketPaymentRequest,
    PaymentRequest,
)


class CreateAuctionPaymentRequestSerializer(serializers.Serializer):
    auction_item_id = serializers.IntegerField()
    buyer_note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    payment_proof = serializers.FileField(required=False, allow_null=True)


class CreateMarketPaymentRequestSerializer(serializers.Serializer):
    order_item_id = serializers.IntegerField()
    buyer_note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    payment_proof = serializers.FileField(required=False, allow_null=True)


class UpdatePaymentRequestSerializer(serializers.Serializer):
    buyer_note = serializers.CharField(required=False, allow_blank=True)
    payment_proof = serializers.FileField(required=False, allow_null=True)


class RejectPaymentRequestSerializer(serializers.Serializer):
    seller_note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )


class PaymentRequestListSerializer(serializers.ModelSerializer):
    buyer = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()
    asset = serializers.SerializerMethodField()

    class Meta:
        model = PaymentRequest
        fields = [
            "id",
            "request_type",
            "buyer",
            "seller",
            "asset",
            "amount",
            "status",
            "buyer_note",
            "seller_note",
            "payment_proof",
            "created",
            "modified",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField())
    def get_buyer(self, obj):
        return self._profile_summary(obj.buyer)

    @extend_schema_field(serializers.DictField())
    def get_seller(self, obj):
        return self._profile_summary(obj.seller)

    @extend_schema_field(serializers.DictField())
    def get_asset(self, obj):
        return {
            "id": obj.asset_id,
            "title": obj.asset.title,
            "category": obj.asset.category,
        }

    def _profile_summary(self, profile):
        return {
            "id": profile.pk,
            "type": profile.type,
        }


class PaymentRequestDetailSerializer(PaymentRequestListSerializer):
    approved_by = serializers.SerializerMethodField()
    rejected_by = serializers.SerializerMethodField()
    auction_item_id = serializers.SerializerMethodField()
    order_item_id = serializers.SerializerMethodField()

    class Meta(PaymentRequestListSerializer.Meta):
        fields = PaymentRequestListSerializer.Meta.fields + [
            "approved_at",
            "rejected_at",
            "approved_by",
            "rejected_by",
            "auction_item_id",
            "order_item_id",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_approved_by(self, obj):
        return self._profile_summary(obj.approved_by) if obj.approved_by_id else None

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_rejected_by(self, obj):
        return self._profile_summary(obj.rejected_by) if obj.rejected_by_id else None

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_auction_item_id(self, obj):
        try:
            return obj.auctionpaymentrequest.auction_item_id
        except AuctionPaymentRequest.DoesNotExist:
            return None

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_order_item_id(self, obj):
        try:
            return obj.marketpaymentrequest.order_item_id
        except MarketPaymentRequest.DoesNotExist:
            return None
