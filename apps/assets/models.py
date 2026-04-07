from django.db import models
from apps.users.models import Profile
from apps.general.models import OwnerBaseModel


class Asset(OwnerBaseModel):

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    ASSET_TYPES = [
        ("bird", "Bird"),
        ("feed", "Feed"),
        ("accessory", "Accessory"),
        ("bundle", "Bundle"),
    ]
    asset_type = models.CharField(max_length=20, choices=ASSET_TYPES)

    STATUS_CHOICES = [
        ("available", "Available"),
        ("reserved", "Reserved"),
        ("sold", "Sold"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="available"
    )
    count = models.IntegerField()
    # 'thumbnail_url' => picture preview about the asset
    thumbnail_url = models.URLField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["asset_type"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.asset_type})"


class Bird(Asset):

    # Bird-specific info
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("young", "Young"),
    ]
    name = models.CharField(max_length=70)
    ring_number = models.CharField(max_length=100)
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

    acheivements = models.CharField(max_length=500)
    flying_speed = models.DecimalField(
        max_digits=6, decimal_places=2, blank=True, null=True
    )
    stamina_CHOICES = [
        ("excellent", "Excellent"),
        ("verygood", "VeryGood"),
        ("good", "Good"),
    ]
    stamina_ability = models.CharField(max_length=10, choices=stamina_CHOICES)

    class Meta:
        indexes = [
            models.Index(fields=["ring_number"]),
        ]

    def __str__(self):
        return f"{self.title} - {self.ring_number} ({self.gender})"
