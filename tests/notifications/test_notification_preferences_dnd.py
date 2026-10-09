from datetime import datetime, time, timedelta
from unittest.mock import MagicMock

from django.db import transaction
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import (
    build_token_for_user,
    build_token_for_user_role,
    switch_user_profile,
)
from apps.draws.models import WeeklyDrawEntry
from apps.draws.services import WeeklyDrawEntryService
from apps.loyalty.constants import BadgeType, RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.services import BadgeService, PointService
from apps.lucky_wheel.models import (
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
)
from apps.lucky_wheel.services import (
    LuckyWheelGrantService,
    LuckyWheelSpinService,
)
from apps.notifications.models import (
    Notification,
    NotificationPreference,
    NotificationType,
    PushDevice,
)
from apps.notifications.payload import localize_notification_text
from apps.notifications.services import (
    NotificationPreferenceService,
    NotificationService,
)
from apps.notifications.tasks import deliver_notification_push
from apps.prizes.models import (
    PrizeAward,
    PrizeAwardFulfillmentType,
    PrizeAwardSourceType,
)
from apps.prizes.services import PrizeAwardService, PrizeClaimService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db

PREFERENCES_URL = "/api/notifications/preferences/"


def auth_client(user, lang=None, profile_type=None):
    client = APIClient()
    refresh = build_token_for_user(user)
    if profile_type is not None:
        refresh["profile"] = profile_type
        refresh.access_token["profile"] = profile_type
    headers = {"HTTP_AUTHORIZATION": f"Bearer {refresh.access_token}"}
    if lang is not None:
        headers["HTTP_ACCEPT_LANGUAGE"] = lang
    client.credentials(**headers)
    return client


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


def make_dual_profile_user():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer = CustomerProfile.objects.get(pk=user.active_profile.pk)
    seller = SellerProfileFactory(
        user=user,
        is_active=False,
        activated=True,
        is_valid=True,
    )
    return user, customer, seller


def make_fixed_dt(hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(
        datetime(2026, 10, 9, hour, minute, 0),
        timezone.get_current_timezone(),
    )



# ===========================================================================
# SETTINGS (Tests 1 - 10)
# ===========================================================================


def test_01_user_with_no_preference_row_behaves_as_dnd_disabled():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert NotificationPreference.objects.filter(user=user).count() == 0
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(2, 0)
        )
        is False
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(14, 0)
        )
        is False
    )


def test_02_get_preferences_returns_accounts_settings_without_creating_row():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    res = auth_client(user).get(PREFERENCES_URL)
    assert res.status_code == status.HTTP_200_OK
    assert res.data == {
        "dnd_enabled": False,
        "dnd_start": None,
        "dnd_end": None,
    }
    assert NotificationPreference.objects.filter(user=user).count() == 0


def test_03_patch_enables_dnd():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    res = auth_client(user).patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "22:00:00",
            "dnd_end": "07:00:00",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data == {
        "dnd_enabled": True,
        "dnd_start": "22:00:00",
        "dnd_end": "07:00:00",
    }
    pref = NotificationPreference.objects.get(user=user)
    assert pref.dnd_enabled is True
    assert pref.dnd_start == time(22, 0)
    assert pref.dnd_end == time(7, 0)


def test_04_patch_disables_dnd_without_deleting_row():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    auth_client(user).patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "22:00:00",
            "dnd_end": "07:00:00",
        },
        format="json",
    )
    pref_id = NotificationPreference.objects.get(user=user).pk

    res = auth_client(user).patch(
        PREFERENCES_URL,
        {"dnd_enabled": False},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data == {
        "dnd_enabled": False,
        "dnd_start": "22:00:00",
        "dnd_end": "07:00:00",
    }
    pref = NotificationPreference.objects.get(user=user)
    assert pref.pk == pref_id
    assert pref.dnd_enabled is False
    assert pref.dnd_start == time(22, 0)
    assert pref.dnd_end == time(7, 0)


def test_05_one_daily_interval_persists():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = auth_client(user)
    client.patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "21:30:00",
            "dnd_end": "06:30:00",
        },
        format="json",
    )
    client.patch(
        PREFERENCES_URL,
        {
            "dnd_start": "23:15:00",
            "dnd_end": "07:45:00",
        },
        format="json",
    )

    assert NotificationPreference.objects.filter(user=user).count() == 1
    get_res = client.get(PREFERENCES_URL)
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.data == {
        "dnd_enabled": True,
        "dnd_start": "23:15:00",
        "dnd_end": "07:45:00",
    }


