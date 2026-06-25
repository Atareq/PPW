from django.core.exceptions import ValidationError
from django.db import models

from apps.general.models import BaseModel


class AnnouncementTarget(models.TextChoices):
    SELLER = "Seller", "Seller"
    CUSTOMER = "Customer", "Customer"


class Announcement(BaseModel):
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True)
    details = models.TextField()
    image = models.URLField(max_length=2048)
    target = models.CharField(
        max_length=20,
        choices=AnnouncementTarget.choices,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="created_announcements",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created", "-id"]
        indexes = [
            models.Index(
                fields=["target", "is_active", "-created"],
                name="announcemen_target_975ae2_idx",
            ),
            models.Index(fields=["is_active", "-created"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if not (self.title or "").strip():
            errors["title"] = "Announcement title is required."
        if not (self.details or "").strip():
            errors["details"] = "Announcement details are required."
        if not self.image:
            errors["image"] = "Announcement image is required."
        if self.target not in AnnouncementTarget.values:
            errors["target"] = "Announcement target must be Seller or Customer."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title
