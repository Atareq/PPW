from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class SeasonStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    CALCULATED = "calculated", _("Calculated")
    FINALIZED = "finalized", _("Finalized")


class Season(BaseModel):
    title = models.CharField(max_length=255)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=SeasonStatus.choices,
        default=SeasonStatus.DRAFT,
    )
    calculated_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        "users.ManagerProfile",
        on_delete=models.SET_NULL,
        related_name="finalized_seasons",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-starts_at", "-id"]

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError({"ends_at": _("Season end must be after start.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SeasonSellerInsight(BaseModel):
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="seller_insights",
    )
    seller = models.ForeignKey(
        "users.SellerProfile",
        on_delete=models.CASCADE,
        related_name="season_insights",
    )
    completed_orders_count = models.PositiveIntegerField(default=0)
    avg_rating = models.DecimalField(max_digits=3, decimal_places=2)
    total_sales_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    rank = models.PositiveIntegerField(null=True, blank=True)
    points_awarded = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["rank", "-completed_orders_count", "-avg_rating"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "seller"],
                name="season_unique_seller_insight",
            ),
        ]
