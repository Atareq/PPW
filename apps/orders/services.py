from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from rest_framework.exceptions import NotFound, ValidationError

from apps.assets.models import Asset, AssetCategory, AssetStatus
from apps.orders.models import (
    Cart,
    CartItem,
    CartStatus,
    Order,
    OrderEvent,
    OrderEventType,
    OrderItem,
    OrderItemStatus,
    OrderStatus,
)
from apps.orders.validators import (
    CartItemValidator,
    OrderCheckoutValidator,
    OrderCreationValidator,
    PurchaseRuleValidator,
)


def cart_item_queryset():
    return CartItem.objects.select_related(
        "asset",
        "seller",
        "seller__user",
    ).order_by("created")


def order_item_queryset():
    return OrderItem.objects.select_related(
        "order",
        "asset",
        "seller",
        "seller__user",
    ).order_by("-created")


class CartService:
    """Manage the customer's persistent active cart."""

    @staticmethod
    def get_or_create_active_cart(customer):
        customer_profile = PurchaseRuleValidator.get_customer_profile(customer)
        try:
            cart, _ = Cart.objects.get_or_create(
                customer=customer_profile,
                status=CartStatus.ACTIVE,
            )
        except IntegrityError:
            cart = Cart.objects.get(
                customer=customer_profile,
                status=CartStatus.ACTIVE,
            )
        return CartService.get_cart(cart.pk)

    @staticmethod
    def get_cart(cart_id):
        return (
            Cart.objects.select_related("customer", "customer__user")
            .prefetch_related(Prefetch("items", queryset=cart_item_queryset()))
            .get(pk=cart_id)
        )

    @staticmethod
    @transaction.atomic
    def add_item(customer, asset_id, quantity):
        customer_profile = PurchaseRuleValidator.get_customer_profile(customer)
        cart = Cart.objects.select_for_update().get_or_create(
            customer=customer_profile,
            status=CartStatus.ACTIVE,
        )[0]
        existing_item = (
            CartItem.objects.select_for_update()
            .filter(cart=cart, asset_id=asset_id)
            .first()
        )
        existing_quantity = existing_item.quantity if existing_item else 0
        validator = CartItemValidator(
            customer_profile,
            asset_id,
            quantity,
            existing_quantity=existing_quantity,
        ).validate()

        total_quantity = quantity + existing_quantity
        if existing_item:
            existing_item.quantity = total_quantity
            existing_item.seller = validator.seller
            existing_item.unit_price_snapshot = validator.asset.price
            existing_item.save(
                update_fields=[
                    "quantity",
                    "seller",
                    "unit_price_snapshot",
                    "modified",
                ]
            )
            return CartService.get_cart(cart.pk)

        CartItem.objects.create(
            cart=cart,
            asset=validator.asset,
            seller=validator.seller,
            quantity=quantity,
            unit_price_snapshot=validator.asset.price,
        )
        return CartService.get_cart(cart.pk)

    @staticmethod
    @transaction.atomic
    def update_item(customer, item_id, quantity):
        customer_profile = PurchaseRuleValidator.get_customer_profile(customer)
        try:
            item = (
                CartItem.objects.select_for_update()
                .select_related(
                    "cart",
                    "asset",
                    "seller",
                )
                .get(
                    pk=item_id,
                    cart__customer=customer_profile,
                    cart__status=CartStatus.ACTIVE,
                )
            )
        except CartItem.DoesNotExist as exc:
            raise NotFound("Cart item was not found.") from exc

        validator = CartItemValidator(
            customer_profile,
            item.asset_id,
            quantity,
        ).validate()

        item.quantity = quantity
        item.seller = validator.seller
        item.unit_price_snapshot = validator.asset.price
        item.save(
            update_fields=[
                "quantity",
                "seller",
                "unit_price_snapshot",
                "modified",
            ]
        )
        return CartService.get_cart(item.cart_id)

    @staticmethod
    @transaction.atomic
    def remove_item(customer, item_id):
        customer_profile = PurchaseRuleValidator.get_customer_profile(customer)
        try:
            item = CartItem.objects.select_for_update().get(
                pk=item_id,
                cart__customer=customer_profile,
                cart__status=CartStatus.ACTIVE,
            )
        except CartItem.DoesNotExist as exc:
            raise NotFound("Cart item was not found.") from exc

        cart_id = item.cart_id
        item.delete()
        return CartService.get_cart(cart_id)

    @staticmethod
    @transaction.atomic
    def clear_cart(customer):
        customer_profile = PurchaseRuleValidator.get_customer_profile(customer)
        cart = Cart.objects.select_for_update().get_or_create(
            customer=customer_profile,
            status=CartStatus.ACTIVE,
        )[0]
        CartItem.objects.filter(cart=cart).delete()
        return CartService.get_cart(cart.pk)


