from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.assets.models import Asset
from apps.general.models import BaseModel, OwnerBaseModel
from apps.users.models import CustomerProfile, Profile, SellerProfile


class CartStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CONVERTED = "converted", "Converted"
    ABANDONED = "abandoned", "Abandoned"


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    PARTIAL_REJECTED = "partial_rejected", "Partial Rejected"


class OrderItemStatus(models.TextChoices):
    PENDING_SELLER = "pending_seller", "Pending Seller"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    AWAITING_PAYMENT_REVIEW = (
        "awaiting_payment_review",
        "Awaiting Payment Review",
    )
    READY_HANDOFF = "ready_handoff", "Ready Handoff"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class OrderEventType(models.TextChoices):
    ORDER_CREATED = "order_created", "Order Created"
    SELLER_PENDING_ITEM = "seller_pending_item", "Seller Pending Item"
    SELLER_APPROVED = "seller_approved", "Seller Approved"


class Cart(BaseModel):
    customer = models.ForeignKey(
        CustomerProfile,
        on_delete=models.CASCADE,
        related_name="carts",
    )
    status = models.CharField(
        max_length=20,
        choices=CartStatus.choices,
        default=CartStatus.ACTIVE,
    )

    class Meta:
        indexes = [
            models.Index(fields=["customer", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(
                    status=CartStatus.ACTIVE,
                    deleted__isnull=True,
                ),
                name="orders_one_active_cart_per_customer",
            ),
        ]

    def __str__(self):
        return f"Cart #{self.pk} ({self.status})"


class CartItem(BaseModel):
    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="cart_items",
    )
    seller = models.ForeignKey(
        SellerProfile,
        on_delete=models.CASCADE,
        related_name="cart_items",
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    unit_price_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        indexes = [
            models.Index(fields=["asset"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "asset"],
                condition=models.Q(deleted__isnull=True),
                name="orders_unique_asset_per_cart",
            ),
            models.CheckConstraint(
                check=models.Q(quantity__gte=1),
                name="orders_cartitem_quantity_gte_1",
            ),
        ]

    def __str__(self):
        return f"CartItem #{self.pk} ({self.quantity})"


class Order(OwnerBaseModel):
    owner = models.ForeignKey(
        CustomerProfile,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
    )
    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Order #{self.pk} ({self.status})"


class OrderItem(BaseModel):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="order_items",
    )
    seller = models.ForeignKey(
        SellerProfile,
        on_delete=models.CASCADE,
        related_name="sold_order_items",
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    unit_price_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        max_length=30,
        choices=OrderItemStatus.choices,
        default=OrderItemStatus.PENDING_SELLER,
    )

    class Meta:
        indexes = [
            models.Index(fields=["asset"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gte=1),
                name="orders_orderitem_quantity_gte_1",
            ),
            models.CheckConstraint(
                check=models.Q(unit_price_snapshot__gte=0),
                name="orders_orderitem_unit_price_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(total_price__gte=0),
                name="orders_orderitem_total_price_gte_0",
            ),
        ]

    def save(self, *args, **kwargs):
        self.total_price = self.quantity * self.unit_price_snapshot

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = list(
                set(update_fields) | {"total_price"}
            )

        super().save(*args, **kwargs)

    def __str__(self):
        return f"OrderItem #{self.pk} ({self.status})"


class OrderEvent(BaseModel):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="events",
    )
    item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="events",
        null=True,
        blank=True,
    )
    recipient = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="order_events",
    )
    event_type = models.CharField(
        max_length=40,
        choices=OrderEventType.choices,
    )
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["order", "event_type"]),
            models.Index(fields=["recipient", "-created"]),
        ]

    def __str__(self):
        return f"OrderEvent #{self.pk} ({self.event_type})"
