from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.assets.models import Asset, AssetCategory, AssetStatus
from apps.orders.models import Order, OrderItem, OrderItemStatus, OrderStatus
from apps.orders.validators import OrderCreationValidator


class OrderService:
    """Orchestrate market order creation and aggregate status updates."""

    @staticmethod
    @transaction.atomic
    def create_order(customer, items_payload):
        validator = OrderCreationValidator(customer, items_payload).validate()

        order = Order.objects.create(
            owner=validator.customer_profile,
            status=OrderStatus.OPEN,
        )

        order_items = []
        for item_payload in items_payload:
            asset = validator.asset_map[item_payload["asset_id"]]
            quantity = item_payload["quantity"]
            unit_price = asset.price

            order_items.append(
                OrderItem(
                    order=order,
                    asset=asset,
                    seller=validator.seller_map[asset.owner_id],
                    quantity=quantity,
                    unit_price_snapshot=unit_price,
                    total_price=quantity * unit_price,
                    status=OrderItemStatus.PENDING_SELLER_APPROVAL,
                )
            )

        OrderItem.objects.bulk_create(order_items)
        return OrderService.get_order(order.pk)

    @staticmethod
    def get_order(order_id):
        return (
            Order.objects.select_related("owner", "owner__user")
            .prefetch_related("items", "items__asset", "items__seller")
            .get(pk=order_id)
        )

    @staticmethod
    def refresh_order_status(order_id):
        order = Order.objects.select_for_update().get(pk=order_id)
        has_active_items = order.items.exclude(
            status__in=[
                OrderItemStatus.REJECTED,
                OrderItemStatus.CANCELLED,
            ]
        ).exists()

        next_status = (
            OrderStatus.OPEN if has_active_items else OrderStatus.CANCELLED
        )
        if order.status != next_status:
            order.status = next_status
            order.save(update_fields=["status", "modified"])

        return order


class OrderItemService:
    """Orchestrate seller decisions for individual order items."""

    @staticmethod
    @transaction.atomic
    def approve_item(order_item, seller):
        seller_profile = OrderCreationValidator.get_seller_profile(seller)
        item = OrderItemService._get_locked_item(order_item.pk)
        OrderItemService._validate_seller_owns_item(item, seller_profile)

        if item.status == OrderItemStatus.SELLER_APPROVED:
            return item

        if item.status != OrderItemStatus.PENDING_SELLER_APPROVAL:
            raise ValidationError("Only pending order items can be approved.")

        asset = OrderItemService._get_locked_asset(item.asset_id)
        OrderItemService._decrement_stock(asset, item.quantity)

        item.status = OrderItemStatus.SELLER_APPROVED
        item.save(update_fields=["status", "modified"])
        OrderService.refresh_order_status(item.order_id)

        return OrderItemService.get_item(item.pk)

    @staticmethod
    @transaction.atomic
    def reject_item(order_item, seller):
        seller_profile = OrderCreationValidator.get_seller_profile(seller)
        item = OrderItemService._get_locked_item(order_item.pk)
        OrderItemService._validate_seller_owns_item(item, seller_profile)

        if item.status == OrderItemStatus.REJECTED:
            return item

        if item.status != OrderItemStatus.PENDING_SELLER_APPROVAL:
            raise ValidationError("Only pending order items can be rejected.")

        item.status = OrderItemStatus.REJECTED
        item.save(update_fields=["status", "modified"])
        OrderService.refresh_order_status(item.order_id)

        return OrderItemService.get_item(item.pk)

    @staticmethod
    def get_item(item_id):
        return OrderItem.objects.select_related(
            "order",
            "asset",
            "seller",
            "seller__user",
        ).get(pk=item_id)

    @staticmethod
    def _get_locked_item(item_id):
        return (
            OrderItem.objects.select_for_update()
            .select_related("order", "asset", "seller")
            .get(pk=item_id)
        )

    @staticmethod
    def _get_locked_asset(asset_id):
        try:
            return Asset.objects.select_for_update().get(pk=asset_id)
        except Asset.DoesNotExist as exc:
            raise ValidationError("Asset is no longer available.") from exc

    @staticmethod
    def _validate_seller_owns_item(item, seller):
        if item.seller_id != seller.pk:
            raise ValidationError("Order item does not belong to this seller.")

    @staticmethod
    def _decrement_stock(asset, quantity):
        if asset.status != AssetStatus.AVAILABLE:
            raise ValidationError("Asset is no longer available.")

        if asset.count < quantity:
            raise ValidationError("Asset does not have enough stock.")

        asset.count -= quantity
        update_fields = ["count", "modified"]

        if asset.category == AssetCategory.BIRD or asset.count == 0:
            asset.status = AssetStatus.SOLD
            asset.is_market_listed = False
            update_fields.extend(["status", "is_market_listed"])

        asset.save(update_fields=update_fields)
