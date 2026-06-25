from django.db import models

from apps.general.models import BaseModel


class TransferType(models.TextChoices):
    DIRECT = "direct", "Direct Transfer"
    MARKET = "market", "Market Purchase"
    AUCTION = "auction", "Auction Purchase"


class OwnershipTransfer(BaseModel):
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="ownership_transfers",
        db_index=True,
    )
    # null = first-ever recorded transfer (no previous owner on record)
    from_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="sent_transfers",
        null=True,
        blank=True,
    )
    to_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="received_transfers",
    )
    transfer_type = models.CharField(
        max_length=20,
        choices=TransferType.choices,
    )
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"Transfer #{self.pk}: {self.asset_id} → {self.to_profile_id} ({self.transfer_type})"
