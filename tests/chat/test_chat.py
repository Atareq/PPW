from unittest import mock

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
import pytest
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.chat.models import Conversation, Message
from apps.chat.services import ChatService
from apps.chat.tasks import send_chat_message_notification
from apps.feed.services import BlockService
from apps.users.constants import ProfileType
from tests.factories import (
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


def create_users():
    sender = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    receiver = UserAccountFactory(with_profile=ProfileType.SELLER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    return sender, receiver, other


class TestChatService:
    def test_customer_can_start_conversation_with_seller(self):
        customer, seller, _ = create_users()

        conversation = ChatService.get_or_create_conversation(
            customer.active_profile,
            seller.active_profile,
        )

        assert conversation.customer_id == customer.active_profile.pk
        assert conversation.seller_id == seller.active_profile.pk
        assert conversation.created_by_id == customer.active_profile.pk

    def test_seller_can_start_conversation_with_customer(self):
        customer, seller, _ = create_users()

        conversation = ChatService.get_or_create_conversation(
            seller.active_profile,
            customer.active_profile,
        )

        assert conversation.customer_id == customer.active_profile.pk
        assert conversation.seller_id == seller.active_profile.pk
        assert conversation.created_by_id == seller.active_profile.pk

    def test_cannot_create_conversation_with_same_profile(self):
        sender, _, _ = create_users()

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                sender.active_profile,
                sender.active_profile,
            )

    def test_customer_cannot_chat_with_customer(self):
        sender, _, other = create_users()

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                sender.active_profile,
                other.active_profile,
            )

    def test_seller_cannot_chat_with_seller(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        other_seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                seller.active_profile,
                other_seller.active_profile,
            )

    def test_manager_cannot_chat_with_customer(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        customer, _, _ = create_users()

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                manager.active_profile,
                customer.active_profile,
            )

    def test_manager_cannot_chat_with_seller(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        _, seller, _ = create_users()

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                manager.active_profile,
                seller.active_profile,
            )

    def test_user_cannot_chat_with_own_other_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_profile = SellerProfileFactory(user=user, is_active=False)

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                user.active_profile,
                seller_profile,
            )

    def test_blocked_customer_seller_cannot_create_conversation(self):
        customer, seller, _ = create_users()
        BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
        )

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                customer.active_profile,
                seller.active_profile,
            )

    def test_blocked_customer_seller_cannot_send_message_and_history_remains(self):
        customer, seller, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            customer.active_profile,
            seller.active_profile,
        )
        old_message = ChatService.send_message(
            conversation.pk,
            customer.active_profile,
            "Before block",
        )
        BlockService.block_customer_seller(
            seller.active_profile,
            customer.active_profile,
        )

        with pytest.raises(ValidationError):
            ChatService.send_message(
                conversation.pk,
                customer.active_profile,
                "After block",
            )

        assert Message.objects.filter(pk=old_message.pk).exists()
        assert Message.objects.count() == 1

    def test_conversation_stores_customer_and_seller(self):
        sender, receiver, _ = create_users()

        conversation = ChatService.get_or_create_conversation(
            receiver.active_profile,
            sender.active_profile,
        )

        assert conversation.customer_id == sender.active_profile.pk
        assert conversation.seller_id == receiver.active_profile.pk

    def test_duplicate_conversation_returns_existing_one(self):
        sender, receiver, _ = create_users()

        first = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        second = ChatService.get_or_create_conversation(
            receiver.active_profile,
            sender.active_profile,
        )

        assert first.pk == second.pk
        assert Conversation.objects.count() == 1

    def test_unique_customer_seller_constraint_prevents_duplicates(self):
        sender, receiver, _ = create_users()
        ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Conversation.objects.create(
                    customer_id=sender.active_profile.pk,
                    seller_id=receiver.active_profile.pk,
                    created_by=sender.active_profile,
                )

    def test_inactive_receiver_profile_is_rejected(self):
        sender, _, _ = create_users()
        inactive = SellerProfileFactory(activated=False, is_active=True)

        with pytest.raises(ValidationError):
            ChatService.get_or_create_conversation(
                sender.active_profile,
                inactive,
            )

    def test_message_sender_must_be_participant(self):
        sender, receiver, other = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        with pytest.raises(NotFound):
            ChatService.send_message(
                conversation.pk,
                other.active_profile,
                "Hello",
            )

    def test_receiver_is_derived_from_conversation(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        message = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "Hello there",
        )

        assert message.sender_id == sender.active_profile.pk
        assert message.receiver_id == receiver.active_profile.pk

    def test_receiver_is_derived_when_seller_sends(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        message = ChatService.send_message(
            conversation.pk,
            receiver.active_profile,
            "Hello back",
        )

        assert message.sender_id == receiver.active_profile.pk
        assert message.receiver_id == sender.active_profile.pk

    def test_message_clean_requires_conversation_participants(self):
        sender, receiver, other = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        message = Message(
            conversation=conversation,
            sender=other.active_profile,
            receiver=receiver.active_profile,
            body="Hello",
        )

        with pytest.raises(DjangoValidationError):
            message.clean()

    @pytest.mark.parametrize(
        "body",
        [
            "Call me at +201001234567",
            "Email me at buyer@example.com",
            "Open https://example.com",
            "message me on WhatsApp",
            "wa.me/201001234567",
            "telegram t.me/user",
        ],
    )
    def test_contact_sharing_content_is_rejected(self, body):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        with pytest.raises(ValidationError):
            ChatService.send_message(
                conversation.pk,
                sender.active_profile,
                body,
            )

    def test_sending_message_updates_conversation_last_message(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        message = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "Hello",
        )
        conversation.refresh_from_db()

        assert conversation.last_message_id == message.pk
        assert conversation.last_message_at == message.created

    def test_mark_messages_read_marks_only_receiver_messages(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        first = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "Hello",
        )
        second = ChatService.send_message(
            conversation.pk,
            receiver.active_profile,
            "Reply",
        )

        updated = ChatService.mark_messages_read(
            conversation.pk,
            receiver.active_profile,
        )
        first.refresh_from_db()
        second.refresh_from_db()

        assert updated == 1
        assert first.is_read is True
        assert first.read_at is not None
        assert second.is_read is False


class TestChatApi:
    def test_authenticated_profile_can_create_conversation(self):
        sender, receiver, _ = create_users()
        client = authenticated_client(sender)

        response = client.post(
            "/api/chat/conversations/",
            {"receiver_profile_id": receiver.active_profile.pk},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["customer"]["id"] == sender.active_profile.pk
        assert response.data["seller"]["id"] == receiver.active_profile.pk
        assert response.data["other_participant"]["id"] == receiver.active_profile.pk
        assert "participant_1" not in response.data
        assert "participant_2" not in response.data

    def test_duplicate_create_returns_same_conversation(self):
        sender, receiver, _ = create_users()
        client = authenticated_client(sender)

        first = client.post(
            "/api/chat/conversations/",
            {"receiver_profile_id": receiver.active_profile.pk},
            format="json",
        )
        second = client.post(
            "/api/chat/conversations/",
            {"receiver_profile_id": receiver.active_profile.pk},
            format="json",
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.data["id"] == second.data["id"]
        assert Conversation.objects.count() == 1

    def test_user_sees_only_own_conversations(self):
        sender, receiver, other = create_users()
        own = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        ChatService.get_or_create_conversation(
            receiver.active_profile,
            other.active_profile,
        )
        client = authenticated_client(sender)

        response = client.get("/api/chat/conversations/")
        results = response_results(response)

        assert response.status_code == 200
        assert [item["id"] for item in results] == [own.pk]

    def test_seller_sees_own_conversations(self):
        sender, receiver, other = create_users()
        own = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        ChatService.get_or_create_conversation(
            other.active_profile,
            UserAccountFactory(with_profile=ProfileType.SELLER).active_profile,
        )
        client = authenticated_client(receiver)

        response = client.get("/api/chat/conversations/")
        results = response_results(response)

        assert response.status_code == 200
        assert [item["id"] for item in results] == [own.pk]

    def test_participant_can_list_messages(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        message = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "Hello",
        )
        client = authenticated_client(receiver)

        response = client.get(
            f"/api/chat/conversations/{conversation.pk}/messages/"
        )
        results = response_results(response)

        assert response.status_code == 200
        assert [item["id"] for item in results] == [message.pk]

    def test_unrelated_user_cannot_list_messages(self):
        sender, receiver, other = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        client = authenticated_client(other)

        response = client.get(
            f"/api/chat/conversations/{conversation.pk}/messages/"
        )

        assert response.status_code == 404

    def test_participant_can_send_message(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        client = authenticated_client(sender)

        response = client.post(
            f"/api/chat/conversations/{conversation.pk}/messages/",
            {"body": "Hello"},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["sender"]["id"] == sender.active_profile.pk
        assert response.data["receiver"]["id"] == receiver.active_profile.pk

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"body": ""},
            {"body": "   "},
            {"body": "x" * (Message.MAX_BODY_LENGTH + 1)},
        ],
    )
    def test_message_body_field_validation_is_serializer_owned(self, payload):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        client = authenticated_client(sender)

        response = client.post(
            f"/api/chat/conversations/{conversation.pk}/messages/",
            payload,
            format="json",
        )

        assert response.status_code == 400
        assert Message.objects.count() == 0

    def test_unrelated_user_cannot_send_message(self):
        sender, receiver, other = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        client = authenticated_client(other)

        response = client.post(
            f"/api/chat/conversations/{conversation.pk}/messages/",
            {"body": "Hello"},
            format="json",
        )

        assert response.status_code == 404

    def test_after_id_filters_messages(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        first = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "First",
        )
        second = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "Second",
        )
        client = authenticated_client(receiver)

        response = client.get(
            f"/api/chat/conversations/{conversation.pk}/messages/",
            {"after_id": first.pk},
        )
        results = response_results(response)

        assert response.status_code == 200
        assert [item["id"] for item in results] == [second.pk]

    def test_mark_read_endpoint_marks_receiver_messages(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        message = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "Unread",
        )
        client = authenticated_client(receiver)

        response = client.post(
            f"/api/chat/conversations/{conversation.pk}/mark-read/"
        )
        message.refresh_from_db()

        assert response.status_code == 200
        assert message.is_read is True


class TestChatNotifications:
    def test_sending_message_triggers_celery_task(
        self,
        django_capture_on_commit_callbacks,
    ):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        with mock.patch(
            "apps.chat.services.send_chat_message_notification.delay"
        ) as delay:
            with django_capture_on_commit_callbacks(execute=True):
                message = ChatService.send_message(
                    conversation.pk,
                    sender.active_profile,
                    "Hello",
                )

        delay.assert_called_once_with(message.pk)

    def test_notification_task_returns_receiver_not_sender(self):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )
        message = ChatService.send_message(
            conversation.pk,
            sender.active_profile,
            "Hello",
        )

        result = send_chat_message_notification(message.pk)

        assert result["receiver_id"] == receiver.active_profile.pk
        assert result["sender_id"] == sender.active_profile.pk

    def test_notification_failure_does_not_rollback_message_creation(
        self,
        django_capture_on_commit_callbacks,
    ):
        sender, receiver, _ = create_users()
        conversation = ChatService.get_or_create_conversation(
            sender.active_profile,
            receiver.active_profile,
        )

        with mock.patch(
            "apps.chat.services.send_chat_message_notification.delay",
            side_effect=RuntimeError("queue down"),
        ):
            with django_capture_on_commit_callbacks(execute=True):
                message = ChatService.send_message(
                    conversation.pk,
                    sender.active_profile,
                    "Still saved",
                )

        assert Message.objects.filter(pk=message.pk).exists()
