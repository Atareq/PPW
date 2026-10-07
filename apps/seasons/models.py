from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel

DEFAULT_SEASON_PRIZE_POINTS = {
    1: 100000,
    2: 50000,
    3: 25000,
}


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

    def __init__(self, *args, **kwargs):
        has_finalized_at = "finalized_at" in kwargs
        finalized_at = kwargs.pop("finalized_at", None)
        super().__init__(*args, **kwargs)
        if has_finalized_at:
            self.finalized_at = finalized_at

    @property
    def finalized_at(self):
        raw_value = (self.metadata or {}).get("finalized_at")
        if not raw_value:
            return None
        if isinstance(raw_value, datetime):
            return raw_value
        return parse_datetime(str(raw_value))

    @finalized_at.setter
    def finalized_at(self, value):
        metadata = dict(self.metadata or {})
        if value is None:
            metadata.pop("finalized_at", None)
        elif isinstance(value, datetime):
            metadata["finalized_at"] = value.isoformat()
        else:
            metadata["finalized_at"] = str(value)
        self.metadata = metadata

    def clean(self):
        super().clean()
        if self.pk:
            previous = (
                Season.all_objects.filter(pk=self.pk)
                .values("status", "starts_at", "ends_at")
                .first()
            )
            if previous and previous["status"] == SeasonStatus.FINALIZED:
                errors = {}
                if self.starts_at != previous["starts_at"]:
                    errors["starts_at"] = _(
                        "Finalized season dates cannot be modified."
                    )
                if self.ends_at != previous["ends_at"]:
                    errors["ends_at"] = _(
                        "Finalized season dates cannot be modified."
                    )
                if errors:
                    raise ValidationError(errors)
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError({"ends_at": _("Season end must be after start.")})

    def ensure_default_prizes(self, created_by=None):
        existing_ranks = set(
            self.prizes.filter(deleted__isnull=True).values_list("rank", flat=True)
        )
        for rank, pp_points in DEFAULT_SEASON_PRIZE_POINTS.items():
            if rank not in existing_ranks:
                prize = SeasonPrize(
                    season=self,
                    rank=rank,
                    pp_points=pp_points,
                    symbolic_prize_code="",
                    title="",
                    metadata={},
                    created_by=created_by,
                    modified_by=created_by,
                )
                prize._allow_system_write = True
                prize.save()

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        self.full_clean()
        super().save(*args, **kwargs)
        if is_new and not getattr(self, "_skip_default_prizes", False):
            self.ensure_default_prizes(
                created_by=getattr(self, "_created_by_manager", None),
            )


class SeasonPrize(BaseModel):
    """Per-season Rank 1..3 prize definition (BE-SEA-03).

    Configured before ``season.starts_at`` while ``season.status == DRAFT``.
    Frozen once the season starts, is calculated, or is finalized.
    Only ``pp_points`` executes automatically at finalization through
    ``PointService.earn_points``; ``symbolic_prize_code`` and ``title`` are
    optional descriptive metadata without automatic fulfillment.
    """

    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="prizes",
    )
    rank = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(3)],
    )
    pp_points = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )
    symbolic_prize_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        "users.ManagerProfile",
        on_delete=models.SET_NULL,
        related_name="created_season_prizes",
        null=True,
        blank=True,
    )
    modified_by = models.ForeignKey(
        "users.ManagerProfile",
        on_delete=models.SET_NULL,
        related_name="modified_season_prizes",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["rank", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "rank"],
                condition=Q(deleted__isnull=True),
                name="season_prize_rank_uniq",
            ),
            models.CheckConstraint(
                check=Q(rank__gte=1, rank__lte=3),
                name="season_prize_rank_range",
            ),
            models.CheckConstraint(
                check=Q(pp_points__gte=0),
                name="season_prize_pp_points_gte_0",
            ),
        ]

    @staticmethod
    def validate_season_editable(season):
        if season.status == SeasonStatus.FINALIZED:
            raise ValidationError(
                {"detail": _("Finalized season prizes cannot be modified.")}
            )
        if season.status != SeasonStatus.DRAFT:
            raise ValidationError(
                {
                    "detail": _(
                        "Season prizes can only be modified while the season is in draft."
                    )
                }
            )
        if season.starts_at and timezone.now() >= season.starts_at:
            raise ValidationError(
                {
                    "detail": _(
                        "Season prizes cannot be modified after the season starts."
                    )
                }
            )

    def clean(self):
        super().clean()
        errors = {}
        if self.rank is None or self.rank < 1 or self.rank > 3:
            errors["rank"] = _("Season prize rank must be between 1 and 3.")
        if self.pp_points is None or self.pp_points < 0:
            errors["pp_points"] = _(
                "Season prize points must be zero or positive."
            )
        if errors:
            raise ValidationError(errors)

        if self.season_id and self.deleted is None:
            duplicate_qs = SeasonPrize.objects.filter(
                season_id=self.season_id,
                rank=self.rank,
                deleted__isnull=True,
            )
            if self.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.pk)
            if duplicate_qs.exists():
                raise ValidationError(
                    {
                        "rank": _(
                            "A prize for this rank already exists in this season."
                        )
                    }
                )
            if not getattr(self, "_allow_system_write", False):
                SeasonPrize.validate_season_editable(self.season)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.season_id and not getattr(self, "_allow_system_write", False):
            SeasonPrize.validate_season_editable(self.season)
        return super().delete(*args, **kwargs)


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
    completed_sales_count = models.PositiveIntegerField(default=0)
    sales_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    ratings_count = models.PositiveIntegerField(default=0)
    avg_rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    rating_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    seller_cancellations_count = models.PositiveIntegerField(default=0)
    reliability_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    interaction_opportunities_count = models.PositiveIntegerField(default=0)
    interaction_replied_count = models.PositiveIntegerField(default=0)
    interaction_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    season_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    rank = models.PositiveIntegerField(null=True, blank=True)
    completed_orders_count = models.PositiveIntegerField(default=0)
    total_sales_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    points_awarded = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["rank", "-season_score", "seller_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "seller"],
                name="season_unique_seller_insight",
            ),
        ]
