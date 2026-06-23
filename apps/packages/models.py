from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.general.models import BaseModel, OwnerBaseModel
from apps.packages.validators import PackageValidationRules
from apps.users.models import SellerProfile


class SellerPackageStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    EXHAUSTED = "exhausted", "Exhausted"
    CANCELLED = "cancelled", "Cancelled"


class PackageUsageType(models.TextChoices):
    AUCTION_CREATED = "auction_created", "Auction Created"
    PRODUCT_CREATED = "product_created", "Product Created"
    PEDIGREE_CREATED = "pedigree_created", "Pedigree Created"


class Package(BaseModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    points = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    activation_period_days = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )
    auction_cost = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    product_cost = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    is_available = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_available"]),
            models.Index(fields=["-created"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(points__gt=0),
                name="packages_package_points_gt_0",
            ),
            models.CheckConstraint(
                check=Q(price__gte=0),
                name="packages_package_price_gte_0",
            ),
            models.CheckConstraint(
                check=Q(activation_period_days__gt=0),
                name="packages_package_period_gt_0",
            ),
            models.CheckConstraint(
                check=Q(auction_cost__gt=0),
                name="packages_package_auction_cost_gt_0",
            ),
            models.CheckConstraint(
                check=Q(product_cost__gt=0),
                name="packages_package_product_cost_gt_0",
            ),
            models.CheckConstraint(
                check=Q(auction_cost__lte=F("points")),
                name="packages_package_auction_cost_lte_points",
            ),
            models.CheckConstraint(
                check=Q(product_cost__lte=F("points")),
                name="packages_package_product_cost_lte_points",
            ),
        ]

    def clean(self):
        errors = PackageValidationRules.build_errors(
            {
                "points": self.points,
                "price": self.price,
                "activation_period_days": self.activation_period_days,
                "auction_cost": self.auction_cost,
                "product_cost": self.product_cost,
            }
        )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Package #{self.pk} {self.name}"


class SellerPackage(OwnerBaseModel):
    owner = models.ForeignKey(
        SellerProfile,
        on_delete=models.CASCADE,
        related_name="seller_packages",
        db_index=True,
    )
    package = models.ForeignKey(
        Package,
        on_delete=models.PROTECT,
        related_name="seller_packages",
    )
    remaining_points = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    activated = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=SellerPackageStatus.choices,
        default=SellerPackageStatus.PENDING,
    )

    class Meta:
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["status", "activated"]),
            models.Index(fields=["expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner"],
                condition=Q(
                    status=SellerPackageStatus.ACTIVE,
                    activated=True,
                    deleted__isnull=True,
                ),
                name="packages_one_active_seller_package",
            ),
            models.CheckConstraint(
                check=Q(remaining_points__gte=0),
                name="packages_sellerpackage_remaining_gte_0",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.remaining_points is None and self.package_id:
            self.remaining_points = self.package.points
        super().save(*args, **kwargs)

    def __str__(self):
        return f"SellerPackage #{self.pk} ({self.status})"


class AssetSellerPackage(SellerPackage):
    class Meta:
        verbose_name = "asset seller package"
        verbose_name_plural = "asset seller packages"


class PedigreeSellerPackage(SellerPackage):
    class Meta:
        verbose_name = "pedigree seller package"
        verbose_name_plural = "pedigree seller packages"


class PackageUsageLog(BaseModel):
    seller_package = models.ForeignKey(
        SellerPackage,
        on_delete=models.CASCADE,
        related_name="usage_logs",
    )
    owner = models.ForeignKey(
        SellerProfile,
        on_delete=models.CASCADE,
        related_name="package_usage_logs",
        db_index=True,
    )
    usage_type = models.CharField(
        max_length=30,
        choices=PackageUsageType.choices,
    )
    points_used = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    related_object_type = models.CharField(max_length=100, blank=True)
    related_object_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "-created"]),
            models.Index(fields=["seller_package", "-created"]),
            models.Index(fields=["usage_type"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(points_used__gt=0),
                name="packages_usagelog_points_used_gt_0",
            ),
        ]

    def __str__(self):
        return f"PackageUsageLog #{self.pk} ({self.usage_type})"
