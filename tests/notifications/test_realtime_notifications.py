from datetime import timedelta
import json
from unittest.mock import MagicMock, patch

from channels.testing import WebsocketCommunicator
from django.db import transaction
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.notifications.fcm import (
    TransientPushError,
    build_fcm_message,
    send_notification_to_devices,
)
from apps.notifications.models import Notification, NotificationType, PushDevice
from apps.notifications.services import NotificationService
from apps.notifications.tasks import deliver_notification_push
from apps.users.constants import ProfileType
from ppw.asgi import application
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db(transaction=True)


def build_jwt_header(token):
    return [(b"authorization", f"Bearer {token}".encode("utf-8"))]


def create_user_with_profile(profile_type=ProfileType.CUSTOMER, is_active=True):
    user = UserAccountFactory(with_profile=profile_type)
    if not is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    return user


def authenticated_client(user):
    client = APIClient()
    token = build_token_for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


# ============================================================================
# 1. WEBSOCKET AUTHENTICATION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_websocket_connect_success_with_valid_jwt():
    user = await UserAccount.objects.acreate(
        username="+201000000001",
        phone_number="+201000000001",
        is_active=True,
    )
    from apps.users.models import CustomerProfile

    profile = await CustomerProfile.objects.acreate(
        user=user,
        type=ProfileType.CUSTOMER,
        is_active=True,
        activated=True,
        is_valid=True,
    )

    refresh = RefreshToken.for_user(user)
    refresh["profile"] = profile.type
    refresh["user_id"] = user.id
    access_token = str(refresh.access_token)

    communicator = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(access_token),
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_websocket_rejects_missing_token():
    communicator = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=[],
    )
    connected, code = await communicator.connect()
    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
async def test_websocket_rejects_invalid_token():
    communicator = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header("invalid.jwt.token"),
    )
    connected, code = await communicator.connect()
    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
async def test_websocket_rejects_inactive_user():
    user = await UserAccount.objects.acreate(
        username="+201000000002",
        phone_number="+201000000002",
        is_active=False,
    )
    from apps.users.models import CustomerProfile

    profile = await CustomerProfile.objects.acreate(
        user=user,
        type=ProfileType.CUSTOMER,
        is_active=True,
        activated=True,
        is_valid=True,
    )

    refresh = RefreshToken.for_user(user)
    refresh["profile"] = profile.type
    refresh["user_id"] = user.id
    access_token = str(refresh.access_token)

    communicator = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(access_token),
    )
    connected, code = await communicator.connect()
    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
async def test_websocket_rejects_invalid_or_inactive_profile():
    user = await UserAccount.objects.acreate(
        username="+201000000003",
        phone_number="+201000000003",
        is_active=True,
    )
    from apps.users.models import CustomerProfile

    profile = await CustomerProfile.objects.acreate(
        user=user,
        type=ProfileType.CUSTOMER,
        is_active=False,  # inactive profile
        activated=True,
        is_valid=True,
    )

    refresh = RefreshToken.for_user(user)
    refresh["profile"] = profile.type
    refresh["user_id"] = user.id
    access_token = str(refresh.access_token)

    communicator = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(access_token),
    )
    connected, code = await communicator.connect()
    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
async def test_websocket_does_not_mutate_active_profile():
    """Verify that socket authentication is read-only and does NOT mutate user.active_profile."""
    user = await UserAccount.objects.acreate(
        username="+201000000004",
        phone_number="+201000000004",
        is_active=True,
    )
    from apps.users.models import CustomerProfile, SellerProfile

    customer_profile = await CustomerProfile.objects.acreate(
        user=user,
        type=ProfileType.CUSTOMER,
        is_active=True,
        activated=True,
        is_valid=True,
    )
    # Another profile that is not currently active
    seller_profile = await SellerProfile.objects.acreate(
        user=user,
        type=ProfileType.SELLER,
        is_active=False,
        activated=True,
        is_valid=True,
    )

    # Token for the active customer profile
    refresh = RefreshToken.for_user(user)
    refresh["profile"] = customer_profile.type
    refresh["user_id"] = user.id
    access_token = str(refresh.access_token)

    communicator = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(access_token),
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.disconnect()

    # Verify that user.active_profile remains CustomerProfile and was not mutated
    await customer_profile.arefresh_from_db()
    await seller_profile.arefresh_from_db()
    assert customer_profile.is_active is True
    assert seller_profile.is_active is False