def test_06_overnight_interval_23_00_to_08_00_accepted():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    res = auth_client(user).patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "23:00",
            "dnd_end": "08:00",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data == {
        "dnd_enabled": True,
        "dnd_start": "23:00:00",
        "dnd_end": "08:00:00",
    }
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(22, 59)
        )
        is False
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(23, 0)
        )
        is True
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(2, 30)
        )
        is True
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(7, 59)
        )
        is True
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(8, 0)
        )
        is False
    )


def test_07_normal_same_day_interval_13_00_to_15_00_accepted():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    res = auth_client(user).patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "13:00",
            "dnd_end": "15:00",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data == {
        "dnd_enabled": True,
        "dnd_start": "13:00:00",
        "dnd_end": "15:00:00",
    }
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(12, 59)
        )
        is False
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(13, 0)
        )
        is True
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(14, 30)
        )
        is True
    )
    assert (
        NotificationPreferenceService.is_dnd_active_for_user(
            user, now=make_fixed_dt(15, 0)
        )
        is False
    )


def test_08_invalid_time_or_missing_or_equal_bounds_rejected():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = auth_client(user)

    bad_time_res = client.patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "25:99:00",
            "dnd_end": "08:00:00",
        },
        format="json",
    )
    assert bad_time_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "dnd_start" in bad_time_res.data

    missing_bounds_res = client.patch(
        PREFERENCES_URL,
        {"dnd_enabled": True},
        format="json",
    )
    assert missing_bounds_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "dnd_start" in missing_bounds_res.data
    assert "dnd_end" in missing_bounds_res.data

    equal_bounds_res = client.patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "00:00:00",
            "dnd_end": "00:00:00",
        },
        format="json",
    )
    assert equal_bounds_res.status_code == status.HTTP_400_BAD_REQUEST
    assert "dnd_end" in equal_bounds_res.data


def test_09_client_cannot_set_another_user_id_or_profile_id():
    attacker = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    victim = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    for forbidden_field, forbidden_val in (
        ("user", victim.pk),
        ("user_id", victim.pk),
        ("profile", victim.active_profile.pk),
        ("profile_id", victim.active_profile.pk),
    ):
        res = auth_client(attacker).patch(
            PREFERENCES_URL,
            {
                forbidden_field: forbidden_val,
                "dnd_enabled": True,
                "dnd_start": "23:00:00",
                "dnd_end": "08:00:00",
            },
            format="json",
        )
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert forbidden_field in res.data

    assert NotificationPreference.objects.filter(user=victim).count() == 0


def test_10_customer_seller_profile_switching_preserves_dnd():
    user, customer, seller = make_dual_profile_user()

    # Configure DND while in Customer mode
    cust_res = auth_client(user, profile_type=ProfileType.CUSTOMER).patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "23:00:00",
            "dnd_end": "08:00:00",
        },
        format="json",
    )
    assert cust_res.status_code == status.HTTP_200_OK

    # Switch to Seller mode
    switched, new_role = switch_user_profile(
        user, new_profile_type=ProfileType.SELLER
    )
    assert switched is True
    assert new_role == ProfileType.SELLER

    seller_get = auth_client(user, profile_type=ProfileType.SELLER).get(
        PREFERENCES_URL
    )
    assert seller_get.status_code == status.HTTP_200_OK
    assert seller_get.data == {
        "dnd_enabled": True,
        "dnd_start": "23:00:00",
        "dnd_end": "08:00:00",
    }

    # Update DND while in Seller mode and switch back to Customer
    seller_patch = auth_client(user, profile_type=ProfileType.SELLER).patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "22:00:00",
            "dnd_end": "06:00:00",
        },
        format="json",
    )
    assert seller_patch.status_code == status.HTTP_200_OK

    switched_back, role_back = switch_user_profile(
        user, new_profile_type=ProfileType.CUSTOMER
    )
    assert switched_back is True
    assert role_back == ProfileType.CUSTOMER

    cust_get = auth_client(user, profile_type=ProfileType.CUSTOMER).get(
        PREFERENCES_URL
    )
    assert cust_get.status_code == status.HTTP_200_OK
    assert cust_get.data == {
        "dnd_enabled": True,
        "dnd_start": "22:00:00",
        "dnd_end": "06:00:00",
    }


