import logging

from celery import shared_task

from apps.chat.models import Message
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

logger = logging.getLogger(__name__)


@shared_task(name="chat.tasks.send_chat_message_notification")
def send_chat_message_notification(message_id):
    """Create the committed receiver notification for a new chat message."""

    message = (
        Message.objects.select_related("sender", "receiver", "conversation")
        .filter(pk=message_id)
        .first()
    )
    if message is None:
        logger.info("Chat message %s was not found for notification.", message_id)
        return None

    logger.info(
        "Chat message notification created: message=%s receiver=%s sender=%s",
        message.pk,
        message.receiver_id,
        message.sender_id,
    )
    NotificationService.safe_create_notification(
        recipient_profile=message.receiver,
        actor_profile=message.sender,
        notification_type=NotificationType.CHAT_MESSAGE_RECEIVED,
        title="New message received",
        body=message.body,
        object_type="message",
        object_id=message.pk,
        metadata={
            "conversation_id": message.conversation_id,
            "message_id": message.pk,
        },
        idempotency_key=f"chat_message:{message.pk}",
    )
    return {
        "message_id": message.pk,
        "receiver_id": message.receiver_id,
        "sender_id": message.sender_id,
    }
