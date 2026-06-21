from decimal import Decimal

import pytest
from rest_framework.exceptions import NotFound
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.auctions.models import AuctionItemStatus
from apps.chat.services import ChatService
from apps.complaints.models import ComplaintStatus
from apps.complaints.services import ComplaintService
from apps.loyalty.constants import BadgeType
from apps.loyalty.services import BadgeService
from apps.notifications.models import Notification, NotificationType
from apps.notifications.services import NotificationService
from apps.orders.models import OrderItemStatus, OrderStatus
from apps.orders.services import OrderItemService
from apps.payments.models import PaymentRequest, PaymentRequestStatus, PaymentRequestType
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BirdFactory,
    NotificationFactory,
    OrderFactory,
    OrderItemFactory,
    SupplyFactory,
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
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    other_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    return customer, seller, manager, other_customer


def create_conversation(customer, seller):
    return ChatService.get_or_create_conversation(
        customer.active_profile,
        seller.active_profile,
    )


def create_approved_order_item(*, buyer=None, seller=None):
    buyer = buyer or UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = seller or UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer_profile = CustomerProfile.objects.get(pk=buyer.active_profile.pk)
    seller_profile = SellerProfile.objects.get(pk=seller.active_profile.pk)
    asset = SupplyFactory(owner=seller_profile, count=5)
    order = OrderFactory(owner=buyer_profile, status=OrderStatus.PROCESSING)
    item = OrderItemFactory(
        order=order,
        asset=asset,
        seller=seller_profile,
        quantity=2,
        unit_price_snapshot=Decimal("25.00"),
        status=OrderItemStatus.APPROVED,
    )
    return item, buyer, seller, asset


def create_sold_auction_item(*, buyer=None, seller=None):
    buyer = buyer or UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = seller or UserAccountFactory(with_profile=ProfileType.SELLER)
    bird = BirdFactory(owner=seller.active_profile)
    auction = AuctionFactory(owner=seller.active_profile)
    item = AuctionItemFactory(
        auction=auction,
        bird=bird,
        status=AuctionItemStatus.SOLD,
        winner=buyer.active_profile,
        current_price=Decimal("150.00"),
    )
    return item, buyer, seller


def create_payment_request(*, status=PaymentRequestStatus.APPROVED):
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    asset = SupplyFactory(owner=seller.active_profile)
    payment_request = PaymentRequest.objects.create(
        buyer=buyer.active_profile,
        seller=seller.active_profile,
        asset=asset,
        amount=Decimal("25.00"),
        status=status,
        request_type=PaymentRequestType.MARKET,
    )
    return payment_request, buyer, seller


class TestNotificationService:
    def test_create_notification(self):
        customer, _, _, _ = create_users()

        notification = NotificationService.create_notification(
            recipient_profile=customer.active_profile,
            notification_type=NotificationType.ORDER_ITEM_APPROVED,
            title="Approved",
            body="Your order item was approved.",
        )

        assert notification is not None
        assert notification.recipient_profile_id == customer.active_profile.pk
        assert notification.actor_profile_id is None
        assert notification.is_read is False

    def test_create_notification_with_actor(self):
        customer, seller, _, _ = create_users()

        notification = NotificationService.create_notification(
            recipient_profile=customer.active_profile,
            actor_profile=seller.active_profile,
            notification_type=NotificationType.PAYMENT_REQUEST_CREATED,
            title="Created",
            body="A payment request was created.",
        )

        assert notification.actor_profile_id == seller.active_profile.pk

    def test_idempotency_key_prevents_duplicates(self):
        customer, _, _, _ = create_users()

        first = NotificationService.create_notification(
            recipient_profile=customer.active_profile,
            notification_type=NotificationType.BADGE_AWARDED,
            title="Badge",
            body="You earned a badge.",
            idempotency_key="badge:test",
        )
        second = NotificationService.create_notification(
            recipient_profile=customer.active_profile,
            notification_type=NotificationType.BADGE_AWARDED,
            title="Badge",
            body="You earned a badge.",
            idempotency_key="badge:test",
        )

        assert first.pk == second.pk
        assert Notification.objects.count() == 1

    def test_unread_count_works(self):
        customer, _, _, _ = create_users()
        NotificationFactory(recipient_profile=customer.active_profile)
        NotificationFactory(recipient_profile=customer.active_profile)
        NotificationFactory(recipient_profile=customer.active_profile, is_read=True)

        assert NotificationService.get_unread_count(customer.active_profile) == 2

    def test_mark_one_as_read(self):
        customer, _, _, _ = create_users()
        notification = NotificationFactory(recipient_profile=customer.active_profile)

        updated = NotificationService.mark_as_read(notification.pk, customer.active_profile)

        assert updated.is_read is True
        assert updated.read_at is not None

    def test_mark_all_as_read(self):
        customer, _, _, _ = create_users()
        NotificationFactory.create_batch(3, recipient_profile=customer.active_profile)
        NotificationFactory(recipient_profile=customer.active_profile, is_read=True)

        updated_count = NotificationService.mark_all_as_read(customer.active_profile)

        assert updated_count == 3
        assert Notification.objects.filter(
            recipient_profile=customer.active_profile,
            is_read=False,
        ).count() == 0

    def test_cannot_mark_another_profiles_notification_read(self):
        customer, _, _, other_customer = create_users()
        notification = NotificationFactory(recipient_profile=customer.active_profile)

        with pytest.raises(NotFound):
            NotificationService.mark_as_read(notification.pk, other_customer.active_profile)


