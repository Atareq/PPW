import logging

from celery import shared_task

from apps.chat.models import Message
from apps.core.models import NotificationKind
from apps.core.services import NotificationService

logger = logging.getLogger(__name__)


@shared_task(name="chat.tasks.send_chat_message_notification")
def send_chat_message_notification(message_id):
    """Safe placeholder for future in-app chat notifications."""

    message = (
        Message.objects.select_related("sender", "receiver", "conversation")
        .filter(pk=message_id)
        .first()
    )
    if message is None:
        logger.info("Chat message %s was not found for notification.", message_id)
        return None

    notification = NotificationService.create(
        profile=message.receiver,
        kind=NotificationKind.CHAT,
        title="New message",
        body=message.body[:140],
        metadata={
            "conversation_id": message.conversation_id,
            "message_id": message.pk,
            "sender_id": message.sender_id,
        },
        profile_type=message.receiver.type,
    )
    logger.info(
        "Chat message notification created: notification=%s message=%s",
        notification.pk,
        message.pk,
    )
    return {
        "notification_id": notification.pk,
        "message_id": message.pk,
        "receiver_id": message.receiver_id,
        "sender_id": message.sender_id,
    }
