from datetime import timedelta

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.general.models import (
    AdminAnnouncement,
    AnnouncementDismissal,
    AnnouncementDurationUnit,
)


def _as_drf_validation_error(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


class AnnouncementService:
    """Business logic for AdminAnnouncement creation, visibility, and user dismissal."""

    @staticmethod
    def _resolve_creator_profile(user):
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        try:
            return user.active_profile
        except (DjangoPermissionDenied, PermissionDenied):
            return None

    @staticmethod
    def _resolve_expires_at(starts_at, expires_at=None, duration_value=None, duration_unit=None):
        if duration_value is not None and duration_unit is not None:
            if duration_unit == AnnouncementDurationUnit.HOURS:
                return starts_at + timedelta(hours=int(duration_value))
            if duration_unit == AnnouncementDurationUnit.DAYS:
                return starts_at + timedelta(days=int(duration_value))
            raise ValidationError(
                {"duration_unit": [_("Unsupported duration unit.")]}
            )
        return expires_at

    @classmethod
    @transaction.atomic
    def create_announcement(cls, validated_data: dict, created_by=None) -> AdminAnnouncement:
        data = dict(validated_data)
        starts_at = data.get("starts_at") or timezone.now()
        duration_value = data.pop("duration_value", None)
        duration_unit = data.pop("duration_unit", None)
        expires_at = cls._resolve_expires_at(
            starts_at=starts_at,
            expires_at=data.get("expires_at"),
            duration_value=duration_value,
            duration_unit=duration_unit,
        )

        announcement = AdminAnnouncement(
            text=data.get("text", ""),
            starts_at=starts_at,
            expires_at=expires_at,
            is_active=data.get("is_active", True),
            created_by=created_by,
        )
        try:
            announcement.save()
        except DjangoValidationError as exc:
            raise _as_drf_validation_error(exc) from exc
        return announcement

    @classmethod
    @transaction.atomic
    def update_announcement(
        cls,
        announcement: AdminAnnouncement,
        validated_data: dict,
    ) -> AdminAnnouncement:
        data = dict(validated_data)
        duration_value = data.pop("duration_value", None)
        duration_unit = data.pop("duration_unit", None)

        for field in ("text", "starts_at", "expires_at", "is_active"):
            if field in data:
                setattr(announcement, field, data[field])

        if duration_value is not None and duration_unit is not None:
            announcement.expires_at = cls._resolve_expires_at(
                starts_at=announcement.starts_at,
                duration_value=duration_value,
                duration_unit=duration_unit,
            )

        try:
            announcement.save()
        except DjangoValidationError as exc:
            raise _as_drf_validation_error(exc) from exc
        return announcement

    @staticmethod
    @transaction.atomic
    def delete_announcement(announcement: AdminAnnouncement) -> None:
        announcement.delete()

    @staticmethod
    def visible_for_user(user, now=None):
        """Return active, unexpired announcements not dismissed by `user`."""
        now = now or timezone.now()
        return (
            AdminAnnouncement.objects.filter(
                deleted__isnull=True,
                is_active=True,
                starts_at__lte=now,
                expires_at__gt=now,
            )
            .exclude(
                dismissals__user=user,
                dismissals__deleted__isnull=True,
            )
            .order_by("-starts_at", "-id")
        )

    @staticmethod
    @transaction.atomic
    def dismiss(announcement_id: int, user) -> AnnouncementDismissal:
        """Idempotently record a UserAccount-scoped dismissal for an announcement."""
        announcement = (
            AdminAnnouncement.objects.filter(
                deleted__isnull=True,
                pk=announcement_id,
            ).first()
        )
        if announcement is None:
            raise NotFound(_("Announcement was not found."))

        existing = (
            AnnouncementDismissal.all_objects.select_for_update()
            .filter(
                announcement=announcement,
                user=user,
            )
            .first()
        )
        if existing is not None:
            if existing.deleted is not None:
                existing.undelete()
            return existing

        try:
            with transaction.atomic():
                return AnnouncementDismissal.objects.create(
                    announcement=announcement,
                    user=user,
                    dismissed_at=timezone.now(),
                )
        except IntegrityError:
            existing = AnnouncementDismissal.all_objects.get(
                announcement=announcement,
                user=user,
            )
            if existing.deleted is not None:
                existing.undelete()
            return existing