# ============================================================================
# 2. PROFILE ISOLATION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_profile_isolation_websocket():
    """Verify Profile A receives only A's notification, and B receives only B's notification."""
    user_a = await UserAccount.objects.acreate(
        username="+201000000010",
        phone_number="+201000000010",
        is_active=True,
    )
    user_b = await UserAccount.objects.acreate(
        username="+201000000011",
        phone_number="+201000000011",
        is_active=True,
    )
    from apps.users.models import CustomerProfile

    profile_a = await CustomerProfile.objects.acreate(
        user=user_a,
        type=ProfileType.CUSTOMER,
        is_active=True,
        activated=True,
        is_valid=True,
    )
    profile_b = await CustomerProfile.objects.acreate(
        user=user_b,
        type=ProfileType.CUSTOMER,
        is_active=True,
        activated=True,
        is_valid=True,
    )

    # Token A
    refresh_a = RefreshToken.for_user(user_a)
    refresh_a["profile"] = profile_a.type
    refresh_a["user_id"] = user_a.id
    comm_a = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(str(refresh_a.access_token)),
    )

    # Token B
    refresh_b = RefreshToken.for_user(user_b)
    refresh_b["profile"] = profile_b.type
    refresh_b["user_id"] = user_b.id
    comm_b = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(str(refresh_b.access_token)),
    )

    connected_a, _ = await comm_a.connect()
    connected_b, _ = await comm_b.connect()
    assert connected_a is True
    assert connected_b is True

    # Broadcast notification for Profile A
    from apps.notifications.websocket import async_broadcast_notification

    notif_a = await Notification.objects.acreate(
        recipient_profile=profile_a,
        notification_type=NotificationType.AUCTION_WON,
        title="Auction Won A",
        body="You won the auction A",
        object_type="auction",
        object_id=101,
        metadata={"bid": "500"},
        target_url="/auctions/101",
    )
    await async_broadcast_notification(notif_a)

    # Profile A MUST receive it
    event_a = await comm_a.receive_json_from(timeout=2)
    assert event_a["event"] == "notification.created"
    assert event_a["notification"]["id"] == notif_a.pk
    assert event_a["notification"]["title"] == "Auction Won A"

    # Profile B MUST NOT receive it
    assert await comm_b.receive_nothing(timeout=0.5) is True

    # Broadcast notification for Profile B
    notif_b = await Notification.objects.acreate(
        recipient_profile=profile_b,
        notification_type=NotificationType.AUCTION_WON,
        title="Auction Won B",
        body="You won the auction B",
        object_type="auction",
        object_id=102,
        metadata={"bid": "800"},
        target_url="/auctions/102",
    )
    await async_broadcast_notification(notif_b)

    # Profile B MUST receive it
    event_b = await comm_b.receive_json_from(timeout=2)
    assert event_b["event"] == "notification.created"
    assert event_b["notification"]["id"] == notif_b.pk
    assert event_b["notification"]["title"] == "Auction Won B"

    # Profile A MUST NOT receive B's notification
    assert await comm_a.receive_nothing(timeout=0.5) is True

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
async def test_multiple_connections_for_same_profile():
    """Verify that multiple devices/tabs connected with Profile A both receive notifications."""
    user = await UserAccount.objects.acreate(
        username="+201000000015",
        phone_number="+201000000015",
        is_active=True,
    )
    from apps.users.models import CustomerProfile

    profile = await CustomerProfile.objects.acreate(
        user=user,
        type=ProfileType.CUSTOMER,
        is_active=True,
        activated=True,
        is_valid=True,
    )

    refresh = RefreshToken.for_user(user)
    refresh["profile"] = profile.type
    refresh["user_id"] = user.id
    access_token = str(refresh.access_token)

    comm1 = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(access_token),
    )
    comm2 = WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=build_jwt_header(access_token),
    )

    connected1, _ = await comm1.connect()
    connected2, _ = await comm2.connect()
    assert connected1 is True
    assert connected2 is True

    from apps.notifications.websocket import async_broadcast_notification

    notif = await Notification.objects.acreate(
        recipient_profile=profile,
        notification_type=NotificationType.CASHBACK_EARNED,
        title="Cashback Earned",
        body="You received cashback",
    )
    await async_broadcast_notification(notif)

    event1 = await comm1.receive_json_from(timeout=2)
    event2 = await comm2.receive_json_from(timeout=2)

    assert event1["event"] == "notification.created"
    assert event1["notification"]["id"] == notif.pk
    assert event2["event"] == "notification.created"
    assert event2["notification"]["id"] == notif.pk

    await comm1.disconnect()
    await comm2.disconnect()


