import logging

from celery import shared_task

from apps.chat.models import Message

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

    logger.info(
        "Chat message notification placeholder: message=%s receiver=%s sender=%s",
        message.pk,
        message.receiver_id,
        message.sender_id,
    )
    return {
        "message_id": message.pk,
        "receiver_id": message.receiver_id,
        "sender_id": message.sender_id,
    }
