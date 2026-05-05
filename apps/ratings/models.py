from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.assets.models import Asset
from apps.general.models import BaseModel, OwnerBaseModel
from apps.users.models import CustomerProfile, SellerProfile


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