# ============================================================================
# 3. TRANSACTION SAFETY TESTS
# ============================================================================


def test_transaction_rollback_does_not_broadcast_or_queue_fcm(monkeypatch):
    """Verify that if a transaction rolls back, NO WebSocket broadcast and NO Celery task fire."""
    user = create_user_with_profile()
    profile = user.active_profile

    ws_broadcast_mock = MagicMock()
    fcm_delay_mock = MagicMock()

    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        ws_broadcast_mock,
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fcm_delay_mock,
    )

    try:
        with transaction.atomic():
            NotificationService.create_notification(
                recipient_profile=profile,
                notification_type=NotificationType.PAYMENT_REQUEST_APPROVED,
                title="Payment Approved",
                body="Your payment was approved.",
            )
            # Force rollback
            raise RuntimeError("Transaction aborted")
    except RuntimeError:
        pass

    assert Notification.objects.count() == 0
    ws_broadcast_mock.assert_not_called()
    fcm_delay_mock.assert_not_called()


def test_transaction_commit_triggers_broadcast_and_fcm(
    django_capture_on_commit_callbacks, monkeypatch
):
    """Verify that after commit, WebSocket broadcast is executed and FCM task is enqueued."""
    user = create_user_with_profile()
    profile = user.active_profile

    ws_broadcast_mock = MagicMock()
    fcm_delay_mock = MagicMock()

    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        ws_broadcast_mock,
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fcm_delay_mock,
    )

    with django_capture_on_commit_callbacks(execute=True):
        notification = NotificationService.create_notification(
            recipient_profile=profile,
            notification_type=NotificationType.PAYMENT_REQUEST_APPROVED,
            title="Payment Approved",
            body="Your payment was approved.",
        )

    assert notification is not None
    ws_broadcast_mock.assert_called_once()
    fcm_delay_mock.assert_called_once_with(notification.pk)


# ============================================================================
# 4. PUSH DEVICE API & OWNERSHIP TESTS
# ============================================================================