# ===========================================================================
# PUSH DURING DND (Tests 11 - 16)
# ===========================================================================


def test_11_notification_db_row_still_created_during_dnd(monkeypatch):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(1, 30),
    )

    tx = PointService.earn_points(
        user=user,
        profile=profile,
        points=50,
        reason="Night earn",
        source_type=RewardSourceType.ONBOARDING,
        source_id=str(user.pk),
        idempotency_key="dnd:11:pp",
    )

    notif = Notification.objects.get(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
        object_id=tx.pk,
    )
    assert notif.pk is not None


def test_12_notification_remains_visible_through_in_app_api_during_dnd(
    monkeypatch,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(2, 0),
    )

    PointService.earn_points(
        user=user,
        profile=profile,
        points=40,
        reason="In-app visible during DND",
        idempotency_key="dnd:12:pp",
    )

    res = auth_client(user).get("/api/notifications/")
    assert res.status_code == status.HTTP_200_OK
    items = response_results(res)
    assert len(items) == 1
    assert items[0]["notification_type"] == NotificationType.POINTS_EARNED


def test_13_websocket_broadcast_still_occurs_during_dnd(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(3, 0),
    )
    ws_mock = MagicMock()
    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        ws_mock,
    )

    with django_capture_on_commit_callbacks(execute=True):
        notif = NotificationService.create_notification(
            recipient_profile=profile,
            notification_type=NotificationType.POINTS_EARNED,
            title="Congratulations! Points earned",
            body="Congratulations! You earned 30 PP Coins.",
            idempotency_key="dnd:13:ws",
        )

    ws_mock.assert_called_once()
    assert ws_mock.call_args[0][0].pk == notif.pk


def test_14_fcm_push_is_not_sent_during_dnd(monkeypatch):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PushDevice.objects.create(
        user=user,
        profile=profile,
        token="dnd-device-token-14",
        platform=PushDevice.Platform.ANDROID,
        is_active=True,
    )
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(1, 0),
    )
    send_mock = MagicMock(return_value=1)
    monkeypatch.setattr(
        "apps.notifications.fcm.send_notification_to_devices",
        send_mock,
    )

    notif = NotificationService.create_notification(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
        title="Congratulations! Points earned",
        body="Congratulations! You earned 50 PP Coins.",
        idempotency_key="dnd:14:push",
    )

    sent_count = deliver_notification_push(notif.pk)
    assert sent_count == 0
    assert send_mock.call_count == 0


def test_15_no_delayed_fcm_push_is_scheduled_during_dnd(monkeypatch):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PushDevice.objects.create(
        user=user,
        profile=profile,
        token="dnd-device-token-15",
        platform=PushDevice.Platform.IOS,
        is_active=True,
    )
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(13, 0),
        dnd_end=time(15, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(14, 0),
    )
    apply_async_mock = MagicMock()
    retry_mock = MagicMock()
    monkeypatch.setattr(
        deliver_notification_push,
        "apply_async",
        apply_async_mock,
    )
    monkeypatch.setattr(
        deliver_notification_push,
        "retry",
        retry_mock,
    )

    notif = NotificationService.create_notification(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
        title="Congratulations! Points earned",
        body="Congratulations! You earned 50 PP Coins.",
        idempotency_key="dnd:15:nodelay",
    )

    result = deliver_notification_push(notif.pk)
    assert result == 0
    assert apply_async_mock.call_count == 0
    assert retry_mock.call_count == 0


