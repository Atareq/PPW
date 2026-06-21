from django.db import models
from django.db.models import Q

from apps.general.models import BaseModel


class NotificationType(models.TextChoices):
    CHAT_MESSAGE_RECEIVED = "chat_message_received", "Chat Message Received"
    PAYMENT_REQUEST_CREATED = (
        "payment_request_created",
        "Payment Request Created",
    )
    PAYMENT_REQUEST_APPROVED = (
        "payment_request_approved",
        "Payment Request Approved",
    )
    PAYMENT_REQUEST_REJECTED = (
        "payment_request_rejected",
        "Payment Request Rejected",
    )
    ORDER_ITEM_APPROVED = "order_item_approved", "Order Item Approved"
    ORDER_ITEM_REJECTED = "order_item_rejected", "Order Item Rejected"
    AUCTION_WON = "auction_won", "Auction Won"
    AUCTION_OUTBID = "auction_outbid", "Auction Outbid"
    COMPLAINT_CREATED = "complaint_created", "Complaint Created"
    COMPLAINT_STATUS_UPDATED = (
        "complaint_status_updated",
        "Complaint Status Updated",
    )
    CASHBACK_EARNED = "cashback_earned", "Cashback Earned"
    BADGE_AWARDED = "badge_awarded", "Badge Awarded"
    PACKAGE_EXPIRING_SOON = (
        "package_expiring_soon",
        "Package Expiring Soon",
    )


class Notification(BaseModel):
    recipient_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="acted_notifications",
        null=True,
        blank=True,
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    object_type = models.CharField(max_length=100, null=True, blank=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target_url = models.CharField(max_length=2048, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created", "-id"]
        indexes = [
            models.Index(
                fields=["recipient_profile", "is_read", "-created"],
            ),
            models.Index(fields=["recipient_profile", "-created"]),
            models.Index(fields=["notification_type", "-created"]),
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["idempotency_key"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=(
                    Q(idempotency_key__isnull=False)
                    & ~Q(idempotency_key="")
                ),
                name="notifications_unique_idempotency_key",
            ),
        ]

    def __str__(self):
        return (
            f"Notification #{self.pk} "
            f"{self.notification_type} -> {self.recipient_profile_id}"
        )

