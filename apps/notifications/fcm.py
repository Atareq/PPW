import json
import logging

import firebase_admin
from firebase_admin import credentials, messaging
from django.conf import settings

logger = logging.getLogger(__name__)


def _firebase_app():
    if not getattr(settings, "FIREBASE_CREDENTIALS_JSON", ""):
        return None
    try:
        return firebase_admin.get_app()
    except ValueError:
        payload = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
        return firebase_admin.initialize_app(credentials.Certificate(payload))


def send_notification_to_devices(notification, devices):
    app = _firebase_app()
    if app is None or not devices:
        return 0

    data = {
        "notification_id": str(notification.pk),
        "notification_type": notification.notification_type,
        "object_type": notification.object_type or "",
        "object_id": str(notification.object_id or ""),
        "target_url": notification.target_url or "",
        "metadata": json.dumps(notification.metadata or {}, ensure_ascii=False),
        "created_at": notification.created.isoformat(),
    }
    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=notification.title,
            body=notification.body,
        ),
        data=data,
        tokens=[device.token for device in devices],
    )
    response = messaging.send_each_for_multicast(message, app=app)

    for device, result in zip(devices, response.responses):
        if not result.success:
            logger.warning(
                "FCM delivery failed for device %s: %s",
                device.pk,
                result.exception,
            )

    return response.success_count
