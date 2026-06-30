from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class SellerInsightEventType(models.TextChoices):
    SELLER_PROFILE_VIEW = "seller_profile_view", _("Seller Profile View")
    AUCTION_VIEW = "auction_view", _("Auction View")
    MARKET_ITEM_VIEW = "market_item_view", _("Market Item View")


class SellerInsightObjectType(models.TextChoices):
    SELLER_PROFILE = "seller_profile", _("Seller Profile")
    AUCTION = "auction", _("Auction")
    MARKET_ITEM = "market_item", _("Market Item")


class SellerInsightEvent(BaseModel):
    seller = models.ForeignKey(
        "users.SellerProfile",
        on_delete=models.CASCADE,
        related_name="insight_events",
    )
    actor_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seller_insight_events",
    )
    event_type = models.CharField(
        max_length=40,
        choices=SellerInsightEventType.choices,
    )
    object_type = models.CharField(
        max_length=40,
        choices=SellerInsightObjectType.choices,
    )
    object_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["seller", "event_type", "created"],
                name="ins_seller_event_created_idx",
            ),
            models.Index(
                fields=["seller", "created"],
                name="ins_seller_created_idx",
            ),
            models.Index(
                fields=["event_type", "created"],
                name="ins_event_created_idx",
            ),
            models.Index(
                fields=["object_type", "object_id"],
                name="ins_object_lookup_idx",
            ),
        ]
        ordering = ["-created"]

    def __str__(self):
        return f"SellerInsightEvent #{self.pk} ({self.event_type})"
