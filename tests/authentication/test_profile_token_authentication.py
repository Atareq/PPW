import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.authentication.services import (
    build_token_for_user,
    build_token_for_user_role,
)
from apps.users.constants import ProfileType
from tests.factories import SellerProfileFactory


def client_with_token(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.mark.django_db
def test_token_without_profile_claim_is_rejected(customer_user):
    refresh = RefreshToken.for_user(customer_user)
    client = client_with_token(refresh.access_token)

    response = client.get("/api/users/mobile/customers/")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_token_matching_current_profile_does_not_switch(customer_user):
    active_profile = customer_user.active_profile
    refresh = build_token_for_user(customer_user)
    client = client_with_token(refresh.access_token)

    response = client.get("/api/users/mobile/customers/")

    customer_user.refresh_from_db()
    assert response.status_code == status.HTTP_200_OK
    assert customer_user.active_profile.id == active_profile.id
    assert customer_user.active_profile.type == ProfileType.CUSTOMER


@pytest.mark.django_db
def test_token_profile_switches_active_profile(customer_user):
    seller_profile = SellerProfileFactory(
        user=customer_user,
        activated=True,
        is_active=False,
    )
    refresh = build_token_for_user_role(customer_user, seller_profile)
    client = client_with_token(refresh.access_token)

    response = client.get("/api/users/mobile/customers/")

    customer_user.refresh_from_db()
    assert response.status_code == status.HTTP_200_OK
    assert customer_user.active_profile.id == seller_profile.id
    assert customer_user.active_profile.type == ProfileType.SELLER


@pytest.mark.django_db
def test_token_profile_switch_failure_keeps_current_profile(customer_user):
    active_profile = customer_user.active_profile
    refresh = RefreshToken.for_user(customer_user)
    refresh["profile"] = ProfileType.SELLER
    client = client_with_token(refresh.access_token)

    response = client.get("/api/users/mobile/customers/")

    customer_user.refresh_from_db()
    assert response.status_code == status.HTTP_200_OK
    assert customer_user.active_profile.id == active_profile.id
    assert customer_user.active_profile.type == ProfileType.CUSTOMER
