import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, ValidationError

from apps.notifications.models import Notification
from apps.users.models import Profile

logger = logging.getLogger(__name__)


def notification_queryset():
    return Notification.objects.select_related(
        "recipient_profile",
        "actor_profile",
    ).order_by("-created", "-id")


def normalize_optional_string(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _publish_after_commit(notification_id):
    def publish():
        try:
            from apps.notifications.websocket import broadcast_notification

            notification = notification_queryset().get(pk=notification_id)
            broadcast_notification(notification)
        except Exception:
            logger.exception(
                "Failed to broadcast notification %s over WebSocket.",
                notification_id,
            )

        try:
            from apps.notifications.tasks import deliver_notification_push

            deliver_notification_push.delay(notification_id)
        except Exception:
            logger.exception(
                "Failed to enqueue FCM delivery for notification %s.",
                notification_id,
            )

    transaction.on_commit(publish)


class NotificationService:
    @staticmethod
    def create_notification(
        recipient_profile,
        notification_type,
        title,
        body,
        actor_profile=None,
        object_type=None,
        object_id=None,
        metadata=None,
        target_url=None,
        idempotency_key=None,
    ):
        recipient = NotificationService._resolve_profile(recipient_profile)
        if not NotificationService._is_eligible_recipient(recipient):
            return None

        actor = NotificationService._resolve_optional_profile(actor_profile)

        from django.utils.functional import Promise

        if isinstance(title, Promise):
            raw_title = getattr(title, "_proxy____args", (title,))[0]
        else:
            raw_title = title
        if isinstance(body, Promise):
            raw_body = getattr(body, "_proxy____args", (body,))[0]
        else:
            raw_body = body

        title = str(raw_title).strip()
        body = str(raw_body).strip()
        if not title:
            raise ValidationError({"title": _("Title is required.")})
        if not body:
            raise ValidationError({"body": _("Body is required.")})

        idempotency_key = normalize_optional_string(idempotency_key)
        if idempotency_key is not None:
            existing = (
                notification_queryset().filter(idempotency_key=idempotency_key).first()
            )
            if existing is not None:
                return existing

        try:
            notification = Notification.objects.create(
                recipient_profile=recipient,
                actor_profile=actor,
                notification_type=notification_type,
                title=title,
                body=body,
                object_type=normalize_optional_string(object_type),
                object_id=object_id,
                metadata=metadata or {},
                target_url=normalize_optional_string(target_url),
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            if idempotency_key is None:
                raise
            existing = (
                notification_queryset().filter(idempotency_key=idempotency_key).first()
            )
            if existing is not None:
                return existing
            raise

        _publish_after_commit(notification.pk)
        return notification

    @staticmethod
    def safe_create_notification(*args, **kwargs):
        try:
            return NotificationService.create_notification(*args, **kwargs)
        except Exception:
            logger.exception("Failed to create in-app notification.")
            return None

    @staticmethod
    @transaction.atomic
    def mark_as_read(notification, profile):
        recipient = NotificationService._resolve_profile(profile)
        notification_id = getattr(notification, "pk", notification)
        try:
            instance = Notification.objects.select_for_update().get(
                pk=notification_id,
                recipient_profile_id=recipient.pk,
            )
        except Notification.DoesNotExist as exc:
            raise NotFound(_("Notification was not found.")) from exc

        if instance.is_read:
            return NotificationService.get_notification(instance.pk)

        now = timezone.now()
        instance.is_read = True
        instance.read_at = now
        instance.save(update_fields=["is_read", "read_at", "modified"])
        return NotificationService.get_notification(instance.pk)

    @staticmethod
    def mark_all_as_read(profile):
        recipient = NotificationService._resolve_profile(profile)
        now = timezone.now()
        return Notification.objects.filter(
            recipient_profile_id=recipient.pk,
            is_read=False,
        ).update(is_read=True, read_at=now, modified=now)

    @staticmethod
    def get_unread_count(profile):
        recipient = NotificationService._resolve_profile(profile)
        return Notification.objects.filter(
            recipient_profile_id=recipient.pk,
            is_read=False,
        ).count()

    @staticmethod
    def get_notification(notification_id):
        return notification_queryset().get(pk=notification_id)

    @staticmethod
    def _resolve_profile(profile_or_id):
        profile_id = getattr(profile_or_id, "pk", profile_or_id)
        try:
            return Profile.objects.get(pk=profile_id)
        except Profile.DoesNotExist as exc:
            raise ValidationError({"profile": _("Profile was not found.")}) from exc

    @staticmethod
    def _resolve_optional_profile(profile_or_id):
        if profile_or_id in {None, ""}:
            return None
        return NotificationService._resolve_profile(profile_or_id)

    @staticmethod
    def _is_eligible_recipient(profile):
        return (
            profile.is_active
            and profile.activated
            and profile.is_valid
            and getattr(profile, "deleted", None) is None
        )
