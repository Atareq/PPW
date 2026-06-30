from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class Conversation(BaseModel):
    customer = models.ForeignKey(
        "users.CustomerProfile",
        on_delete=models.CASCADE,
        related_name="chat_conversations_as_customer",
    )
    seller = models.ForeignKey(
        "users.SellerProfile",
        on_delete=models.CASCADE,
        related_name="chat_conversations_as_seller",
    )
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="created_conversations",
    )
    last_message = models.ForeignKey(
        "chat.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "seller"],
                name="chat_unique_conversation_customer_seller",
            ),
        ]
        indexes = [
            models.Index(
                fields=["customer", "last_message_at"],
                name="chat_conv_customer_last_idx",
            ),
            models.Index(
                fields=["seller", "last_message_at"],
                name="chat_conv_seller_last_idx",
            ),
            models.Index(
                fields=["last_message_at"],
                name="chat_conv_last_msg_at_idx",
            ),
        ]
        ordering = ["-last_message_at", "-created"]

    def clean(self):
        if (
            self.customer_id
            and self.seller_id
            and self.customer.user_id == self.seller.user_id
        ):
            raise ValidationError(
                {"seller": _("Customer and seller must belong to different users.")}
            )

    def __str__(self):
        return f"Conversation #{self.pk}"


class Message(BaseModel):
    MAX_BODY_LENGTH = 2000

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="sent_chat_messages",
    )
    receiver = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="received_chat_messages",
    )
    body = models.TextField(max_length=MAX_BODY_LENGTH)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "created"]),
            models.Index(fields=["receiver", "is_read"]),
            models.Index(fields=["sender", "created"]),
            models.Index(fields=["created"]),
        ]
        ordering = ["created"]

    def clean(self):
        participant_ids = {
            self.conversation.customer_id,
            self.conversation.seller_id,
        }
        if self.sender_id not in participant_ids:
            raise ValidationError(
                {"sender": _("Sender must be a conversation participant.")}
            )
        if self.receiver_id not in participant_ids:
            raise ValidationError(
                {"receiver": _("Receiver must be a conversation participant.")}
            )
        if self.sender_id == self.receiver_id:
            raise ValidationError(
                {"receiver": _("Receiver must be the other participant.")}
            )

    def __str__(self):
        return f"Message #{self.pk}"
