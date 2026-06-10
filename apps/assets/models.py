from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.general.models import OwnerBaseModel


class AssetCategory(models.TextChoices):
    BIRD = "bird", "Bird"
    SUPPLY = "supply", "Supply"
    ACCESSORY = "accessory", "Accessory"
    FEED = "feed", "Feed"
    SUPPLEMENT = "supplement", "Supplement"


class AssetStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    SOLD = "sold", "Sold"
    INACTIVE = "inactive", "Inactive"


class Asset(OwnerBaseModel):

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=20,
        choices=AssetCategory.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=AssetStatus.choices,
        default=AssetStatus.AVAILABLE,
    )
    count = models.IntegerField()
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    is_market_listed = models.BooleanField(default=False)
    is_for_auction = models.BooleanField(default=False)
    avg_rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    ratings_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["category"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self):
        return f" #{self.pk} Asset ({self.category})"


class AssetSubtype(Asset):
    CATEGORY = None

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.CATEGORY:
            self.category = self.CATEGORY

        super().save(*args, **kwargs)


class Bird(AssetSubtype):
    CATEGORY = AssetCategory.BIRD

    # Bird-specific info
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("young", "Young"),
    ]
    name = models.CharField(max_length=70)
    ring_number = models.CharField(max_length=100, unique=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    colour = models.CharField(max_length=40)
    birthday = models.DateField(blank=True, null=True)

    # Optional: bird family
    father = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children_from_father",
    )

    mother = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children_from_mother",
    )

    achievements = models.CharField(max_length=500)
    flying_speed = models.DecimalField(
        max_digits=6, decimal_places=2, blank=True, null=True
    )
    STAMINA_CHOICES = [
        ("excellent", "Excellent"),
        ("verygood", "VeryGood"),
        ("good", "Good"),
    ]
    stamina_ability = models.CharField(max_length=10, choices=STAMINA_CHOICES)

    class Meta:
        indexes = [
            models.Index(fields=["ring_number"]),
        ]

    def save(self, *args, **kwargs):
        self.count = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f" #{self.pk} Bird {self.ring_number} ({self.gender})"


class Supply(AssetSubtype):
    CATEGORY = AssetCategory.SUPPLY


class Accessory(AssetSubtype):
    CATEGORY = AssetCategory.ACCESSORY


class Feed(AssetSubtype):
    CATEGORY = AssetCategory.FEED


class Supplement(AssetSubtype):
    CATEGORY = AssetCategory.SUPPLEMENT
