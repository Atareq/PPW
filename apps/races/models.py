from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.general.models import BaseModel


class Race(BaseModel):
    title = models.CharField(max_length=255)
    season_year = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    station_name = models.CharField(max_length=255)
    release_datetime = models.DateTimeField()
    weather_condition = models.TextField(blank=True)
    total_birds = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    competitors_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    planned_distance_km = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    coordinate_x = models.CharField(max_length=100, blank=True)
    coordinate_y = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["season_year"]),
            models.Index(fields=["release_datetime"]),
            models.Index(fields=["station_name"]),
        ]
        ordering = ["-release_datetime", "-created"]

    def __str__(self):
        return f"{self.title} ({self.season_year})"


class RaceResult(BaseModel):
    race = models.ForeignKey(
        Race,
        on_delete=models.CASCADE,
        related_name="results",
    )
    rank = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        editable=False,
    )
    competitor_name = models.CharField(max_length=255)
    bird_ring_number = models.CharField(max_length=100)
    distance_km = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    speed = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    arrival_datetime = models.DateTimeField()
    basket_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )

    class Meta:
        indexes = [
            models.Index(fields=["race", "rank"]),
            models.Index(fields=["bird_ring_number"]),
            models.Index(fields=["competitor_name"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["race", "bird_ring_number"],
                condition=models.Q(deleted__isnull=True),
                name="races_unique_ring_per_race",
            ),
            models.UniqueConstraint(
                fields=["race", "rank"],
                condition=models.Q(deleted__isnull=True),
                name="races_unique_rank_per_race",
            ),
            models.CheckConstraint(
                check=models.Q(rank__gte=1),
                name="races_result_rank_gte_1",
            ),
            models.CheckConstraint(
                check=models.Q(distance_km__gt=0),
                name="races_result_distance_gt_0",
            ),
            models.CheckConstraint(
                check=models.Q(speed__gt=0),
                name="races_result_speed_gt_0",
            ),
        ]
        ordering = ["rank", "id"]

    def __str__(self):
        return f"{self.race_id} #{self.rank} {self.bird_ring_number}"
