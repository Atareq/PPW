from django.core.exceptions import ValidationError
from django.db import models

from apps.general.models import BaseModel


class SellerFollow(BaseModel):
    follower = models.ForeignKey(
        "users.CustomerProfile",
        on_delete=models.CASCADE,
        related_name="seller_follows",
    )
    seller = models.ForeignKey(
        "users.SellerProfile",
        on_delete=models.CASCADE,
        related_name="customer_follows",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "seller"],
                name="feed_unique_follower_seller",
            ),
        ]
        indexes = [
            models.Index(
                fields=["follower", "is_active"],
                name="feed_fol_follower_active",
            ),
            models.Index(
                fields=["seller", "is_active"],
                name="feed_fol_seller_active",
            ),
            models.Index(
                fields=["follower", "created"],
                name="feed_fol_follower_created",
            ),
            models.Index(
                fields=["seller", "created"],
                name="feed_fol_seller_created",
            ),
        ]
        ordering = ["-created"]

    def clean(self):
        if self.follower_id and self.seller_id:
            if self.follower.user_id == self.seller.user_id:
                raise ValidationError(
                    {"seller": ("Customers cannot follow their own seller profile.")}
                )

    def __str__(self):
        return f"SellerFollow #{self.pk}"
