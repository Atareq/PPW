import pytest
from rest_framework import status

from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db

CUSTOMER_PROFILE_URL = "/api/users/mobile/customers/"
SELLER_PROFILE_URL = "/api/users/mobile/sellers/"


def test_customer_can_create_inactive_seller_profile(
    authenticated_customer_client, customer_user
):
    payload = {
        "country": CountryOptions.EGYPT,
        "currency": CurrencyOptions.EGP,
        "recoded_voice": "voice-key",
        "nickname": "client-provided-name",
    }

    response = authenticated_customer_client.post(
        SELLER_PROFILE_URL, payload, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED

    seller_profile = SellerProfile.objects.get(user=customer_user)
    assert seller_profile.type == ProfileType.SELLER
    assert seller_profile.nickname == customer_user.username
    assert seller_profile.is_active is False
    assert seller_profile.country == CountryOptions.EGYPT
    assert seller_profile.currency == CurrencyOptions.EGP

    customer_user.refresh_from_db()
    assert customer_user.active_profile.type == ProfileType.CUSTOMER


def test_seller_profile_create_rejects_existing_seller_profile(
    authenticated_seller_client, seller_user
):
    payload = {
        "country": CountryOptions.EGYPT,
        "currency": CurrencyOptions.EGP,
        "recoded_voice": "voice-key",
    }

    response = authenticated_seller_client.post(
        SELLER_PROFILE_URL, payload, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data == {"message": "Profile already exists"}
    assert SellerProfile.objects.filter(user=seller_user).count() == 1


def test_seller_mobile_list_returns_only_current_users_active_seller(
    authenticated_seller_client, seller_user
):
    own_profile = SellerProfile.objects.get(user=seller_user)
    own_profile.nickname = "current-seller"
    own_profile.save(update_fields=["nickname"])
    other_user = UserAccountFactory(with_profile=False)
    SellerProfileFactory(
        user=other_user,
        nickname="other-seller",
        is_active=True,
    )

    response = authenticated_seller_client.get(SELLER_PROFILE_URL)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["results"] == [
        {
            "country": own_profile.country,
            "currency": own_profile.currency,
            "recoded_voice": own_profile.recoded_voice,
            "nickname": "current-seller",
        }
    ]


def test_customer_mobile_list_returns_only_current_users_customer_profile(
    authenticated_customer_client, customer_user
):
    own_profile = CustomerProfile.objects.get(user=customer_user)
    other_user = UserAccountFactory(with_profile=False)
    CustomerProfileFactory(user=other_user, is_active=True)

    response = authenticated_customer_client.get(CUSTOMER_PROFILE_URL)

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data["results"]) == 1
    assert response.data["results"][0]["id"] == own_profile.id
    assert response.data["results"][0]["country"] == own_profile.country
    assert response.data["results"][0]["currency"] == own_profile.currency


def test_customer_delete_switches_to_an_available_profile_and_soft_deletes(
    authenticated_customer_client, customer_user
):
    customer_profile = CustomerProfile.objects.get(user=customer_user)
    seller_profile = SellerProfileFactory(
        user=customer_user,
        activated=True,
        is_active=False,
    )

    response = authenticated_customer_client.delete(
        f"{CUSTOMER_PROFILE_URL}{customer_profile.id}/"
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    deleted_customer = CustomerProfile.all_objects.get(id=customer_profile.id)
    assert deleted_customer.deleted is not None
    assert deleted_customer.is_active is False
    assert deleted_customer.is_valid is False

    customer_user.refresh_from_db()
    assert customer_user.active_profile.id == seller_profile.id
    assert customer_user.active_profile.type == ProfileType.SELLER


def test_seller_delete_soft_deletes_active_seller_profile(
    authenticated_seller_client, seller_user
):
    seller_profile = SellerProfile.objects.get(user=seller_user)

    response = authenticated_seller_client.delete(
        f"{SELLER_PROFILE_URL}{seller_profile.id}/"
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    deleted_seller = SellerProfile.all_objects.get(id=seller_profile.id)
    assert deleted_seller.deleted is not None
    assert deleted_seller.is_active is False
    assert deleted_seller.is_valid is False


def test_seller_delete_switches_to_an_available_profile(
    authenticated_seller_client, seller_user
):
    seller_profile = SellerProfile.objects.get(user=seller_user)
    customer_profile = CustomerProfileFactory(
        user=seller_user,
        activated=True,
        is_active=False,
    )

    response = authenticated_seller_client.delete(
        f"{SELLER_PROFILE_URL}{seller_profile.id}/"
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    seller_user.refresh_from_db()
    assert seller_user.active_profile.id == customer_profile.id
    assert seller_user.active_profile.type == ProfileType.CUSTOMER