class TestNotificationAPI:
    def test_unauthenticated_list_returns_401(self):
        response = APIClient().get("/api/notifications/")

        assert response.status_code == 401

    def test_authenticated_profile_lists_only_own_notifications(self):
        customer, seller, _, _ = create_users()
        own = NotificationFactory(recipient_profile=customer.active_profile)
        NotificationFactory(recipient_profile=seller.active_profile)
        client = authenticated_client(customer)

        response = client.get("/api/notifications/")
        results = response_results(response)

        assert response.status_code == 200
        assert [item["id"] for item in results] == [own.pk]

    def test_unread_count_returns_correct_count(self):
        customer, _, _, _ = create_users()
        NotificationFactory.create_batch(2, recipient_profile=customer.active_profile)
        NotificationFactory(recipient_profile=customer.active_profile, is_read=True)
        client = authenticated_client(customer)

        response = client.get("/api/notifications/unread-count/")

        assert response.status_code == 200
        assert response.data == {"unread_count": 2}

    def test_filter_by_is_read_works(self):
        customer, _, _, _ = create_users()
        unread = NotificationFactory(recipient_profile=customer.active_profile, is_read=False)
        NotificationFactory(recipient_profile=customer.active_profile, is_read=True)
        client = authenticated_client(customer)

        response = client.get("/api/notifications/", {"is_read": "false"})
        results = response_results(response)

        assert response.status_code == 200
        assert [item["id"] for item in results] == [unread.pk]

    def test_filter_by_type_works(self):
        customer, _, _, _ = create_users()
        matched = NotificationFactory(
            recipient_profile=customer.active_profile,
            notification_type=NotificationType.ORDER_ITEM_APPROVED,
        )
        NotificationFactory(
            recipient_profile=customer.active_profile,
            notification_type=NotificationType.PAYMENT_REQUEST_CREATED,
        )
        client = authenticated_client(customer)

        response = client.get(
            "/api/notifications/",
            {"type": NotificationType.ORDER_ITEM_APPROVED},
        )
        results = response_results(response)

        assert response.status_code == 200
        assert [item["id"] for item in results] == [matched.pk]

    def test_mark_read_works(self):
        customer, _, _, _ = create_users()
        notification = NotificationFactory(recipient_profile=customer.active_profile)
        client = authenticated_client(customer)

        response = client.post(f"/api/notifications/{notification.pk}/mark-read/")
        notification.refresh_from_db()

        assert response.status_code == 200
        assert notification.is_read is True
        assert response.data["is_read"] is True

    def test_mark_all_read_works(self):
        customer, _, _, _ = create_users()
        NotificationFactory.create_batch(3, recipient_profile=customer.active_profile)
        client = authenticated_client(customer)

        response = client.post("/api/notifications/mark-all-read/")

        assert response.status_code == 200
        assert response.data == {"updated_count": 3}

    def test_private_profile_fields_are_not_exposed(self):
        customer, seller, _, _ = create_users()
        notification = NotificationFactory(
            recipient_profile=customer.active_profile,
            actor_profile=seller.active_profile,
        )
        client = authenticated_client(customer)

        response = client.get("/api/notifications/")
        results = response_results(response)

        assert response.status_code == 200
        actor_profile = results[0]["actor_profile"]
        assert results[0]["id"] == notification.pk
        assert "email" not in actor_profile
        assert "phone_number" not in actor_profile

    def test_cannot_mark_another_profiles_notification_read(self):
        customer, _, _, other_customer = create_users()
        notification = NotificationFactory(recipient_profile=customer.active_profile)
        client = authenticated_client(other_customer)

        response = client.post(f"/api/notifications/{notification.pk}/mark-read/")

        assert response.status_code == 404