def test_push_device_registration_and_list():
    user = create_user_with_profile()
    client = authenticated_client(user)

    # Register device
    res = client.post(
        "/api/notifications/devices/",
        {"token": "fcm-token-123", "platform": "android"},
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    device_id = res.data["id"]
    assert res.data["platform"] == "android"
    assert res.data["is_active"] is True

    # List devices
    list_res = client.get("/api/notifications/devices/")
    assert list_res.status_code == status.HTTP_200_OK
    assert len(list_res.data) == 1
    assert list_res.data[0]["id"] == device_id


def test_push_device_re_registration_updates_same_profile():
    user = create_user_with_profile()
    client = authenticated_client(user)

    res1 = client.post(
        "/api/notifications/devices/",
        {"token": "fcm-token-same", "platform": "android"},
        format="json",
    )
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = client.post(
        "/api/notifications/devices/",
        {"token": "fcm-token-same", "platform": "ios"},
        format="json",
    )
    assert res2.status_code == status.HTTP_200_OK
    assert res2.data["id"] == res1.data["id"]
    assert res2.data["platform"] == "ios"
    assert res2.data["is_active"] is True


def test_push_device_token_reassignment_to_another_profile():
    """Verify that if user B registers a token previously used by user A, it is reassigned."""
    user_a = create_user_with_profile()
    user_b = create_user_with_profile()
    client_a = authenticated_client(user_a)
    client_b = authenticated_client(user_b)

    # User A registers token
    res_a = client_a.post(
        "/api/notifications/devices/",
        {"token": "shared-device-token", "platform": "android"},
        format="json",
    )
    assert res_a.status_code == status.HTTP_201_CREATED
    device_id = res_a.data["id"]

    # User B registers same token
    res_b = client_b.post(
        "/api/notifications/devices/",
        {"token": "shared-device-token", "platform": "android"},
        format="json",
    )
    assert res_b.status_code == status.HTTP_200_OK
    assert res_b.data["id"] == device_id
    assert res_b.data["profile"] == user_b.active_profile.pk

    # User A lists devices: should be empty for User A
    list_a = client_a.get("/api/notifications/devices/")
    assert len(list_a.data) == 0

    # User B lists devices: contains the device
    list_b = client_b.get("/api/notifications/devices/")
    assert len(list_b.data) == 1
    assert list_b.data[0]["id"] == device_id


def test_push_device_deactivation_and_ownership_isolation():
    user_a = create_user_with_profile()
    user_b = create_user_with_profile()
    client_a = authenticated_client(user_a)
    client_b = authenticated_client(user_b)

    res = client_a.post(
        "/api/notifications/devices/",
        {"token": "token-user-a", "platform": "ios"},
        format="json",
    )
    device_id = res.data["id"]

    # User B attempts to delete User A's device -> 404 NOT FOUND
    del_res_b = client_b.delete(f"/api/notifications/devices/{device_id}/")
    assert del_res_b.status_code == status.HTTP_404_NOT_FOUND

    device = PushDevice.objects.get(pk=device_id)
    assert device.is_active is True

    # User A deletes their own device -> 204 NO CONTENT
    del_res_a = client_a.delete(f"/api/notifications/devices/{device_id}/")
    assert del_res_a.status_code == status.HTTP_204_NO_CONTENT

    device.refresh_from_db()
    assert device.is_active is False


# ============================================================================
# 5. FCM DELIVERY & ERROR CLASSIFICATION TESTS
# ============================================================================


def test_fcm_message_payload_structure():
    user = create_user_with_profile()
    notif = Notification.objects.create(
        recipient_profile=user.active_profile,
        notification_type=NotificationType.ORDER_ITEM_APPROVED,
        title="Item Approved",
        body="Your order item was approved.",
        object_type="order_item",
        object_id=981,
        target_url="/orders/items/981",
        metadata={"order_id": 42},
    )

    msg = build_fcm_message(notif, ["token-1", "token-2"])
    assert msg.notification.title == "Item Approved"
    assert msg.notification.body == "Your order item was approved."
    assert msg.data["notification_id"] == str(notif.pk)
    assert msg.data["notification_type"] == "order_item_approved"
    assert msg.data["object_type"] == "order_item"
    assert msg.data["object_id"] == "981"
    assert msg.data["target_url"] == "/orders/items/981"
    assert json.loads(msg.data["metadata"]) == {"order_id": 42}
    assert msg.tokens == ["token-1", "token-2"]


def test_fcm_permanent_error_deactivates_push_device(monkeypatch):
    user = create_user_with_profile()
    profile = user.active_profile
    device = PushDevice.objects.create(
        user=user,
        profile=profile,
        token="invalid-token",
        platform="android",
        is_active=True,
    )
    notif = Notification.objects.create(
        recipient_profile=profile,
        notification_type=NotificationType.AUCTION_WON,
        title="Won",
        body="Won auction",
    )

    # Mock Firebase app and send_each_for_multicast
    fake_app = MagicMock()
    monkeypatch.setattr("apps.notifications.fcm._firebase_app", lambda: fake_app)

    from firebase_admin import messaging

    fake_response = MagicMock()
    fake_response.success_count = 0
    fake_result = MagicMock()
    fake_result.success = False
    fake_result.exception = messaging.UnregisteredError("Requested entity was not found.")
    fake_response.responses = [fake_result]

    monkeypatch.setattr(
        "firebase_admin.messaging.send_each_for_multicast",
        lambda msg, app: fake_response,
    )

    send_notification_to_devices(notif, [device])

    device.refresh_from_db()
    assert device.is_active is False


def test_fcm_transient_error_raises_retry_exception(monkeypatch):
    user = create_user_with_profile()
    profile = user.active_profile
    device = PushDevice.objects.create(
        user=user,
        profile=profile,
        token="good-token",
        platform="android",
        is_active=True,
    )
    notif = Notification.objects.create(
        recipient_profile=profile,
        notification_type=NotificationType.AUCTION_WON,
        title="Won",
        body="Won auction",
    )

    fake_app = MagicMock()
    monkeypatch.setattr("apps.notifications.fcm._firebase_app", lambda: fake_app)

    from firebase_admin import exceptions as fb_exceptions, messaging

    fake_response = MagicMock()
    fake_response.success_count = 0
    fake_result = MagicMock()
    fake_result.success = False
    fake_result.exception = fb_exceptions.UnavailableError("Service temporarily unavailable.")
    fake_response.responses = [fake_result]

    monkeypatch.setattr(
        "firebase_admin.messaging.send_each_for_multicast",
        lambda msg, app: fake_response,
    )

    with pytest.raises(TransientPushError):
        send_notification_to_devices(notif, [device])

    # Transient error should NOT deactivate device
    device.refresh_from_db()
    assert device.is_active is True


def test_fcm_missing_credentials_handled_gracefully(monkeypatch):
    user = create_user_with_profile()
    profile = user.active_profile
    device = PushDevice.objects.create(
        user=user,
        profile=profile,
        token="token-xyz",
        platform="android",
        is_active=True,
    )
    notif = Notification.objects.create(
        recipient_profile=profile,
        notification_type=NotificationType.AUCTION_WON,
        title="Won",
        body="Won auction",
    )

    # Ensure no credentials configured
    monkeypatch.setattr("apps.notifications.fcm._firebase_app", lambda: None)

    count = send_notification_to_devices(notif, [device])
    assert count == 0
    device.refresh_from_db()
    assert device.is_active is True


def test_deliver_notification_push_celery_task(monkeypatch):
    user = create_user_with_profile()
    profile = user.active_profile
    device = PushDevice.objects.create(
        user=user,
        profile=profile,
        token="valid-token",
        platform="ios",
        is_active=True,
    )
    notif = Notification.objects.create(
        recipient_profile=profile,
        notification_type=NotificationType.AUCTION_WON,
        title="Won",
        body="Won auction",
    )

    send_mock = MagicMock(return_value=1)
    monkeypatch.setattr(
        "apps.notifications.fcm.send_notification_to_devices",
        send_mock,
    )

    deliver_notification_push(notif.pk)
    send_mock.assert_called_once()
    assert send_mock.call_args[0][0].pk == notif.pk
    assert send_mock.call_args[0][1][0].pk == device.pk


# ============================================================================
# 6. REST RECOVERY / CREATED_AFTER FILTER TESTS
# ============================================================================


def test_notification_recovery_created_after_filter():
    user = create_user_with_profile()
    client = authenticated_client(user)
    profile = user.active_profile

    t0 = timezone.now() - timedelta(hours=2)
    t1 = timezone.now() - timedelta(hours=1)
    t2 = timezone.now()

    # Old notification
    old_notif = Notification.objects.create(
        recipient_profile=profile,
        notification_type=NotificationType.AUCTION_WON,
        title="Old Notification",
        body="Created earlier",
    )
    Notification.objects.filter(pk=old_notif.pk).update(created=t0)

    # New notification
    new_notif = Notification.objects.create(
        recipient_profile=profile,
        notification_type=NotificationType.AUCTION_WON,
        title="New Notification",
        body="Created recently",
    )
    Notification.objects.filter(pk=new_notif.pk).update(created=t2)

    # Sync with created_after = t1 (should return only new_notif)
    response = client.get(
        "/api/notifications/",
        {"created_after": t1.isoformat()},
    )
    assert response.status_code == status.HTTP_200_OK
    results = response.data.get("results", response.data)
    assert len(results) == 1
    assert results[0]["id"] == new_notif.pk