def test_16_dnd_does_not_change_notification_unread_state(monkeypatch):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(4, 0),
    )

    notif = NotificationService.create_notification(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
        title="Congratulations! Points earned",
        body="Congratulations! You earned 20 PP Coins.",
        idempotency_key="dnd:16:unread",
    )
    deliver_notification_push(notif.pk)

    notif.refresh_from_db()
    assert notif.is_read is False
    assert notif.read_at is None
    assert NotificationService.get_unread_count(profile) == 1


# ===========================================================================
# OUTSIDE DND (Tests 17 - 19)
# ===========================================================================


def test_17_fcm_push_sends_normally_outside_dnd(monkeypatch):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    device = PushDevice.objects.create(
        user=user,
        profile=profile,
        token="outside-dnd-token-17",
        platform=PushDevice.Platform.ANDROID,
        is_active=True,
    )
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(12, 0),
    )
    send_mock = MagicMock(return_value=1)
    monkeypatch.setattr(
        "apps.notifications.fcm.send_notification_to_devices",
        send_mock,
    )

    notif = NotificationService.create_notification(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
        title="Congratulations! Points earned",
        body="Congratulations! You earned 45 PP Coins.",
        idempotency_key="dnd:17:outside",
    )

    sent_count = deliver_notification_push(notif.pk)
    assert sent_count == 1
    send_mock.assert_called_once()
    assert send_mock.call_args[0][0].pk == notif.pk
    assert [d.pk for d in send_mock.call_args[0][1]] == [device.pk]


def test_18_disabling_dnd_restores_push(monkeypatch):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PushDevice.objects.create(
        user=user,
        profile=profile,
        token="restore-dnd-token-18",
        platform=PushDevice.Platform.ANDROID,
        is_active=True,
    )
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(
        timezone,
        "now",
        lambda: make_fixed_dt(2, 0),
    )
    send_mock = MagicMock(return_value=1)
    monkeypatch.setattr(
        "apps.notifications.fcm.send_notification_to_devices",
        send_mock,
    )

    notif = NotificationService.create_notification(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
        title="Congratulations! Points earned",
        body="Congratulations! You earned 45 PP Coins.",
        idempotency_key="dnd:18:restore",
    )

    # Suppressed while DND enabled
    assert deliver_notification_push(notif.pk) == 0
    assert send_mock.call_count == 0

    # Disable DND via PATCH -> push now sends even at 02:00
    res = auth_client(user).patch(
        PREFERENCES_URL,
        {"dnd_enabled": False},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert deliver_notification_push(notif.pk) == 1
    assert send_mock.call_count == 1


def test_19_same_dnd_push_behavior_across_customer_seller_mode_switch(
    monkeypatch,
):
    user, customer, seller = make_dual_profile_user()
    PushDevice.objects.create(
        user=user,
        profile=customer,
        token="cust-token-19",
        platform=PushDevice.Platform.ANDROID,
        is_active=True,
    )
    PushDevice.objects.create(
        user=user,
        profile=seller,
        token="seller-token-19",
        platform=PushDevice.Platform.IOS,
        is_active=True,
    )

    # Set DND while in Customer mode
    auth_client(user, profile_type=ProfileType.CUSTOMER).patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "23:00:00",
            "dnd_end": "08:00:00",
        },
        format="json",
    )

    # Switch to Seller mode
    switch_user_profile(user, new_profile_type=ProfileType.SELLER)
    seller.refresh_from_db()
    assert seller.is_active is True

    send_mock = MagicMock(return_value=1)
    monkeypatch.setattr(
        "apps.notifications.fcm.send_notification_to_devices",
        send_mock,
    )

    seller_notif = NotificationService.create_notification(
        recipient_profile=seller,
        notification_type=NotificationType.POINTS_EARNED,
        title="Congratulations! Points earned",
        body="Congratulations! You earned 100 PP Coins.",
        idempotency_key="dnd:19:seller",
    )

    # Inside DND window (01:00) -> Seller push suppressed by account-wide DND
    monkeypatch.setattr(timezone, "now", lambda: make_fixed_dt(1, 0))
    assert deliver_notification_push(seller_notif.pk) == 0
    assert send_mock.call_count == 0

    # Outside DND window (11:00) -> Seller push sent normally
    monkeypatch.setattr(timezone, "now", lambda: make_fixed_dt(11, 0))
    assert deliver_notification_push(seller_notif.pk) == 1
    assert send_mock.call_count == 1