class TestNotificationIntegrations:
    def test_chat_message_creates_notification_for_receiver_only(
        self,
        django_capture_on_commit_callbacks,
    ):
        customer, seller, _, _ = create_users()
        conversation = create_conversation(customer, seller)

        with django_capture_on_commit_callbacks(execute=True):
            ChatService.send_message(
                conversation.pk,
                customer.active_profile,
                "Hello seller",
            )

        notification = Notification.objects.get(
            notification_type=NotificationType.CHAT_MESSAGE_RECEIVED
        )
        assert notification.recipient_profile_id == seller.active_profile.pk
        assert notification.actor_profile_id == customer.active_profile.pk

    def test_chat_notification_task_is_idempotent(self):
        customer, seller, _, _ = create_users()
        conversation = create_conversation(customer, seller)
        message = ChatService.send_message(
            conversation.pk,
            customer.active_profile,
            "Hello seller",
        )

        from apps.chat.tasks import send_chat_message_notification

        send_chat_message_notification(message.pk)

        assert Notification.objects.filter(
            notification_type=NotificationType.CHAT_MESSAGE_RECEIVED
        ).count() == 1

    def test_payment_request_created_notifies_seller(
        self,
        django_capture_on_commit_callbacks,
    ):
        item, buyer, seller = create_sold_auction_item()

        with django_capture_on_commit_callbacks(execute=True):
            payment_request = PaymentRequestService.create_for_auction_item(
                item.pk,
                buyer.active_profile,
            )

        notification = Notification.objects.get(
            notification_type=NotificationType.PAYMENT_REQUEST_CREATED,
            object_id=payment_request.pk,
        )
        assert notification.recipient_profile_id == seller.active_profile.pk
        assert notification.actor_profile_id == buyer.active_profile.pk

    def test_payment_request_approved_notifies_buyer(
        self,
        django_capture_on_commit_callbacks,
    ):
        item, buyer, seller, _ = create_approved_order_item()
        with django_capture_on_commit_callbacks(execute=True):
            payment_request = PaymentRequestService.create_for_order_item(
                item.pk,
                buyer.active_profile,
            )

        with django_capture_on_commit_callbacks(execute=True):
            PaymentRequestService.approve(
                payment_request.pk,
                seller.active_profile,
            )

        notification = Notification.objects.get(
            notification_type=NotificationType.PAYMENT_REQUEST_APPROVED,
            object_id=payment_request.pk,
        )
        assert notification.recipient_profile_id == buyer.active_profile.pk
        assert notification.actor_profile_id == seller.active_profile.pk

    def test_payment_request_rejected_notifies_buyer(
        self,
        django_capture_on_commit_callbacks,
    ):
        item, buyer, seller, _ = create_approved_order_item()
        with django_capture_on_commit_callbacks(execute=True):
            payment_request = PaymentRequestService.create_for_order_item(
                item.pk,
                buyer.active_profile,
            )

        with django_capture_on_commit_callbacks(execute=True):
            PaymentRequestService.reject(
                payment_request.pk,
                seller.active_profile,
                seller_note="Rejected",
            )

        notification = Notification.objects.get(
            notification_type=NotificationType.PAYMENT_REQUEST_REJECTED,
            object_id=payment_request.pk,
        )
        assert notification.recipient_profile_id == buyer.active_profile.pk

    def test_order_item_approved_notifies_customer(
        self,
        django_capture_on_commit_callbacks,
    ):
        item, buyer, seller, _ = create_approved_order_item()
        item.status = OrderItemStatus.PENDING_SELLER
        item.save(update_fields=["status", "modified"])

        with django_capture_on_commit_callbacks(execute=True):
            OrderItemService.approve_item(item, seller.active_profile)

        notification = Notification.objects.get(
            notification_type=NotificationType.ORDER_ITEM_APPROVED,
            object_id=item.pk,
        )
        assert notification.recipient_profile_id == buyer.active_profile.pk

    def test_order_item_rejected_notifies_customer(
        self,
        django_capture_on_commit_callbacks,
    ):
        item, buyer, seller, _ = create_approved_order_item()
        item.status = OrderItemStatus.PENDING_SELLER
        item.save(update_fields=["status", "modified"])

        with django_capture_on_commit_callbacks(execute=True):
            OrderItemService.reject_item(item, seller.active_profile)

        notification = Notification.objects.get(
            notification_type=NotificationType.ORDER_ITEM_REJECTED,
            object_id=item.pk,
        )
        assert notification.recipient_profile_id == buyer.active_profile.pk

    def test_complaint_status_update_notifies_buyer_and_seller(
        self,
        django_capture_on_commit_callbacks,
    ):
        payment_request, buyer, seller = create_payment_request()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        complaint = ComplaintService.create_complaint(
            payment_request.pk,
            buyer.active_profile,
            title="Problem",
            description="Details",
        )

        with django_capture_on_commit_callbacks(execute=True):
            updated = ComplaintService.resolve(
                complaint.pk,
                manager.active_profile,
                admin_note="Resolved",
            )

        assert updated.status == ComplaintStatus.RESOLVED
        notifications = Notification.objects.filter(
            notification_type=NotificationType.COMPLAINT_STATUS_UPDATED,
            object_id=complaint.pk,
        ).order_by("recipient_profile_id")
        assert [n.recipient_profile_id for n in notifications] == [
            buyer.active_profile.pk,
            seller.active_profile.pk,
        ]

    def test_badge_awarded_creates_notification_once(
        self,
        django_capture_on_commit_callbacks,
    ):
        customer, _, _, _ = create_users()

        with django_capture_on_commit_callbacks(execute=True):
            first = BadgeService.award_badge(
                profile=customer.active_profile,
                badge_type=BadgeType.WELCOME_BUYER,
                idempotency_key="badge:welcome",
            )
        second = BadgeService.award_badge(
            profile=customer.active_profile,
            badge_type=BadgeType.WELCOME_BUYER,
            idempotency_key="badge:welcome",
        )

        assert first.pk == second.pk
        assert Notification.objects.filter(
            notification_type=NotificationType.BADGE_AWARDED,
            object_id=first.pk,
        ).count() == 1
