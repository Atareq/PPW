from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class PaymentRequestStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    CANCELLED = "cancelled", _("Cancelled")


class PaymentRequestType(models.TextChoices):
    AUCTION = "auction", _("Auction")
    MARKET = "market", _("Market")


class PaymentRequestEventType(models.TextChoices):
    CREATED = "payment_request_created", _("Payment Request Created")
    UPDATED = "payment_request_updated", _("Payment Request Updated")
    APPROVED = "payment_request_approved", _("Payment Request Approved")
    REJECTED = "payment_request_rejected", _("Payment Request Rejected")


class PaymentRequest(BaseModel):
    buyer = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="payment_requests_as_buyer",
    )
    seller = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="payment_requests_as_seller",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="payment_requests",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        max_length=20,
        choices=PaymentRequestStatus.choices,
        default=PaymentRequestStatus.PENDING,
    )
    request_type = models.CharField(
        max_length=20,
        choices=PaymentRequestType.choices,
    )
    buyer_note = models.TextField(blank=True)
    seller_note = models.TextField(blank=True)
    payment_proof = models.FileField(upload_to='payment_proofs/', null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_payment_requests",
    )
    rejected_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rejected_payment_requests",
    )

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["buyer"]),
            models.Index(fields=["seller"]),
            models.Index(fields=["asset"]),
            models.Index(fields=["status"]),
            models.Index(fields=["request_type"]),
            models.Index(fields=["created"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=0),
                name="payments_paymentrequest_amount_gte_0",
            ),
        ]

    def __str__(self):
        return f"PaymentRequest #{self.pk} ({self.request_type}, {self.status})"


class AuctionPaymentRequest(PaymentRequest):
    auction_item = models.OneToOneField(
        "auctions.AuctionItem",
        on_delete=models.PROTECT,
        related_name="payment_request",
    )

    def save(self, *args, **kwargs):
        self.request_type = PaymentRequestType.AUCTION
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"request_type"}
        super().save(*args, **kwargs)


class MarketPaymentRequest(PaymentRequest):
    order_item = models.OneToOneField(
        "orders.OrderItem",
        on_delete=models.PROTECT,
        related_name="payment_request",
    )

    def save(self, *args, **kwargs):
        self.request_type = PaymentRequestType.MARKET
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"request_type"}
        super().save(*args, **kwargs)


class PaymentRequestEvent(BaseModel):
    payment_request = models.ForeignKey(
        PaymentRequest,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(
        max_length=40,
        choices=PaymentRequestEventType.choices,
    )
    actor = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_request_events",
    )
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["payment_request", "event_type"]),
            models.Index(fields=["actor"]),
            models.Index(fields=["created"]),
        ]

    def __str__(self):
        return f"PaymentRequestEvent #{self.pk} ({self.event_type})"
