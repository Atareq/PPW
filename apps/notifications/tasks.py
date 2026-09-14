import logging

from celery import shared_task

from apps.notifications.fcm import TransientPushError

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(TransientPushError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def deliver_notification_push(self, notification_id):
    from apps.notifications.fcm import send_notification_to_devices
    from apps.notifications.models import Notification, PushDevice

    try:
        notification = Notification.objects.get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.warning(
            "Notification #%s does not exist; skipping FCM delivery.",
            notification_id,
        )
        return 0

    devices = list(
        PushDevice.objects.filter(
            profile_id=notification.recipient_profile_id,
            is_active=True,
        )
    )
    if not devices:
        return 0

    return send_notification_to_devices(notification, devices)
