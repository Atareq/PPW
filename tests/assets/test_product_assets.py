from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.assets.models import (
    Accessory,
    AssetCategory,
    Feed,
    Supplement,
    Supply,
)
from apps.authentication.services import build_token_for_user

PRODUCT_ENDPOINTS = [
    (Supply, "supplies", AssetCategory.SUPPLY),
    (Accessory, "accessories", AssetCategory.ACCESSORY),
    (Feed, "feeds", AssetCategory.FEED),
    (Supplement, "supplements", AssetCategory.SUPPLEMENT),
]


def authenticated_client_for(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def product_url(route, pk=None):
    if pk is None:
        return f"/api/assets/{route}/"
    return f"/api/assets/{route}/{pk}/"


def product_payload(**overrides):
    payload = {
        "title": "Premium product",
        "description": "Shared asset product",
        "count": 5,
        "thumbnail_url": "https://example.com/product.jpg",
        "price": "25.50",
        "status": "available",
        "is_market_listed": False,
        "is_for_auction": False,
    }
    payload.update(overrides)
    return payload


def create_product(model, owner, **overrides):
    data = {
        "owner": owner,
        "title": "Stored product",
        "description": "Existing product",
        "count": 3,
        "price": Decimal("10.00"),
        "is_market_listed": False,
        "is_for_auction": False,
    }
    data.update(overrides)
    return model.objects.create(**data)


@pytest.mark.django_db
@pytest.mark.parametrize("model,route,category", PRODUCT_ENDPOINTS)
def test_seller_can_create_product_type_with_owner_and_category(
    model,
    route,
    category,
    authenticated_seller_client,
    seller_user,
):
    response = authenticated_seller_client.post(
        product_url(route),
        product_payload(
            owner=99999,
            category=AssetCategory.BIRD,
        ),
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED

    product = model.objects.get(pk=response.data["id"])
    assert product.owner_id == seller_user.active_profile.id
    assert product.category == category
    assert response.data["owner"] == seller_user.active_profile.id
    assert response.data["category"] == category


@pytest.mark.django_db
def test_customer_cannot_create_update_or_delete_product_assets(
    authenticated_customer_client,
    seller_user,
):
    product = create_product(Supply, seller_user.active_profile)

    create_response = authenticated_customer_client.post(
        product_url("supplies"),
        product_payload(),
        format="json",
    )
    update_response = authenticated_customer_client.patch(
        product_url("supplies", product.pk),
        {"title": "Customer edit"},
        format="json",
    )
    delete_response = authenticated_customer_client.delete(
        product_url("supplies", product.pk)
    )

    assert create_response.status_code == status.HTTP_403_FORBIDDEN
    assert update_response.status_code == status.HTTP_403_FORBIDDEN
    assert delete_response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_seller_can_update_and_delete_own_product(
    authenticated_seller_client,
    seller_user,
):
    product = create_product(Supply, seller_user.active_profile)

    update_response = authenticated_seller_client.patch(
        product_url("supplies", product.pk),
        {"title": "Updated by owner"},
        format="json",
    )

    assert update_response.status_code == status.HTTP_200_OK
    product.refresh_from_db()
    assert product.title == "Updated by owner"

    delete_response = authenticated_seller_client.delete(
        product_url("supplies", product.pk)
    )

    assert delete_response.status_code == status.HTTP_204_NO_CONTENT
    assert not Supply.objects.filter(pk=product.pk).exists()


@pytest.mark.django_db
def test_seller_cannot_update_or_delete_another_sellers_product(
    authenticated_seller_client,
    seller_user_2,
):
    product = create_product(Supply, seller_user_2.active_profile)

    update_response = authenticated_seller_client.patch(
        product_url("supplies", product.pk),
        {"title": "Wrong seller edit"},
        format="json",
    )
    delete_response = authenticated_seller_client.delete(
        product_url("supplies", product.pk)
    )

    assert update_response.status_code == status.HTTP_403_FORBIDDEN
    assert delete_response.status_code == status.HTTP_403_FORBIDDEN
    product.refresh_from_db()
    assert product.title == "Stored product"


@pytest.mark.django_db
def test_customer_only_sees_market_listed_products(
    authenticated_customer_client,
    seller_user,
):
    listed = create_product(
        Supply,
        seller_user.active_profile,
        title="Listed supply",
        is_market_listed=True,
    )
    unlisted = create_product(
        Supply,
        seller_user.active_profile,
        title="Hidden supply",
        is_market_listed=False,
    )

    list_response = authenticated_customer_client.get(product_url("supplies"))
    listed_ids = {item["id"] for item in list_response.data}
    hidden_response = authenticated_customer_client.get(
        product_url("supplies", unlisted.pk)
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert listed.pk in listed_ids
    assert unlisted.pk not in listed_ids
    assert hidden_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_seller_sees_own_unlisted_and_market_listed_products(
    authenticated_seller_client,
    seller_user,
    seller_user_2,
):
    owned_unlisted = create_product(
        Supply,
        seller_user.active_profile,
        title="Own hidden supply",
        is_market_listed=False,
    )
    other_unlisted = create_product(
        Supply,
        seller_user_2.active_profile,
        title="Other hidden supply",
        is_market_listed=False,
    )
    listed_other = create_product(
        Supply,
        seller_user_2.active_profile,
        title="Other listed supply",
        is_market_listed=True,
    )

    response = authenticated_seller_client.get(product_url("supplies"))
    ids = {item["id"] for item in response.data}

    assert response.status_code == status.HTTP_200_OK
    assert owned_unlisted.pk in ids
    assert listed_other.pk in ids
    assert other_unlisted.pk not in ids


@pytest.mark.django_db
def test_manager_can_list_create_update_and_delete_all_products(
    manager_user,
    seller_user,
    seller_user_2,
):
    manager_client = authenticated_client_for(manager_user)
    product = create_product(Supply, seller_user.active_profile)
    other_product = create_product(
        Supply,
        seller_user_2.active_profile,
        title="Other seller supply",
    )

    list_response = manager_client.get(product_url("supplies"))
    ids = {item["id"] for item in list_response.data}
    create_response = manager_client.post(
        product_url("supplies"),
        product_payload(title="Manager product"),
        format="json",
    )
    update_response = manager_client.patch(
        product_url("supplies", other_product.pk),
        {"title": "Manager edit"},
        format="json",
    )
    delete_response = manager_client.delete(
        product_url("supplies", product.pk)
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert product.pk in ids
    assert other_product.pk in ids
    assert create_response.status_code == status.HTTP_201_CREATED
    assert create_response.data["owner"] == manager_user.active_profile.id
    assert create_response.data["category"] == AssetCategory.SUPPLY
    assert update_response.status_code == status.HTTP_200_OK
    other_product.refresh_from_db()
    assert other_product.title == "Manager edit"
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
@pytest.mark.parametrize("model,route,category", PRODUCT_ENDPOINTS)
def test_product_endpoints_filter_by_status(
    model,
    route,
    category,
    authenticated_customer_client,
    seller_user,
):
    available = create_product(
        model,
        seller_user.active_profile,
        title=f"Available {category}",
        status="available",
        is_market_listed=True,
    )
    inactive = create_product(
        model,
        seller_user.active_profile,
        title=f"Inactive {category}",
        status="inactive",
        is_market_listed=True,
    )

    response = authenticated_customer_client.get(
        product_url(route),
        {"status": "available"},
    )
    ids = {item["id"] for item in response.data}

    assert response.status_code == status.HTTP_200_OK
    assert available.pk in ids
    assert inactive.pk not in ids


@pytest.mark.django_db
def test_product_filters_support_ranges_search_owner_and_ordering(
    authenticated_customer_client,
    seller_user,
    seller_user_2,
):
    matching = create_product(
        Supply,
        seller_user.active_profile,
        title="Premium grain mix",
        description="High protein feed",
        count=6,
        price=Decimal("20.00"),
        is_market_listed=True,
    )
    cheaper = create_product(
        Supply,
        seller_user.active_profile,
        title="Premium grain budget",
        description="Budget feed",
        count=2,
        price=Decimal("8.00"),
        is_market_listed=True,
    )
    other_owner = create_product(
        Supply,
        seller_user_2.active_profile,
        title="Premium grain other",
        description="Other seller feed",
        count=7,
        price=Decimal("24.00"),
        is_market_listed=True,
    )

    response = authenticated_customer_client.get(
        product_url("supplies"),
        {
            "search": "grain",
            "owner": seller_user.active_profile.id,
            "min_price": "10",
            "max_price": "30",
            "min_count": "5",
            "ordering": "-price",
        },
    )
    ids = [item["id"] for item in response.data]

    assert response.status_code == status.HTTP_200_OK
    assert ids == [matching.pk]
    assert cheaper.pk not in ids
    assert other_owner.pk not in ids


@pytest.mark.django_db
def test_product_filters_support_boolean_values(
    manager_user,
    seller_user,
):
    manager_client = authenticated_client_for(manager_user)
    listed = create_product(
        Supply,
        seller_user.active_profile,
        title="Listed supply",
        is_market_listed=True,
    )
    unlisted = create_product(
        Supply,
        seller_user.active_profile,
        title="Unlisted supply",
        is_market_listed=False,
    )

    response = manager_client.get(
        product_url("supplies"),
        {"is_market_listed": "false"},
    )
    ids = {item["id"] for item in response.data}

    assert response.status_code == status.HTTP_200_OK
    assert unlisted.pk in ids
    assert listed.pk not in ids
