from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.assets.models import Asset
from apps.general.models import BaseModel, OwnerBaseModel
from apps.users.models import CustomerProfile, Profile, SellerProfile


class Comment(OwnerBaseModel):
    text = models.TextField()
    is_rating_comment = models.BooleanField(default=False)

    class Meta:
        abstract = True


class AssetComment(Comment):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
    )

    class Meta:
        indexes = [
            models.Index(fields=["asset", "-created"]),
        ]

    def __str__(self):
        return f"AssetComment #{self.pk} on asset #{self.asset_id}"


class SellerComment(Comment):
    seller = models.ForeignKey(
        SellerProfile,
        on_delete=models.CASCADE,
    )

    class Meta:
        indexes = [
            models.Index(fields=["seller", "-created"]),
        ]

    def __str__(self):
        return f"SellerComment #{self.pk} on seller #{self.seller_id}"


class Rating(BaseModel):
    owner = models.ForeignKey(
        CustomerProfile,
        on_delete=models.CASCADE,
        db_index=True,
    )
    stars = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(5),
        ],
    )

    class Meta:
        abstract = True


class AssetRating(Rating):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="asset_ratings",
    )
    comment = models.ForeignKey(
        AssetComment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["asset", "-created"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "asset"],
                condition=models.Q(deleted__isnull=True),
                name="asset_ratings_unique_owner_asset",
            ),
        ]

    def __str__(self):
        return f"AssetRating #{self.pk} ({self.stars}) for {self.asset_id}"


class SellerRating(Rating):
    seller = models.ForeignKey(
        SellerProfile,
        on_delete=models.CASCADE,
    )
    comment = models.ForeignKey(
        SellerComment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["seller", "-created"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "seller"],
                condition=models.Q(deleted__isnull=True),
                name="seller_ratings_unique_owner_seller",
            ),
        ]

    def __str__(self):
        return f"SellerRating #{self.pk} ({self.stars}) for {self.seller_id}"


class DealRatingDirection(models.TextChoices):
    BUYER_TO_SELLER = "buyer_to_seller", _("Buyer to Seller")
    SELLER_TO_BUYER = "seller_to_buyer", _("Seller to Buyer")


class DealRating(BaseModel):
    """One party's rating of the other party for one completed deal.

    Separate from the public ``AssetRating`` / ``SellerRating`` reviews. Each
    deal has at most one rating per direction; the uniqueness covers deleted
    rows too, so a rating can never be recreated. Write it through
    ``DealRatingService``.
    """

    deal = models.ForeignKey(
        "deals.CompletedDeal",
        on_delete=models.PROTECT,
        related_name="ratings",
    )
    direction = models.CharField(max_length=20, choices=DealRatingDirection.choices)
    rater_profile = models.ForeignKey(
        Profile,
        on_delete=models.PROTECT,
        related_name="deal_ratings_given",
    )
    rated_profile = models.ForeignKey(
        Profile,
        on_delete=models.PROTECT,
        related_name="deal_ratings_received",
    )
    stars = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(5),
        ],
    )
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["created", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["deal", "rater_profile"],
                name="deal_ratings_unique_deal_rater",
            ),
            models.UniqueConstraint(
                fields=["deal", "direction"],
                name="deal_ratings_unique_deal_direction",
            ),
            models.CheckConstraint(
                check=~Q(rater_profile=F("rated_profile")),
                name="deal_ratings_rater_not_rated",
            ),
            models.CheckConstraint(
                check=Q(stars__gte=1) & Q(stars__lte=5),
                name="deal_ratings_stars_1_to_5",
            ),
        ]

    def __str__(self):
        return f"DealRating #{self.pk} deal={self.deal_id} {self.direction}"
