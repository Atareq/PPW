from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class NotificationType(models.TextChoices):
    CHAT_MESSAGE_RECEIVED = "chat_message_received", _("Chat Message Received")
    MANUAL_MANAGER_NOTIFICATION = (
        "manual_manager_notification",
        _("Manual Manager Notification"),
    )
    AUCTION_BID_PLACED = "auction_bid_placed", _("Auction Bid Placed")
    AUCTION_BUY_NOW_SUCCESS = (
        "auction_buy_now_success",
        _("Auction Buy Now Success"),
    )
    PAYMENT_REQUEST_CREATED = (
        "payment_request_created",
        _("Payment Request Created"),
    )
    PAYMENT_REQUEST_APPROVED = (
        "payment_request_approved",
        _("Payment Request Approved"),
    )
    PAYMENT_REQUEST_REJECTED = (
        "payment_request_rejected",
        _("Payment Request Rejected"),
    )
    ORDER_ITEM_APPROVED = "order_item_approved", _("Order Item Approved")
    ORDER_ITEM_REJECTED = "order_item_rejected", _("Order Item Rejected")
    ORDER_ITEM_PENDING_SELLER = (
        "order_item_pending_seller",
        _("Order Item Pending Seller"),
    )
    ORDER_ITEM_AUTO_CANCELLED = (
        "order_item_auto_cancelled",
        _("Order Item Auto Cancelled"),
    )
    AUCTION_WON = "auction_won", _("Auction Won")
    AUCTION_OUTBID = "auction_outbid", _("Auction Outbid")
    AUCTION_LOST = "auction_lost", _("Auction Lost")
    AUCTION_RESERVE_NOT_MET = (
        "auction_reserve_not_met",
        _("Auction Reserve Not Met"),
    )
    NEW_USER_REGISTERED = "new_user_registered", _("New User Registered")
    COMPLAINT_CREATED = "complaint_created", _("Complaint Created")
    COMPLAINT_STATUS_UPDATED = (
        "complaint_status_updated",
        _("Complaint Status Updated"),
    )
    CASHBACK_EARNED = "cashback_earned", _("Cashback Earned")
    BADGE_AWARDED = "badge_awarded", _("Badge Awarded")
    PACKAGE_EXPIRING_SOON = (
        "package_expiring_soon",
        _("Package Expiring Soon"),
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
    metadata = models.JSONField(default=dict, blank=True)
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
                condition=(Q(idempotency_key__isnull=False) & ~Q(idempotency_key="")),
                name="notifications_unique_idempotency_key",
            ),
        ]

    def __str__(self):
        return (
            f"Notification #{self.pk} "
            f"{self.notification_type} -> {self.recipient_profile_id}"
        )
