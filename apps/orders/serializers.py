from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from drf_writable_nested import WritableNestedModelSerializer
from rest_framework import serializers

from apps.assets.models import Asset
from apps.general.serializers import AssetMediaSerializer
from apps.orders.models import Cart, CartItem, Order, OrderItem
from apps.orders.services import OrderCheckoutService


def format_money(value):
    return f"{value:.2f}"  # noqa


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
        return OrderCheckoutService.create_order_from_items(
            customer,
            items_payload,
        )


class CheckoutSerializer(serializers.Serializer):
    pass


class CartItemCreateSerializer(serializers.Serializer):
    asset_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class CartItemUpdateSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)


class AssetOrderSerializer(serializers.ModelSerializer):
    media = AssetMediaSerializer(many=True, read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id",
            "title",
            "category",
            "status",
            "media",
            "price",
            "is_market_listed",
            "count",
        ]
        read_only_fields = fields


class CartItemSerializer(serializers.ModelSerializer):
    asset_id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(source="asset.title", read_only=True)
    seller_id = serializers.IntegerField(read_only=True)
    seller_nickname = serializers.CharField(
        source="seller.nickname",
        read_only=True,
        allow_blank=True,
    )
    unit_price = serializers.DecimalField(
        source="unit_price_snapshot",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )
    total = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "asset_id",
            "title",
            "seller_id",
            "seller_nickname",
            "quantity",
            "unit_price",
            "total",
            "created",
            "modified",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DecimalField(max_digits=10, decimal_places=2))
    def get_total(self, obj):
        return format_money(obj.quantity * obj.unit_price_snapshot)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            "id",
            "status",
            "items",
            "subtotal",
            "items_count",
            "created",
            "modified",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DecimalField(max_digits=10, decimal_places=2))
    def get_subtotal(self, obj):
        subtotal = sum(
            (item.quantity * item.unit_price_snapshot for item in obj.items.all()),
            Decimal("0.00"),
        )
        return format_money(subtotal)

    def get_items_count(self, obj) -> int:
        return obj.items.count()


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
            "gross_total",
            "discount_offer",
            "discount_amount",
            "cashback_offer",
            "cashback_redeemed_amount",
            "cashback_earned_amount",
            "total_price",
            "status",
            "created",
            "modified",
        ]
        read_only_fields = fields


class OrderItemSummarySerializer(serializers.ModelSerializer):
    asset_id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(source="asset.title", read_only=True)
    seller_id = serializers.IntegerField(read_only=True)
    seller_nickname = serializers.CharField(
        source="seller.nickname",
        read_only=True,
        allow_blank=True,
    )
    unit_price = serializers.DecimalField(
        source="unit_price_snapshot",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )
    total = serializers.DecimalField(
        source="total_price",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "asset_id",
            "title",
            "seller_id",
            "seller_nickname",
            "quantity",
            "unit_price",
            "gross_total",
            "discount_amount",
            "cashback_redeemed_amount",
            "cashback_earned_amount",
            "total",
            "status",
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
            "total_price",
            "item_count",
            "total_amount",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def get_item_count(self, obj) -> int:
        return obj.items.count()

    def get_total_amount(self, obj) -> str:
        return str(obj.total_price)


class OrderDetailSerializer(OrderListSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta(OrderListSerializer.Meta):
        fields = OrderListSerializer.Meta.fields + [
            "items",
        ]


class OrderSummarySerializer(serializers.ModelSerializer):
    items = OrderItemSummarySerializer(many=True, read_only=True)
    sellers_involved = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "total_price",
            "sellers_involved",
            "items",
            "created",
            "modified",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_sellers_involved(self, obj):
        return sorted({item.seller_id for item in obj.items.all()})
