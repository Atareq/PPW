import logging
import re

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.chat.models import Conversation, Message
from apps.chat.tasks import send_chat_message_notification
from apps.feed.services import BlockService, SellerFollowService
from apps.users.constants import ProfileType
from apps.users.models import Profile

logger = logging.getLogger(__name__)

CONTACT_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE),
    re.compile(
        r"https?://|www\.|(?:^|\s)[\w-]+\.[a-z]{2,}(?:/|\s|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:whats\s*app|whatsapp|wa\.me|telegram|t\.me)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)"),
]


def conversation_queryset():
    """Optimized queryset for conversation list/detail style responses."""

    return Conversation.objects.select_related(
        "customer",
        "seller",
        "last_message",
        "last_message__sender",
        "last_message__receiver",
    )


def message_queryset():
    """Optimized queryset for message list responses."""

    return Message.objects.select_related(
        "conversation",
        "sender",
        "receiver",
    ).order_by("created", "id")


class ChatService:
    """Direct profile-to-profile chat workflow."""

    @staticmethod
    @transaction.atomic
    def get_or_create_conversation(sender_profile, receiver_profile):
        sender = ChatService._get_profile(sender_profile)
        receiver = ChatService._get_profile(receiver_profile)

        if sender.pk == receiver.pk:
            raise ValidationError(
                {"receiver_profile_id": ("Cannot create a conversation with yourself.")}
            )

        if sender.user_id == receiver.user_id:
            raise ValidationError(
                {
                    "receiver_profile_id": (
                        "Cannot create a conversation with another profile "
                        "on your account."
                    )
                }
            )

        ChatService._ensure_active_profile(sender)
        ChatService._ensure_active_profile(receiver)
        customer, seller = ChatService._resolve_customer_seller(sender, receiver)
        if BlockService.is_blocked_between(customer, seller):
            raise ValidationError(
                {
                    "receiver_profile_id": (
                        "Cannot create a conversation while a block is active."
                    )
                }
            )

        existing = Conversation.objects.filter(
            customer_id=customer.pk,
            seller_id=seller.pk,
        ).first()
        if existing is not None:
            return existing

        if (
            sender.type == ProfileType.CUSTOMER
            and not SellerFollowService.is_following(
                customer,
                seller,
            )
        ):
            raise ValidationError(
                {
                    "receiver_profile_id": (
                        "You must follow this seller before starting a conversation."
                    )
                }
            )

        try:
            with transaction.atomic():
                conversation, _ = Conversation.objects.get_or_create(
                    customer_id=customer.pk,
                    seller_id=seller.pk,
                    defaults={"created_by": sender},
                )
            return conversation
        except IntegrityError:
            return Conversation.objects.get(
                customer_id=customer.pk,
                seller_id=seller.pk,
            )

    @staticmethod
    @transaction.atomic
    def send_message(conversation_id, sender_profile, body):
        sender = ChatService._get_active_profile(sender_profile)
        conversation = ChatService._get_locked_conversation(conversation_id)

        if not conversation.is_active:
            raise ValidationError("Conversation is not active.")

        ChatService._ensure_participant(conversation, sender)
        if BlockService.is_blocked_between(
            conversation.customer,
            conversation.seller,
        ):
            raise ValidationError("Cannot send messages while a block is active.")
        receiver = ChatService._other_participant(conversation, sender)
        ChatService._validate_no_contact_sharing(body)

        message = Message.objects.create(
            conversation=conversation,
            sender=sender,
            receiver=receiver,
            body=body,
        )
        conversation.last_message = message
        conversation.last_message_at = message.created
        conversation.save(
            update_fields=[
                "last_message",
                "last_message_at",
                "modified",
            ]
        )
        ChatService._enqueue_message_notification(message.pk)
        return ChatService.get_message(message.pk)

    @staticmethod
    @transaction.atomic
    def mark_messages_read(conversation_id, reader_profile):
        reader = ChatService._get_active_profile(reader_profile)
        conversation = ChatService._get_locked_conversation(conversation_id)
        ChatService._ensure_participant(conversation, reader)

        now = timezone.now()
        return Message.objects.filter(
            conversation=conversation,
            receiver=reader,
            is_read=False,
        ).update(is_read=True, read_at=now, modified=now)

    @staticmethod
    def get_conversation_for_profile(conversation_id, profile):
        profile = ChatService._get_active_profile(profile)
        try:
            return conversation_queryset().get(
                Q(customer_id=profile.pk) | Q(seller_id=profile.pk),
                pk=conversation_id,
            )
        except Conversation.DoesNotExist as exc:
            raise NotFound("Conversation was not found.") from exc

    @staticmethod
    def get_message(message_id):
        return message_queryset().get(pk=message_id)

    @staticmethod
    def _get_active_profile(profile_or_id):
        profile = ChatService._get_profile(profile_or_id)
        ChatService._ensure_active_profile(profile)
        return profile

    @staticmethod
    def _get_profile(profile_or_id):
        profile_id = getattr(profile_or_id, "pk", profile_or_id)
        try:
            return Profile.objects.get(pk=profile_id)
        except Profile.DoesNotExist as exc:
            raise ValidationError({"profile": "Profile was not found."}) from exc

    @staticmethod
    def _ensure_active_profile(profile):
        if (
            not profile.is_active
            or not profile.activated
            or not profile.is_valid
            or getattr(profile, "deleted", None) is not None
        ):
            raise ValidationError({"profile": "Profile is not active."})

    @staticmethod
    def _resolve_customer_seller(profile_a, profile_b):
        profile_types = {profile_a.type, profile_b.type}
        if profile_types != {ProfileType.CUSTOMER, ProfileType.SELLER}:
            raise ValidationError(
                {
                    "receiver_profile_id": (
                        "Conversations must be between one customer and one " "seller."
                    )
                }
            )

        if profile_a.type == ProfileType.CUSTOMER:
            return profile_a, profile_b
        return profile_b, profile_a

    @staticmethod
    def _get_locked_conversation(conversation_id):
        try:
            return (
                Conversation.objects.select_for_update()
                .select_related("customer", "seller")
                .get(pk=conversation_id)
            )
        except Conversation.DoesNotExist as exc:
            raise NotFound("Conversation was not found.") from exc

    @staticmethod
    def _ensure_participant(conversation, profile):
        if profile.pk not in {
            conversation.customer_id,
            conversation.seller_id,
        }:
            raise NotFound("Conversation was not found.")

    @staticmethod
    def _other_participant(conversation, profile):
        if conversation.customer_id == profile.pk:
            return conversation.seller
        return conversation.customer

    @staticmethod
    def _validate_no_contact_sharing(body):
        for pattern in CONTACT_PATTERNS:
            if pattern.search(body):
                raise ValidationError(
                    {
                        "body": (
                            "Sharing contact details or external links is not "
                            "allowed in chat messages."
                        )
                    }
                )

    @staticmethod
    def _enqueue_message_notification(message_id):
        def enqueue():
            try:
                send_chat_message_notification.delay(message_id)
            except Exception:
                logger.exception(
                    "Failed to enqueue chat message notification for message %s.",
                    message_id,
                )

        transaction.on_commit(enqueue)