# ===========================================================================
# TRANSACTION SAFETY (Tests 20 - 22)
# ===========================================================================


def test_20_business_rollback_still_leaves_no_notification(monkeypatch):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    NotificationPreferenceService.update_preference(
        user,
        dnd_enabled=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
    )
    monkeypatch.setattr(timezone, "now", lambda: make_fixed_dt(2, 0))

    with pytest.raises(RuntimeError, match="abort tx 20"):
        with transaction.atomic():
            PointService.earn_points(
                user=user,
                profile=profile,
                points=50,
                reason="Rollback",
                idempotency_key="dnd:20:rollback",
            )
            raise RuntimeError("abort tx 20")

    assert PointTransaction.objects.count() == 0
    assert Notification.objects.count() == 0


def test_21_rollback_triggers_no_websocket_or_fcm(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    ws_mock = MagicMock()
    fcm_mock = MagicMock()
    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        ws_mock,
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fcm_mock,
    )

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError, match="abort tx 21"):
            with transaction.atomic():
                BadgeService.award_badge(
                    profile=profile,
                    badge_type=BadgeType.WELCOME_BUYER,
                    idempotency_key="dnd:21:badge",
                )
                raise RuntimeError("abort tx 21")

    assert ws_mock.call_count == 0
    assert fcm_mock.call_count == 0
    assert BadgeAward.objects.count() == 0
    assert Notification.objects.count() == 0


def test_22_fcm_failure_does_not_roll_back_committed_notification_or_business_state(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    def fail_fcm(*args, **kwargs):
        raise RuntimeError("FCM broker failure")

    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fail_fcm,
    )

    with django_capture_on_commit_callbacks(execute=True):
        tx = PointService.earn_points(
            user=user,
            profile=profile,
            points=80,
            reason="Committed despite push error",
            idempotency_key="dnd:22:pp",
        )

    assert PointTransaction.objects.filter(pk=tx.pk).exists()
    assert Notification.objects.filter(
        recipient_profile=profile,
        object_id=tx.pk,
    ).exists()


# ===========================================================================
# REWARD FLOWS (Tests 23 - 29)
# ===========================================================================


def test_23_pp_reward_still_creates_one_notification():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    tx = PointService.earn_points(
        user=user,
        profile=profile,
        points=30,
        reason="Referral reward",
        source_type=RewardSourceType.REFERRAL_APPROVED,
        source_id="ref-23",
        idempotency_key="dnd:23:pp",
    )
    notifs = Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
    )
    assert notifs.count() == 1
    assert notifs.first().object_id == tx.pk


def test_24_badge_still_creates_one_notification():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    award = BadgeService.award_badge(
        profile=profile,
        badge_type=BadgeType.WELCOME_BUYER,
        idempotency_key="dnd:24:badge",
    )
    notifs = Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.BADGE_AWARDED,
    )
    assert notifs.count() == 1
    assert notifs.first().object_id == award.pk


def test_25_claim_required_prize_still_creates_one_notification():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    award = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="ref-25",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="dnd:25:prize",
    )
    notifs = Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert notifs.count() == 1
    assert notifs.first().object_id == award.pk


def test_26_digital_automatic_prize_still_creates_no_generic_duplicate():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="spin-26",
        title="50 PP Coins",
        prize_snapshot={"points_amount": 50},
        idempotency_key="dnd:26:digital",
    )
    assert Notification.objects.count() == 0