class OrderService:
    """Read orders and maintain aggregate order state."""

    @staticmethod
    def create_order(customer, items_payload):
        return OrderCheckoutService.create_order_from_items(
            customer,
            items_payload,
        )

    @staticmethod
    def get_order(order_id):
        return (
            Order.objects.select_related("owner", "owner__user")
            .prefetch_related(
                Prefetch("items", queryset=order_item_queryset()),
                "events",
            )
            .get(pk=order_id)
        )

    @staticmethod
    def refresh_order_status(order_id):
        order = (
            Order.objects.select_for_update()
            .prefetch_related("items")
            .get(pk=order_id)
        )
        statuses = list(order.items.values_list("status", flat=True))

        terminal_rejected = {
            OrderItemStatus.REJECTED,
            OrderItemStatus.CANCELLED,
        }
        progress_statuses = {
            OrderItemStatus.APPROVED,
            OrderItemStatus.AWAITING_PAYMENT_REVIEW,
            OrderItemStatus.READY_HANDOFF,
            OrderItemStatus.COMPLETED,
        }

        if statuses and all(
            status in terminal_rejected for status in statuses
        ):
            next_status = OrderStatus.CANCELLED
        elif any(status in terminal_rejected for status in statuses):
            next_status = OrderStatus.PARTIAL_REJECTED
        elif statuses and all(
            status == OrderItemStatus.COMPLETED for status in statuses
        ):
            next_status = OrderStatus.COMPLETED
        elif any(status in progress_statuses for status in statuses):
            next_status = OrderStatus.PROCESSING
        else:
            next_status = OrderStatus.PENDING

        if order.status != next_status:
            order.status = next_status
            order.save(update_fields=["status", "modified"])

        return order


class OrderCheckoutService:
    """Convert cart/direct payloads into stock-reserving orders."""

    @staticmethod
    @transaction.atomic
    def checkout_cart(customer):
        customer_profile = PurchaseRuleValidator.get_customer_profile(customer)
        try:
            cart = Cart.objects.select_for_update().get(
                customer=customer_profile,
                status=CartStatus.ACTIVE,
            )
        except Cart.DoesNotExist as exc:
            raise ValidationError({"cart": "Cart is empty."}) from exc

        cart_items = list(
            CartItem.objects.select_for_update()
            .select_related("asset", "seller")
            .filter(cart=cart)
            .order_by("asset_id")
        )
        asset_map = OrderCheckoutService._get_locked_asset_map(
            [item.asset_id for item in cart_items]
        )
        validator = OrderCheckoutValidator(
            customer_profile,
            cart_items,
            asset_map,
        ).validate()

        item_payloads = [
            {"asset_id": item.asset_id, "quantity": item.quantity}
            for item in cart_items
        ]
        order = OrderCheckoutService._create_reserved_order(
            validator.customer_profile,
            item_payloads,
            asset_map,
            validator.seller_map,
        )

        cart.status = CartStatus.CONVERTED
        cart.save(update_fields=["status", "modified"])
        return order

    @staticmethod
    @transaction.atomic
    def create_order_from_items(customer, items_payload):
        validator = OrderCreationValidator(
            customer,
            items_payload,
            lock_assets=True,
        ).validate()
        return OrderCheckoutService._create_reserved_order(
            validator.customer_profile,
            items_payload,
            validator.asset_map,
            validator.seller_map,
        )

    @staticmethod
    def _get_locked_asset_map(asset_ids):
        unique_ids = sorted(set(asset_ids))
        assets = (
            Asset.objects.select_for_update()
            .select_related("owner", "owner__user")
            .filter(pk__in=unique_ids)
            .order_by("id")
        )
        return {asset.pk: asset for asset in assets}

    @staticmethod
    def _create_reserved_order(
        customer_profile,
        items_payload,
        asset_map,
        seller_map,
    ):
        order = Order.objects.create(
            owner=customer_profile,
            status=OrderStatus.PENDING,
            total_price=Decimal("0.00"),
        )

        order_items = []
        total_price = Decimal("0.00")
        for item_payload in items_payload:
            asset = asset_map[item_payload["asset_id"]]
            quantity = item_payload["quantity"]
            unit_price = asset.price
            item_total = quantity * unit_price
            total_price += item_total

            order_items.append(
                OrderItem(
                    order=order,
                    asset=asset,
                    seller=seller_map[asset.owner_id],
                    quantity=quantity,
                    unit_price_snapshot=unit_price,
                    total_price=item_total,
                    status=OrderItemStatus.PENDING_SELLER,
                )
            )
            OrderCheckoutService._reserve_stock(asset, quantity)

        OrderItem.objects.bulk_create(order_items)
        order.total_price = total_price
        order.save(update_fields=["total_price", "modified"])
        order = OrderService.get_order(order.pk)
        OrderCheckoutService._create_checkout_events(order)
        return OrderService.get_order(order.pk)

    @staticmethod
    def _reserve_stock(asset, quantity):
        if asset.count < quantity:
            raise ValidationError({"detail": "Insufficient stock"})

        asset.count -= quantity
        update_fields = ["count", "modified"]

        if asset.category == AssetCategory.BIRD or asset.count == 0:
            asset.status = AssetStatus.SOLD
            asset.is_market_listed = False
            update_fields.extend(["status", "is_market_listed"])

        asset.save(update_fields=update_fields)

    @staticmethod
    def _create_checkout_events(order):
        events = [
            OrderEvent(
                order=order,
                recipient=order.owner,
                event_type=OrderEventType.ORDER_CREATED,
                message="Order created.",
            )
        ]
        events.extend(
            OrderEvent(
                order=order,
                item=item,
                recipient=item.seller,
                event_type=OrderEventType.SELLER_PENDING_ITEM,
                message="Seller notified of pending item.",
                metadata={"asset_id": item.asset_id},
            )
            for item in order.items.all()
        )
        OrderEvent.objects.bulk_create(events)


