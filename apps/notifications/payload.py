from django.utils.translation import gettext as _


class NotificationPayload:
    """Canonical request-independent representation for WebSocket and FCM payloads."""

    @staticmethod
    def build(notification) -> dict:
        actor_data = None
        actor = getattr(notification, "actor_profile", None)
        if actor is not None:
            actor_data = {
                "id": actor.pk,
                "type": actor.type,
            }
            nickname = getattr(actor, "nickname", "")
            if nickname:
                actor_data["nickname"] = nickname

        created_iso = (
            notification.created.isoformat() if notification.created else None
        )
        read_at_iso = (
            notification.read_at.isoformat() if notification.read_at else None
        )

        return {
            "id": notification.pk,
            "notification_type": notification.notification_type,
            "title": str(_(notification.title)),
            "body": str(_(notification.body)),
            "actor_profile": actor_data,
            "object_type": notification.object_type,
            "object_id": notification.object_id,
            "metadata": notification.metadata or {},
            "target_url": notification.target_url,
            "is_read": bool(notification.is_read),
            "read_at": read_at_iso,
            "created": created_iso,
        }
