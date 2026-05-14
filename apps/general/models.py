from django.db import models
from django_extensions.db.models import TimeStampedModel
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


class Image(BaseModel):
    image_url = models.CharField(max_length=2048)
    alt_text = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.image_url


class AssetImage(Image):
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="images",
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
