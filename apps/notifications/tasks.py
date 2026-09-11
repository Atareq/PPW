from celery import shared_task


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def deliver_notification_push(self, notification_id):
    from apps.notifications.fcm import send_notification_to_devices
    from apps.notifications.models import Notification, PushDevice

    notification = Notification.objects.get(pk=notification_id)
    devices = list(
        PushDevice.objects.filter(
            profile_id=notification.recipient_profile_id,
            is_active=True,
        )
    )
    send_notification_to_devices(notification, devices)
