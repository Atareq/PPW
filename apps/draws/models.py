from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class WeeklyDrawStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    OPEN = "OPEN", _("Open")
    CLOSED = "CLOSED", _("Closed")
    FINALIZED = "FINALIZED", _("Finalized")


class WeeklyDrawEffectiveStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    SCHEDULED = "SCHEDULED", _("Scheduled")
    OPEN = "OPEN", _("Open")
    ENDED = "ENDED", _("Ended")
    CLOSED = "CLOSED", _("Closed")
    FINALIZED = "FINALIZED", _("Finalized")


class WeeklyDrawEntrySource(models.TextChoices):
    ONBOARDING = "ONBOARDING", _("Onboarding")


class WeeklyDrawEntryStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    ASSIGNED = "ASSIGNED", _("Assigned")


class WeeklyDraw(BaseModel):
    """Historical weekly onboarding draw table retained for migration compatibility (BE-DRW-CANCEL-01).

    Product decision (BE-DRW-CANCEL-01):
    - The weekly onboarding draw feature is cancelled completely.
    - No runtime endpoints, onboarding hooks, winner selection, prizes, or
      notifications are active.
    - Historical models and migrations are retained so existing databases and
      committed migration history remain intact without destructive table drops.
    """

    title = models.CharField(max_length=150, blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=WeeklyDrawStatus.choices,
        default=WeeklyDrawStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_weekly_draws",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("starts_at", "id")
        indexes = [
            models.Index(
                fields=["status", "starts_at", "id"],
                name="draws_weekly_status_start_idx",
            ),
            models.Index(
                fields=["starts_at", "ends_at"],
                name="draws_weekly_window_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(starts_at__lt=models.F("ends_at")),
                name="draws_weekly_starts_before_ends_ck",
            ),
        ]

    def __str__(self) -> str:
        label = self.title or f"WeeklyDraw #{self.pk}"
        return f"{label} ({self.status})"

    def effective_status_at(self, now=None) -> str:
        now = now or timezone.now()
        if self.status == WeeklyDrawStatus.FINALIZED:
            return WeeklyDrawEffectiveStatus.FINALIZED
        if self.status == WeeklyDrawStatus.CLOSED:
            return WeeklyDrawEffectiveStatus.CLOSED
        if self.ends_at is not None and now >= self.ends_at:
            return WeeklyDrawEffectiveStatus.ENDED
        if self.status == WeeklyDrawStatus.OPEN:
            if (
                self.starts_at is not None
                and self.ends_at is not None
                and self.starts_at <= now < self.ends_at
            ):
                return WeeklyDrawEffectiveStatus.OPEN
            return WeeklyDrawEffectiveStatus.SCHEDULED
        return WeeklyDrawEffectiveStatus.DRAFT

    @property
    def effective_status(self) -> str:
        return self.effective_status_at()

    def is_currently_open_at(self, now=None) -> bool:
        now = now or timezone.now()
        return (
            self.deleted is None
            and self.status == WeeklyDrawStatus.OPEN
            and self.starts_at <= now < self.ends_at
        )

    def clean(self):
        errors = {}
        if self.starts_at is None:
            errors["starts_at"] = _("This field is required.")
        elif timezone.is_naive(self.starts_at):
            errors["starts_at"] = _("Datetime value must be timezone-aware.")

        if self.ends_at is None:
            errors["ends_at"] = _("This field is required.")
        elif timezone.is_naive(self.ends_at):
            errors["ends_at"] = _("Datetime value must be timezone-aware.")

        if (
            "starts_at" not in errors
            and "ends_at" not in errors
            and self.starts_at >= self.ends_at
        ):
            errors["ends_at"] = _(
                "Weekly draw start time must be earlier than end time."
            )

        if self.status not in WeeklyDrawStatus.values:
            errors["status"] = _("Invalid weekly draw status.")

        if self.metadata is None or not isinstance(self.metadata, dict):
            errors["metadata"] = _("Weekly draw metadata must be a JSON object.")

        if errors:
            raise DjangoValidationError(errors)

        if self.pk is not None:
            original = WeeklyDraw.all_objects.filter(pk=self.pk).first()
            if original is not None:
                schedule_changed = (
                    original.starts_at != self.starts_at
                    or original.ends_at != self.ends_at
                )
                if schedule_changed:
                    has_entries = self.entries.filter(deleted__isnull=True).exists()
                    now = timezone.now()
                    if (
                        original.status
                        in (WeeklyDrawStatus.CLOSED, WeeklyDrawStatus.FINALIZED)
                        or now >= original.starts_at
                        or has_entries
                    ):
                        raise DjangoValidationError(
                            {
                                "starts_at": _(
                                    "Weekly draw schedule cannot be modified after the draw has started or has assigned entries."
                                )
                            }
                        )
                if original.status == WeeklyDrawStatus.FINALIZED and self.status != WeeklyDrawStatus.FINALIZED:
                    raise DjangoValidationError(
                        {
                            "status": _(
                                "Finalized weekly draws cannot change status."
                            )
                        }
                    )
                if (
                    original.status == WeeklyDrawStatus.CLOSED
                    and self.status in (WeeklyDrawStatus.DRAFT, WeeklyDrawStatus.OPEN)
                ):
                    raise DjangoValidationError(
                        {
                            "status": _(
                                "Closed weekly draws cannot be reopened."
                            )
                        }
                    )

        if self.status == WeeklyDrawStatus.OPEN and self.deleted is None:
            overlapping_open = WeeklyDraw.objects.filter(
                deleted__isnull=True,
                status=WeeklyDrawStatus.OPEN,
                starts_at__lt=self.ends_at,
                ends_at__gt=self.starts_at,
            )
            if self.pk is not None:
                overlapping_open = overlapping_open.exclude(pk=self.pk)
            if overlapping_open.exists():
                raise DjangoValidationError(
                    {
                        "status": _(
                            "Another open weekly draw overlaps with this schedule window."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if (
            self.status in (WeeklyDrawStatus.CLOSED, WeeklyDrawStatus.FINALIZED)
            or (
                self.pk is not None
                and WeeklyDraw.all_objects.filter(
                    pk=self.pk,
                    status__in=(WeeklyDrawStatus.CLOSED, WeeklyDrawStatus.FINALIZED),
                ).exists()
            )
            or (self.pk is not None and self.entries.exists())
        ):
            raise ProtectedError(
                _(
                    "Cannot delete a weekly draw that is closed, finalized, or has assigned entries."
                ),
                list(self.entries.all()) or [self],
            )
        return super().delete(*args, **kwargs)


class WeeklyDrawEntry(BaseModel):
    """Historical onboarding weekly draw entry table retained for migration compatibility (BE-DRW-CANCEL-01).

    Product decision (BE-DRW-CANCEL-01):
    - The weekly onboarding draw feature is cancelled completely.
    - Account/profile onboarding no longer creates `WeeklyDrawEntry` rows.
    """

    draw = models.ForeignKey(
        WeeklyDraw,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entries",
    )
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="weekly_draw_entries",
    )
    source = models.CharField(
        max_length=30,
        choices=WeeklyDrawEntrySource.choices,
        default=WeeklyDrawEntrySource.ONBOARDING,
    )
    status = models.CharField(
        max_length=20,
        choices=WeeklyDrawEntryStatus.choices,
        default=WeeklyDrawEntryStatus.PENDING,
    )
    idempotency_key = models.CharField(max_length=160, unique=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("created", "id")
        indexes = [
            models.Index(
                fields=["status", "created", "id"],
                name="draws_entry_status_created_idx",
            ),
            models.Index(
                fields=["draw", "created", "id"],
                name="draws_entry_draw_created_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source"],
                name="draws_entry_unique_user_source",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        status=WeeklyDrawEntryStatus.PENDING,
                        draw__isnull=True,
                        assigned_at__isnull=True,
                    )
                    | models.Q(
                        status=WeeklyDrawEntryStatus.ASSIGNED,
                        draw__isnull=False,
                        assigned_at__isnull=False,
                    )
                ),
                name="draws_entry_status_draw_consistency_ck",
            ),
        ]

    def __str__(self) -> str:
        return f"WeeklyDrawEntry(user={self.user_id}, status={self.status}, draw={self.draw_id})"

    def clean(self):
        errors = {}
        if not self.idempotency_key or not str(self.idempotency_key).strip():
            errors["idempotency_key"] = _("Idempotency key is required.")
        if self.source not in WeeklyDrawEntrySource.values:
            errors["source"] = _("Invalid weekly draw entry source.")
        if self.status not in WeeklyDrawEntryStatus.values:
            errors["status"] = _("Invalid weekly draw entry status.")
        if self.metadata is None or not isinstance(self.metadata, dict):
            errors["metadata"] = _(
                "Weekly draw entry metadata must be a JSON object."
            )
        if self.assigned_at is not None and timezone.is_naive(self.assigned_at):
            errors["assigned_at"] = _("Datetime value must be timezone-aware.")

        if errors:
            raise DjangoValidationError(errors)

        if self.status == WeeklyDrawEntryStatus.PENDING:
            if self.draw_id is not None or self.assigned_at is not None:
                raise DjangoValidationError(
                    {
                        "status": _(
                            "Pending weekly draw entries cannot have an assigned draw or assigned_at timestamp."
                        )
                    }
                )
        elif self.status == WeeklyDrawEntryStatus.ASSIGNED:
            if self.draw_id is None or self.assigned_at is None:
                raise DjangoValidationError(
                    {
                        "status": _(
                            "Assigned weekly draw entries must have both draw and assigned_at set."
                        )
                    }
                )

        if self.pk is not None:
            original = (
                WeeklyDrawEntry.all_objects.select_related("draw")
                .filter(pk=self.pk)
                .first()
            )
            if (
                original is not None
                and original.status == WeeklyDrawEntryStatus.ASSIGNED
            ):
                if (
                    self.draw_id != original.draw_id
                    or self.status != WeeklyDrawEntryStatus.ASSIGNED
                    or self.user_id != original.user_id
                    or self.source != original.source
                    or self.assigned_at != original.assigned_at
                    or self.idempotency_key != original.idempotency_key
                    or self.deleted != original.deleted
                ):
                    raise DjangoValidationError(
                        {
                            "draw": _(
                                "An assigned weekly draw entry cannot be reassigned or reverted to pending."
                            )
                        }
                    )
                if (
                    original.draw is not None
                    and original.draw.status
                    in (WeeklyDrawStatus.CLOSED, WeeklyDrawStatus.FINALIZED)
                    and self.metadata != original.metadata
                ):
                    raise DjangoValidationError(
                        {
                            "draw": _(
                                "Entries belonging to a closed or finalized weekly draw are immutable."
                            )
                        }
                    )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if (
            self.status == WeeklyDrawEntryStatus.ASSIGNED
            or self.draw_id is not None
            or (
                self.pk is not None
                and WeeklyDrawEntry.all_objects.filter(
                    pk=self.pk,
                    status=WeeklyDrawEntryStatus.ASSIGNED,
                ).exists()
            )
        ):
            raise ProtectedError(
                _("Assigned weekly draw entries cannot be deleted."),
                [self],
            )
        return super().delete(*args, **kwargs)
