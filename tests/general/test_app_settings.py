from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.general.models import AppSettings
from apps.users.constants import ProfileType
from apps.users.models import ManagerProfile
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

USER_URL = "/api/app-settings/"
ADMIN_URL = "/api/app-settings/admin/"


def auth_client(user, lang="en"):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE=lang,
    )
    return client


def create_manager_with_permissions(permissions):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager_profile = ManagerProfile.objects.get(pk=user.active_profile.pk)
    manager_profile.permissions = list(permissions)
    manager_profile.save(update_fields=["permissions"])
    return user


def set_share(**fields):
    AppSettings.objects.update_or_create(
        pk=AppSettings.SINGLETON_ID, defaults=fields
    )


def test_customer_and_seller_read_default_flags_and_row_is_created_lazily():
    assert not AppSettings.objects.exists()

    for profile_type in (ProfileType.CUSTOMER, ProfileType.SELLER):
        user = UserAccountFactory(with_profile=profile_type)
        response = auth_client(user).get(USER_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"referral_share_visible": True}

    assert AppSettings.objects.count() == 1


def test_login_is_required():
    assert APIClient().get(USER_URL).status_code == (
        status.HTTP_401_UNAUTHORIZED
    )
    assert APIClient().get(ADMIN_URL).status_code == (
        status.HTTP_401_UNAUTHORIZED
    )


def test_disabled_share_is_hidden_regardless_of_window():
    now = timezone.now()
    set_share(
        referral_share_enabled=False,
        referral_share_starts_at=now - timedelta(hours=1),
        referral_share_ends_at=now + timedelta(hours=1),
    )
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    response = auth_client(customer).get(USER_URL)
    assert response.data == {"referral_share_visible": False}


@pytest.mark.parametrize(
    ("starts_offset", "ends_offset", "expected"),
    [
        (None, None, True),
        (-1, None, True),
        (None, 1, True),
        (-1, 1, True),
        (1, None, False),
        (1, 2, False),
        (None, -1, False),
        (-2, -1, False),
    ],
)
def test_visibility_follows_the_optional_window(
    starts_offset, ends_offset, expected
):
    now = timezone.now()

    def at(offset):
        return None if offset is None else now + timedelta(hours=offset)

    set_share(
        referral_share_enabled=True,
        referral_share_starts_at=at(starts_offset),
        referral_share_ends_at=at(ends_offset),
    )
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    response = auth_client(customer).get(USER_URL)
    assert response.data == {"referral_share_visible": expected}


def test_window_bounds_are_start_inclusive_and_end_exclusive():
    now = timezone.now()
    settings_row = AppSettings(
        referral_share_starts_at=now,
        referral_share_ends_at=now + timedelta(hours=1),
    )
    assert settings_row.is_referral_share_visible(now=now) is True
    assert (
        settings_row.is_referral_share_visible(now=now + timedelta(hours=1))
        is False
    )


def test_manager_with_permission_hides_share_for_a_period():
    manager = create_manager_with_permissions(
        ["app_settings.retrieve", "app_settings.update"]
    )
    client = auth_client(manager)
    starts_at = timezone.now() + timedelta(days=3)

    response = client.patch(
        ADMIN_URL,
        {"referral_share_starts_at": starts_at.isoformat()},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["referral_share_enabled"] is True
    assert response.data["referral_share_visible"] is False

    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert auth_client(customer).get(USER_URL).data == {
        "referral_share_visible": False
    }

    response = client.patch(
        ADMIN_URL, {"referral_share_starts_at": None}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["referral_share_visible"] is True

    response = client.get(ADMIN_URL)
    assert response.status_code == status.HTTP_200_OK
    assert set(response.data) == {
        "referral_share_enabled",
        "referral_share_starts_at",
        "referral_share_ends_at",
        "referral_share_visible",
        "modified",
    }


def test_manager_toggles_share_off_and_on():
    manager = create_manager_with_permissions(["app_settings.update"])
    client = auth_client(manager)

    response = client.patch(
        ADMIN_URL, {"referral_share_enabled": False}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    assert AppSettings.objects.get().referral_share_enabled is False

    response = client.patch(
        ADMIN_URL, {"referral_share_enabled": True}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    assert AppSettings.objects.get().referral_share_enabled is True


def test_window_validation_uses_stored_values_on_partial_update():
    manager = create_manager_with_permissions(["app_settings.update"])
    client = auth_client(manager)
    now = timezone.now()

    response = client.patch(
        ADMIN_URL,
        {
            "referral_share_starts_at": now.isoformat(),
            "referral_share_ends_at": now.isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "referral_share_ends_at" in response.data

    set_share(referral_share_starts_at=now + timedelta(days=2))
    response = client.patch(
        ADMIN_URL,
        {"referral_share_ends_at": (now + timedelta(days=1)).isoformat()},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert AppSettings.objects.get().referral_share_ends_at is None


def test_window_validation_message_is_localized():
    manager = create_manager_with_permissions(["app_settings.update"])
    now = timezone.now()

    response = auth_client(manager, lang="ar").patch(
        ADMIN_URL,
        {
            "referral_share_starts_at": now.isoformat(),
            "referral_share_ends_at": now.isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert str(response.data["referral_share_ends_at"][0]) == (
        "يجب أن يكون وقت انتهاء مشاركة رابط الإحالة بعد وقت البدء."
    )


def test_customers_sellers_and_unprivileged_managers_cannot_use_admin_api():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    read_only_manager = create_manager_with_permissions(
        ["app_settings.retrieve"]
    )
    no_permission_manager = create_manager_with_permissions([])

    for user in (customer, seller, no_permission_manager):
        client = auth_client(user)
        assert client.get(ADMIN_URL).status_code == status.HTTP_403_FORBIDDEN
        assert (
            client.patch(
                ADMIN_URL, {"referral_share_enabled": False}, format="json"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )

    client = auth_client(read_only_manager)
    assert client.get(ADMIN_URL).status_code == status.HTTP_200_OK
    assert (
        client.patch(
            ADMIN_URL, {"referral_share_enabled": False}, format="json"
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert not AppSettings.objects.filter(
        referral_share_enabled=False
    ).exists()


def test_only_one_settings_row_can_exist():
    AppSettings.objects.create(pk=AppSettings.SINGLETON_ID)
    with pytest.raises(IntegrityError), transaction.atomic():
        AppSettings.objects.create(pk=2)
