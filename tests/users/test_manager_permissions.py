from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from importlib import import_module
import pytest
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.manager_permissions import (
    get_manager_permission_codes,
    normalize_manager_permissions,
    validate_manager_permissions,
)
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile
from apps.users.services import ManagerPermissionService
from tests.factories import UserAccountFactory

copy_user_permissions_to_manager_profiles = import_module(
    "apps.users.migrations.0002_managerprofile_permissions"
).copy_user_permissions_to_manager_profiles


pytestmark = pytest.mark.django_db


def authenticated_client_for(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def test_manager_user_cannot_create_seller_profile(manager_user):
    client = authenticated_client_for(manager_user)
    payload = {
        "country": CountryOptions.EGYPT,
        "currency": CurrencyOptions.EGP,
        "recoded_voice": "voice",
    }

    response = client.post("/api/users/mobile/sellers/", payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["message"] == "Manager accounts cannot create seller profiles"


def test_manager_user_cannot_create_customer_profile(manager_user):
    client = authenticated_client_for(manager_user)
    payload = {
        "country": CountryOptions.EGYPT,
        "currency": CurrencyOptions.EGP,
    }

    response = client.post("/api/users/mobile/customers/", payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["message"] == "Manager accounts cannot create customer profiles"


def test_customer_user_cannot_become_manager_profile(customer_user):
    with pytest.raises(ValidationError):
        ManagerProfile.objects.create(
            user=customer_user,
            is_active=True,
            activated=True,
        )


def test_seller_user_cannot_become_manager_profile(seller_user):
    with pytest.raises(ValidationError):
        ManagerProfile.objects.create(
            user=seller_user,
            is_active=True,
            activated=True,
        )


def test_manager_user_cannot_create_customer_or_seller_profiles_in_model(manager_user):
    with pytest.raises(ValidationError):
        CustomerProfile.objects.create(
            user=manager_user,
            country=CountryOptions.EGYPT,
            currency=CurrencyOptions.EGP,
        )

    with pytest.raises(ValidationError):
        SellerProfile.objects.create(
            user=manager_user,
            country=CountryOptions.EGYPT,
            currency=CurrencyOptions.EGP,
        )


def test_validate_manager_permissions_accepts_valid_list():
    permissions = ["assets.list", "orders.full_control"]

    assert validate_manager_permissions(permissions) == permissions


def test_validate_manager_permissions_rejects_invalid_code():
    with pytest.raises(ValidationError):
        validate_manager_permissions(["assets.invalid"])


def test_validate_manager_permissions_rejects_non_list():
    with pytest.raises(ValidationError):
        validate_manager_permissions("assets.list")


def test_validate_manager_permissions_rejects_non_string_item():
    with pytest.raises(ValidationError):
        validate_manager_permissions(["assets.list", 1])


def test_normalize_manager_permissions_removes_duplicates_and_keeps_highest_level():
    permissions = [
        "assets.list",
        "assets.edit",
        "assets.list",
        "orders.list",
        "orders.full_control",
    ]

    assert normalize_manager_permissions(permissions) == [
        "assets.edit",
        "orders.full_control",
    ]


def test_normalize_manager_permissions_accepts_empty_list_and_none():
    assert normalize_manager_permissions([]) == []
    assert normalize_manager_permissions(None) == []


def test_permission_hierarchy_full_control_allows_edit_and_list(manager_user):
    manager_profile = manager_user.active_profile
    manager_profile.permissions = ["assets.full_control"]
    manager_profile.save()

    assert ManagerPermissionService.has_permission(
        manager_profile, "assets.edit"
    )
    assert ManagerPermissionService.has_permission(
        manager_profile, "assets.list"
    )


def test_permission_hierarchy_edit_allows_list_but_not_full_control(manager_user):
    manager_profile = manager_user.active_profile
    manager_profile.permissions = ["assets.edit"]
    manager_profile.save()

    assert ManagerPermissionService.has_permission(
        manager_profile, "assets.list"
    )
    assert not ManagerPermissionService.has_permission(
        manager_profile, "assets.full_control"
    )


def test_permission_hierarchy_list_does_not_allow_edit(manager_user):
    manager_profile = manager_user.active_profile
    manager_profile.permissions = ["assets.list"]
    manager_profile.save()

    assert not ManagerPermissionService.has_permission(
        manager_profile, "assets.edit"
    )


def test_permissions_are_stored_on_manager_profile_not_user_permissions(manager_user):
    manager_user.permissions = ["assets.full_control"]
    manager_user.save(update_fields=["permissions"])

    manager_profile = manager_user.active_profile
    manager_profile.permissions = []
    manager_profile.save()

    assert not manager_user.has_app_permission("assets.list")


def test_customer_permissions_do_not_grant_manager_permission(customer_user):
    customer_user.permissions = ["assets.full_control"]
    customer_user.save(update_fields=["permissions"])

    with pytest.raises(PermissionDenied):
        ManagerPermissionService.require_permission(
            customer_user,
            "assets.list",
        )


def test_superuser_bypass_works_without_manager_profile():
    user = UserAccountFactory(with_profile=False)
    user.is_superuser = True
    user.is_staff = True
    user.save(update_fields=["is_superuser", "is_staff"])

    assert ManagerPermissionService.require_permission(
        user, "assets.full_control"
    )


def test_superuser_can_create_manager(superuser_manager):
    client = authenticated_client_for(superuser_manager)
    payload = {
        "username": "manager-created-by-superuser",
        "phone_number": "+201001234567",
        "password": "SecurePass123!@",
        "permissions": ["assets.list", "assets.edit", "orders.full_control"],
    }

    response = client.post("/api/users/admin/managers/", payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    created_profile = ManagerProfile.objects.get(
        user__username="manager-created-by-superuser"
    )
    assert created_profile.permissions == [
        "assets.edit",
        "orders.full_control",
    ]
    assert created_profile.user.is_staff is True


def test_manager_creation_rejects_existing_customer_user(
    superuser_manager, customer_user
):
    client = authenticated_client_for(superuser_manager)
    payload = {
        "username": "duplicate-customer-phone",
        "phone_number": str(customer_user.phone_number),
        "password": "SecurePass123!@",
        "permissions": ["assets.list"],
    }

    response = client.post("/api/users/admin/managers/", payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "phone_number" in response.data


def test_superuser_can_update_manager_permissions(superuser_manager, manager_user):
    client = authenticated_client_for(superuser_manager)
    manager_profile = manager_user.active_profile

    response = client.patch(
        f"/api/users/admin/managers/{manager_profile.pk}/permissions/",
        {"permissions": ["assets.list", "assets.full_control"]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    manager_profile.refresh_from_db()
    assert manager_profile.permissions == ["assets.full_control"]


def test_invalid_permissions_rejected_by_manager_api(
    superuser_manager, manager_user
):
    client = authenticated_client_for(superuser_manager)
    manager_profile = manager_user.active_profile

    response = client.patch(
        f"/api/users/admin/managers/{manager_profile.pk}/permissions/",
        {"permissions": ["assets.nope"]},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "permissions" in response.data


def test_permission_registry_endpoint_returns_valid_codes(superuser_manager):
    client = authenticated_client_for(superuser_manager)

    response = client.get("/api/users/admin/manager-permissions/")

    assert response.status_code == status.HTTP_200_OK
    returned_codes = {item["code"] for item in response.data["permissions"]}
    assert get_manager_permission_codes().issubset(returned_codes)


def test_manager_admin_api_rejects_unauthenticated(api_client):
    response = api_client.get("/api/users/admin/managers/")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_manager_admin_api_rejects_customer(authenticated_customer_client):
    response = authenticated_customer_client.get("/api/users/admin/managers/")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_manager_admin_api_rejects_normal_manager(manager_user):
    client = authenticated_client_for(manager_user)
    response = client.get("/api/users/admin/managers/")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_migration_copies_permissions_to_existing_manager_profile():
    user = UserAccount.objects.create_user(
        username="legacy-manager",
        phone_number="+201001239999",
        password="SecurePass123!@",
        permissions=["assets", "users", "orders.full_control"],
    )
    manager_profile = ManagerProfile.objects.create(
        user=user,
        is_active=True,
        activated=True,
        permissions=[],
    )

    copy_user_permissions_to_manager_profiles(django_apps, None)

    manager_profile.refresh_from_db()
    assert manager_profile.permissions == [
        "assets.full_control",
        "orders.full_control",
        "users.full_control",
    ]


def test_migration_does_not_create_missing_manager_profile():
    user = UserAccount.objects.create_user(
        username="legacy-user-no-manager",
        phone_number="+201001238888",
        password="SecurePass123!@",
        permissions=["assets"],
    )

    copy_user_permissions_to_manager_profiles(django_apps, None)

    assert not ManagerProfile.objects.filter(user=user).exists()
