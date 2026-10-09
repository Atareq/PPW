from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.deals.validators import CompletedDealValidator
from apps.general.models import BaseModel
from apps.users.constants import CurrencyOptions


class CompletedDealType(models.TextChoices):
    AUCTION = "auction", _("Auction")
    MARKET = "market", _("Market")


class CompletedDealSourceType(models.TextChoices):
    AUCTION_ITEM = "auction_item", _("Auction Item")
    ORDER_ITEM = "order_item", _("Order Item")


class CompletedDeal(BaseModel):
    """Canonical record that PPW considers a buyer/seller deal completed.

    This is business history, not a payment: PPW never moves money. Rows are
    written once by ``CompletedDealService`` from the trusted completion flow and
    are immutable afterwards. The business source (order item or auction item)
    is unique, so a deal can be recorded only once.
    """

    deal_type = models.CharField(max_length=20, choices=CompletedDealType.choices)
    source_type = models.CharField(
        max_length=30,
        choices=CompletedDealSourceType.choices,
    )
    source_id = models.PositiveBigIntegerField()
    buyer_user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        related_name="completed_purchases",
    )
    buyer_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="completed_purchases",
    )
    seller_user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        related_name="completed_sales",
    )
    seller_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="completed_sales",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="completed_deals",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(
        max_length=3,
        choices=CurrencyOptions.choices,
        blank=True,
    )
    completed_at = models.DateTimeField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-completed_at", "-id"]
        indexes = [
            models.Index(fields=["buyer_profile", "completed_at"]),
            models.Index(fields=["seller_profile", "completed_at"]),
            models.Index(fields=["deal_type", "completed_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "source_id"],
                name="deals_unique_completed_deal_source",
            ),
            models.CheckConstraint(
                check=(
                    Q(
                        deal_type=CompletedDealType.MARKET,
                        source_type=CompletedDealSourceType.ORDER_ITEM,
                    )
                    | Q(
                        deal_type=CompletedDealType.AUCTION,
                        source_type=CompletedDealSourceType.AUCTION_ITEM,
                    )
                ),
                name="deals_completed_deal_type_matches_source",
            ),
            models.CheckConstraint(
                check=Q(amount__gte=0),
                name="deals_completed_deal_amount_gte_0",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_("Completed deals cannot be changed."))
        CompletedDealValidator.validate_parties(
            buyer_user=self.buyer_user,
            buyer_profile=self.buyer_profile,
            seller_user=self.seller_user,
            seller_profile=self.seller_profile,
        )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Completed deals cannot be deleted."))

    def __str__(self):
        return f"CompletedDeal #{self.pk} ({self.deal_type} {self.source_type}:{self.source_id})"
