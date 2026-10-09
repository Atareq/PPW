from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django_extensions.db.models import TimeStampedModel
from django.utils.translation import gettext_lazy as _
from safedelete.models import SafeDeleteModel


class BaseModel(TimeStampedModel, SafeDeleteModel):
    class Meta:
        abstract = True


class OwnerBaseModel(BaseModel):
    owner = models.ForeignKey(
        "users.profile",
        on_delete=models.CASCADE,
        related_name="owned_%(class)ss",
        db_index=True,
    )

    class Meta:
        abstract = True


class MediaType(models.TextChoices):
    IMAGE = "image", _("Image")
    VIDEO = "video", _("Video")


class Media(BaseModel):
    media_url = models.CharField(max_length=2048)
    media_type = models.CharField(
        max_length=10,
        choices=MediaType.choices,
        default=MediaType.IMAGE,
    )
    alt_text = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.media_url


class AssetMedia(Media):
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="media",
        db_index=False,
    )
    is_primary = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "created"]
        indexes = [
            models.Index(fields=["asset"]),
            models.Index(fields=["asset", "is_primary"]),
        ]


class ResourcePermission(models.Model):
    resource = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey(
        "authentication.UserAccount", on_delete=models.CASCADE, db_index=True
    )

    def __str__(self):
        return self.resource


class AnnouncementDurationUnit(models.TextChoices):
    HOURS = "hours", _("Hours")
    DAYS = "days", _("Days")


class AdminAnnouncement(BaseModel):
    """Temporary admin-controlled announcement bar message shown to users."""

    text = models.TextField()
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="created_admin_announcements",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-starts_at", "-id"]
        indexes = [
            models.Index(
                fields=["is_active", "starts_at", "expires_at"],
                name="general_ann_active_win_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(starts_at__lt=models.F("expires_at")),
                name="general_ann_starts_before_exp_ck",
            ),
        ]

    @property
    def published_at(self):
        return self.starts_at

    def clean(self):
        super().clean()
        errors = {}
        if not self.text or not str(self.text).strip():
            errors["text"] = _("Announcement text is required.")
        if self.starts_at is not None and timezone.is_naive(self.starts_at):
            errors["starts_at"] = _(
                "Announcement start time must be timezone-aware."
            )
        if self.expires_at is not None and timezone.is_naive(self.expires_at):
            errors["expires_at"] = _(
                "Announcement expiration time must be timezone-aware."
            )
        if (
            self.starts_at is not None
            and self.expires_at is not None
            and "starts_at" not in errors
            and "expires_at" not in errors
            and self.starts_at >= self.expires_at
        ):
            errors["expires_at"] = _(
                "Announcement expiration time must be later than start time."
            )
        if errors:
            raise ValidationError(errors)

    def is_currently_active(self, now=None) -> bool:
        now = now or timezone.now()
        return (
            self.deleted is None
            and self.is_active
            and self.starts_at <= now < self.expires_at
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"AdminAnnouncement #{self.pk}"


class AnnouncementDismissal(BaseModel):
    """Per-UserAccount dismissal record for an AdminAnnouncement."""

    announcement = models.ForeignKey(
        AdminAnnouncement,
        on_delete=models.CASCADE,
        related_name="dismissals",
    )
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="announcement_dismissals",
    )
    dismissed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-dismissed_at", "-id"]
        indexes = [
            models.Index(
                fields=["user", "announcement"],
                name="general_ann_dism_user_ann_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "user"],
                name="general_ann_dismissal_unique_user",
            ),
        ]

    def __str__(self):
        return (
            f"AnnouncementDismissal #{self.pk} "
            f"({self.announcement_id} -> {self.user_id})"
        )

