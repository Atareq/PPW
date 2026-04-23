from decimal import Decimal

from drf_writable_nested import WritableNestedModelSerializer
from rest_framework import serializers

from apps.assets.models import Asset
from apps.orders.models import Order, OrderItem
from apps.orders.services import OrderService


class OrderItemCreateInputSerializer(serializers.ModelSerializer):
    asset_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)

    class Meta:
        model = OrderItem
        fields = [
            "asset_id",
            "quantity",
        ]


class OrderCreateSerializer(WritableNestedModelSerializer):
    items = OrderItemCreateInputSerializer(many=True, allow_empty=False)

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "items",
        ]
        read_only_fields = [
            "id",
            "status",
        ]

    def create(self, validated_data):
        request = self.context.get("request")
        customer = request.user.active_profile
        items_payload = validated_data["items"]
        return OrderService.create_order(customer, items_payload)


class AssetOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Asset
        fields = [
            "id",
            "title",
            "category",
            "status",
            "thumbnail_url",
            "price",
            "is_market_listed",
            "count",
        ]
        read_only_fields = fields


class OrderItemSerializer(serializers.ModelSerializer):
    asset = AssetOrderSerializer(read_only=True)
    seller_nickname = serializers.CharField(
        source="seller.nickname",
        read_only=True,
        allow_blank=True,
    )

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "order",
            "asset",
            "seller",
            "seller_nickname",
            "quantity",
            "unit_price_snapshot",
            "total_price",
            "status",
            "created",
            "modified",
        ]
        read_only_fields = fields


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "item_count",
            "total_amount",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def get_item_count(self, obj) -> int:
        return obj.items.count()

    def get_total_amount(self, obj) -> str:
        total = sum(
            (item.total_price for item in obj.items.all()), Decimal("0.00")
        )
        return str(total)


class OrderDetailSerializer(OrderListSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta(OrderListSerializer.Meta):
        fields = OrderListSerializer.Meta.fields + [
            "items",
        ]