class OrderItemService:
    """Orchestrate seller decisions for individual order items."""

    @staticmethod
    @transaction.atomic
    def approve_item(order_item, seller):
        seller_profile = PurchaseRuleValidator.get_seller_profile(seller)
        item = OrderItemService._get_locked_item(order_item.pk)
        OrderItemService._validate_seller_owns_item(item, seller_profile)

        if item.status == OrderItemStatus.APPROVED:
            return item

        if item.status != OrderItemStatus.PENDING_SELLER:
            raise ValidationError("Only pending order items can be approved.")

        item.status = OrderItemStatus.APPROVED
        item.save(update_fields=["status", "modified"])
        OrderService.refresh_order_status(item.order_id)
        OrderItemService._create_seller_approved_event(item)

        return OrderItemService.get_item(item.pk)

    @staticmethod
    @transaction.atomic
    def reject_item(order_item, seller):
        seller_profile = PurchaseRuleValidator.get_seller_profile(seller)
        item = OrderItemService._get_locked_item(order_item.pk)
        OrderItemService._validate_seller_owns_item(item, seller_profile)

        if item.status == OrderItemStatus.REJECTED:
            return item

        if item.status != OrderItemStatus.PENDING_SELLER:
            raise ValidationError("Only pending order items can be rejected.")

        item.status = OrderItemStatus.REJECTED
        item.save(update_fields=["status", "modified"])
        OrderService.refresh_order_status(item.order_id)

        return OrderItemService.get_item(item.pk)

    @staticmethod
    def get_item(item_id):
        return order_item_queryset().get(pk=item_id)

    @staticmethod
    def _get_locked_item(item_id):
        return (
            OrderItem.objects.select_for_update()
            .select_related("order", "asset", "seller")
            .get(pk=item_id)
        )

    @staticmethod
    def _validate_seller_owns_item(item, seller):
        if item.seller_id != seller.pk:
            raise ValidationError("Order item does not belong to this seller.")

    @staticmethod
    def _create_seller_approved_event(item):
        OrderEvent.objects.create(
            order=item.order,
            item=item,
            recipient=item.order.owner,
            event_type=OrderEventType.SELLER_APPROVED,
            message=(
                "Seller approved your order you need to complete the payment."
            ),
            metadata={"asset_id": item.asset_id},
        )
