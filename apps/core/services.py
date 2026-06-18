from django.utils.timezone import now

from apps.core.models import Notification


class NotificationService:
    @staticmethod
    def create(
        profile,
        kind: str,
        title: str,
        body: str = "",
        metadata: dict | None = None,
        profile_type: str = "All",
    ) -> Notification:
        return Notification.objects.create(
            profile=profile,
            kind=kind,
            title=title,
            body=body,
            metadata=metadata or {},
            profile_type=profile_type,
        )

    @staticmethod
    def mark_read(notification_id: int, profile) -> Notification:
        notification = Notification.objects.get(
            pk=notification_id,
            profile=profile,
        )
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = now()
            notification.save(update_fields=["is_read", "read_at"])
        return notification

    @staticmethod
    def unread_count(profile) -> int:
        return Notification.objects.filter(
            profile=profile,
            is_read=False,
        ).count()