def test_27_lucky_wheel_pp_notification_unchanged():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    LuckyWheelGrantService.grant_attempt(
        profile=profile,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="deal-27",
        idempotency_key="dnd:27:grant",
    )
    LuckyWheelPrize.objects.create(
        label="60 PP Coins",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=60,
        weight=1,
        is_active=True,
    )
    Notification.objects.all().delete()

    LuckyWheelSpinService.spin(user, profile, "dnd:27:spin")

    assert (
        Notification.objects.filter(
            recipient_profile=profile,
            notification_type=NotificationType.POINTS_EARNED,
        ).count()
        == 1
    )
    assert (
        Notification.objects.filter(
            notification_type=NotificationType.PRIZE_AWARDED
        ).count()
        == 0
    )


def test_28_lucky_wheel_badge_notification_unchanged():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    LuckyWheelGrantService.grant_attempt(
        profile=profile,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="deal-28",
        idempotency_key="dnd:28:grant",
    )
    LuckyWheelPrize.objects.create(
        label="Trusted Buyer Badge",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.TRUSTED_BUYER,
        weight=1,
        is_active=True,
    )
    Notification.objects.all().delete()

    LuckyWheelSpinService.spin(user, profile, "dnd:28:spin")

    assert (
        Notification.objects.filter(
            recipient_profile=profile,
            notification_type=NotificationType.BADGE_AWARDED,
        ).count()
        == 1
    )
    assert (
        Notification.objects.filter(
            notification_type=NotificationType.PRIZE_AWARDED
        ).count()
        == 0
    )


def test_29_weekly_onboarding_draw_creates_no_notification_because_cancelled():
    payload = customer_registration_payload(phone_number="+201055443322")
    payload["username"] = "dnd_onboard_check"
    res = APIClient().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    user = UserAccount.objects.get(username="dnd_onboard_check")
    customer = CustomerProfile.objects.get(user=user)

    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    assert WeeklyDrawEntryService.ensure_for_onboarding(user, profile=customer) is None
    user_notifs = Notification.objects.filter(recipient_profile=customer)
    assert user_notifs.count() == 2
    assert {n.notification_type for n in user_notifs} == {
        NotificationType.POINTS_EARNED,
        NotificationType.BADGE_AWARDED,
    }


# ===========================================================================
# MESSAGES (Tests 30 - 33)
# ===========================================================================


def test_30_message_copy_is_short_and_supports_known_sources():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    sources = [
        (RewardSourceType.ONBOARDING, "for joining"),
        (RewardSourceType.REFERRAL_APPROVED, "from a referral"),
        (RewardSourceType.DEAL_COMPLETED, "for a completed deal"),
        (RewardSourceType.SEASON_WINNER, "as a season winner"),
        (RewardSourceType.LUCKY_WHEEL, "from Lucky Wheel"),
        (None, "PP Coins."),
    ]
    for idx, (source_type, expected_phrase) in enumerate(sources):
        tx = PointService.earn_points(
            user=user,
            profile=profile,
            points=10 * (idx + 1),
            reason=f"Source {idx}",
            source_type=source_type,
            source_id=str(idx),
            idempotency_key=f"msg:30:{idx}",
        )
        notif = Notification.objects.get(
            notification_type=NotificationType.POINTS_EARNED,
            object_id=tx.pk,
        )
        assert expected_phrase in notif.body
        assert len(notif.body) <= 80


def test_31_arabic_copy_remains_localized_for_all_sources():
    assert (
        localize_notification_text(
            "Congratulations! You earned 100 PP Coins for joining.", "ar"
        )
        == "تهانينا! لقد ربحت 100 نقطة PP لانضمامك."
    )
    assert (
        localize_notification_text(
            "Congratulations! You earned 30 PP Coins from a referral.", "ar"
        )
        == "تهانينا! لقد ربحت 30 نقطة PP من الإحالة."
    )
    assert (
        localize_notification_text(
            "Congratulations! You earned 50 PP Coins for a completed deal.",
            "ar",
        )
        == "تهانينا! لقد ربحت 50 نقطة PP لإتمام صفقة."
    )
    assert (
        localize_notification_text(
            "Congratulations! You earned 200 PP Coins as a season winner.", "ar"
        )
        == "تهانينا! لقد ربحت 200 نقطة PP كفائز بالموسم."
    )
    assert (
        localize_notification_text(
            "Congratulations! You earned 75 PP Coins from Lucky Wheel.", "ar"
        )
        == "تهانينا! لقد ربحت 75 نقطة PP من عجلة الحظ."
    )


