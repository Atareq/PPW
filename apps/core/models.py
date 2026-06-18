from django.db import models

from apps.general.models import BaseModel


class NotificationKind(models.TextChoices):
    ORDER = "order", "Order update"
    AUCTION_WON = "auction_won", "Auction won"
    NEW_BID = "new_bid", "New bid on item"
    BID_ACCEPTED = "bid_accepted", "Bid accepted"
    NEW_BADGE = "new_badge", "Badge awarded"
    PAYMENT = "payment", "Payment update"
    CHAT = "chat", "New message"


class Notification(BaseModel):
    profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=30, choices=NotificationKind.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    profile_type = models.CharField(max_length=20, blank=True, default="All")

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["profile", "is_read"]),
        ]

    def __str__(self):
        return f"Notification #{self.pk} {self.kind} -> profile {self.profile_id}"
