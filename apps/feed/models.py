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


class SellerPackageFollow(BaseModel):
    follower = models.ForeignKey(
        "users.CustomerProfile",
        on_delete=models.CASCADE,
        related_name="seller_package_follows",
    )
    seller_package = models.ForeignKey(
        "packages.SellerPackage",
        on_delete=models.CASCADE,
        related_name="customer_follows",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "seller_package"],
                name="feed_unique_follower_seller_package",
            ),
        ]
        indexes = [
            models.Index(
                fields=["follower", "is_active"],
                name="feed_pkg_fol_follower_active",
            ),
            models.Index(
                fields=["seller_package", "is_active"],
                name="feed_pkg_fol_package_active",
            ),
        ]
        ordering = ["-created"]

    def clean(self):
        if self.follower_id and self.seller_package_id:
            if self.follower.user_id == self.seller_package.owner.user_id:
                raise ValidationError(
                    {
                        "seller_package": (
                            "Customers cannot follow their own seller package."
                        )
                    }
                )

    def __str__(self):
        return f"SellerPackageFollow #{self.pk}"


class SellerCustomerBlock(BaseModel):
    customer = models.ForeignKey(
        "users.CustomerProfile",
        on_delete=models.CASCADE,
        related_name="seller_blocks",
    )
    seller = models.ForeignKey(
        "users.SellerProfile",
        on_delete=models.CASCADE,
        related_name="customer_blocks",
    )
    blocked_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="seller_customer_blocks_created",
    )
    reason = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "seller"],
                name="feed_unique_customer_seller_block",
            ),
        ]
        indexes = [
            models.Index(
                fields=["customer", "is_active"],
                name="feed_blk_customer_active",
            ),
            models.Index(
                fields=["seller", "is_active"],
                name="feed_blk_seller_active",
            ),
            models.Index(
                fields=["blocked_by", "created"],
                name="feed_blk_actor_created",
            ),
            models.Index(
                fields=["customer", "seller", "is_active"],
                name="feed_blk_pair_active",
            ),
        ]
        ordering = ["-created"]

    def clean(self):
        errors = {}
        if self.customer_id and self.seller_id:
            if self.customer.user_id == self.seller.user_id:
                errors["seller"] = "Customer and seller must belong to different users."
        if self.blocked_by_id and self.customer_id and self.seller_id:
            if self.blocked_by_id not in {self.customer_id, self.seller_id}:
                errors["blocked_by"] = (
                    "Block creator must be either the customer or seller."
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"SellerCustomerBlock #{self.pk}"