def test_32_english_copy_remains_localized_for_all_sources():
    for text in (
        "Congratulations! You earned 100 PP Coins for joining.",
        "Congratulations! You earned 30 PP Coins from a referral.",
        "Congratulations! You earned 50 PP Coins for a completed deal.",
        "Congratulations! You earned 200 PP Coins as a season winner.",
        "Congratulations! You earned 75 PP Coins from Lucky Wheel.",
    ):
        assert localize_notification_text(text, "en") == text


def test_33_no_pii_in_message_or_metadata():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    award = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="ref-33",
        title="Gold Cup",
        prize_snapshot={"prize_code": "GOLD_CUP"},
        idempotency_key="secret:33:prize",
    )
    PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Private Winner Name",
        address="Private Street 123",
        identity_number="29801011234567",
    )

    notif = Notification.objects.get(
        recipient_profile=profile,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert "Private Winner Name" not in notif.body
    assert "Private Street 123" not in str(notif.metadata)
    assert "29801011234567" not in str(notif.metadata)
    assert "secret:33:prize" not in str(notif.metadata)


# ===========================================================================
# REGRESSION & SECURITY (Tests 34 - 37)
# ===========================================================================


def test_34_notification_apis_remain_green_with_dnd_enabled():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    client = auth_client(user)

    client.patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "23:00:00",
            "dnd_end": "08:00:00",
        },
        format="json",
    )
    PointService.earn_points(
        user=user,
        profile=profile,
        points=25,
        reason="Test API",
        idempotency_key="reg:34:pp",
    )
    notif = Notification.objects.get(recipient_profile=profile)

    unread_res = client.get("/api/notifications/unread-count/")
    assert unread_res.status_code == status.HTTP_200_OK
    assert unread_res.data == {"unread_count": 1}

    mark_res = client.post(f"/api/notifications/{notif.pk}/mark-read/")
    assert mark_res.status_code == status.HTTP_200_OK
    assert mark_res.data["is_read"] is True


def test_35_device_registration_and_unregistration_remain_green():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = auth_client(user)

    reg_res = client.post(
        "/api/notifications/devices/",
        {"token": "device-token-35", "platform": "android"},
        format="json",
    )
    assert reg_res.status_code == status.HTTP_201_CREATED
    device_id = reg_res.data["id"]

    list_res = client.get("/api/notifications/devices/")
    assert list_res.status_code == status.HTTP_200_OK
    assert len(list_res.data) == 1

    del_res = client.delete(f"/api/notifications/devices/{device_id}/")
    assert del_res.status_code == status.HTTP_204_NO_CONTENT


def test_36_manager_only_edits_own_account_dnd_not_other_users():
    manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    target_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    res = auth_client(manager_user).patch(
        PREFERENCES_URL,
        {
            "user_id": target_user.pk,
            "dnd_enabled": True,
            "dnd_start": "23:00:00",
            "dnd_end": "08:00:00",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert NotificationPreference.objects.filter(user=target_user).count() == 0


def test_37_profile_switching_preserves_account_wide_dnd_and_auth():
    user, customer, seller = make_dual_profile_user()
    client = auth_client(user, profile_type=ProfileType.CUSTOMER)

    client.patch(
        PREFERENCES_URL,
        {
            "dnd_enabled": True,
            "dnd_start": "22:30:00",
            "dnd_end": "07:30:00",
        },
        format="json",
    )

    switch_res = client.post(
        "/api/auth/switch-profile/",
        {"new_profile": ProfileType.SELLER},
        format="json",
    )
    assert switch_res.status_code == status.HTTP_200_OK

    seller_client = auth_client(user, profile_type=ProfileType.SELLER)
    pref_res = seller_client.get(PREFERENCES_URL)
    assert pref_res.status_code == status.HTTP_200_OK
    assert pref_res.data == {
        "dnd_enabled": True,
        "dnd_start": "22:30:00",
        "dnd_end": "07:30:00",
    }
