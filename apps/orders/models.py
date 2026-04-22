from django.core.validators import MinValueValidator
from django.db import models

from apps.assets.models import Asset
from apps.general.models import BaseModel, OwnerBaseModel
from apps.users.models import CustomerProfile, SellerProfile


class OrderStatus(models.TextChoices):
    OPEN = "open", "Open"  # Wehenever moved from Cart model to Order
    PARTIALLY_SETTLED = "partially_settled", "Partially Settled"
    SETTLED = "settled", "Settled"
    CANCELLED = "cancelled", "Cancelled"


class OrderItemStatus(models.TextChoices):
    PENDING_SELLER_APPROVAL = (
        "pending_seller_approval",
        "Pending Seller Approval",
    )
    SELLER_APPROVED = "seller_approved", "Seller Approved"
    REJECTED = "rejected", "Rejected"
    SETTLED = "settled", "Settled"
    CANCELLED = "cancelled", "Cancelled"


class Order(OwnerBaseModel):
    owner = models.ForeignKey(
        CustomerProfile,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        default=OrderStatus.OPEN,
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
        default=OrderItemStatus.PENDING_SELLER_APPROVAL,
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
