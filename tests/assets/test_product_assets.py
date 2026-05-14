from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.assets.models import (
    Accessory,
    AssetCategory,
    Bird,
    Feed,
    Supplement,
    Supply,
)
from apps.authentication.services import build_token_for_user
from apps.general.models import AssetImage, Image
from apps.packages.models import PackageUsageLog, PackageUsageType
from tests.factories import (
    ActiveAssetSellerPackageFactory,
    AssetImageFactory,
    PackageFactory,
)

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
        "price": "25.50",
        "status": "available",
        "is_market_listed": False,
        "is_for_auction": False,
    }
    payload.update(overrides)
    return payload


def image_payload(image_url="https://example.com/product.jpg", **overrides):
    payload = {
        "image_url": image_url,
        "alt_text": "",
        "is_primary": False,
        "order": 0,
    }
    payload.update(overrides)
    return payload


def bird_payload(**overrides):
    payload = {
        "title": "Image backed bird",
        "description": "Bird with images",
        "category": AssetCategory.BIRD,
        "status": "available",
        "count": 1,
        "price": "100.00",
        "is_market_listed": True,
        "is_for_auction": False,
        "name": "Fast bird",
        "ring_number": "IMG-BIRD-001",
        "gender": "male",
        "colour": "Blue",
        "birthday": None,
        "father": None,
        "mother": None,
        "achievements": "Fast training result",
        "flying_speed": "10.00",
        "stamina_ability": "good",
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


def create_active_package(owner, remaining_points=50, product_cost=7):
    return ActiveAssetSellerPackageFactory(
        owner=owner,
        package=PackageFactory(
            points=50,
            auction_cost=10,
            product_cost=product_cost,
        ),
        remaining_points=remaining_points,
    )


@pytest.mark.django_db
def test_asset_can_have_multiple_images_and_primary_image(seller_user):
    product = create_product(Supply, seller_user.active_profile)

    first = AssetImageFactory(asset=product)
    primary = AssetImageFactory(
        asset=product,
        image_url="https://example.com/primary.jpg",
        is_primary=True,
    )

    assert list(product.images.values_list("id", flat=True)) == [
        first.id,
        primary.id,
    ]
    assert primary.is_primary is True


@pytest.mark.django_db
def test_asset_image_ordering_uses_order_then_created(seller_user):
    product = create_product(Supply, seller_user.active_profile)
    now = timezone.now()
    first_same_order = AssetImageFactory(asset=product, order=1)
    second_same_order = AssetImageFactory(asset=product, order=1)
    lower_order = AssetImageFactory(asset=product, order=0)
    Image.objects.filter(pk=first_same_order.pk).update(created=now)
    Image.objects.filter(pk=second_same_order.pk).update(
        created=now + timedelta(seconds=1)
    )
    Image.objects.filter(pk=lower_order.pk).update(
        created=now + timedelta(seconds=2)
    )

    assert list(product.images.values_list("id", flat=True)) == [
        lower_order.id,
        first_same_order.id,
        second_same_order.id,
    ]


@pytest.mark.django_db
def test_bird_create_retrieve_and_list_include_images(
    authenticated_seller_client,
):
    create_response = authenticated_seller_client.post(
        "/api/assets/birds/",
        bird_payload(
            images=[
                image_payload(
                    "https://example.com/bird-primary.jpg",
                    is_primary=True,
                    order=0,
                ),
                image_payload(
                    "https://example.com/bird-secondary.jpg",
                    order=1,
                ),
            ]
        ),
        format="json",
    )

    assert (
        create_response.status_code == status.HTTP_201_CREATED
    ), create_response.data
    assert "thumbnail_url" not in create_response.data
    assert [image["image_url"] for image in create_response.data["images"]] == [
        "https://example.com/bird-primary.jpg",
        "https://example.com/bird-secondary.jpg",
    ]

    bird = Bird.objects.get(pk=create_response.data["id"])
    assert bird.images.count() == 2

    retrieve_response = authenticated_seller_client.get(
        f"/api/assets/birds/{bird.pk}/"
    )
    list_response = authenticated_seller_client.get("/api/assets/birds/")
    list_item = next(
        item for item in list_response.data["results"] if item["id"] == bird.pk
    )

    assert retrieve_response.status_code == status.HTTP_200_OK
    assert len(retrieve_response.data["images"]) == 2
    assert "thumbnail_url" not in retrieve_response.data
    assert len(list_item["images"]) == 2

    replace_response = authenticated_seller_client.patch(
        f"/api/assets/birds/{bird.pk}/",
        {
            "images": [
                image_payload(
                    "https://example.com/bird-replacement.jpg",
                    is_primary=True,
                )
            ]
        },
        format="json",
    )
    clear_response = authenticated_seller_client.patch(
        f"/api/assets/birds/{bird.pk}/",
        {"images": []},
        format="json",
    )

    assert replace_response.status_code == status.HTTP_200_OK
    assert [
        image["image_url"] for image in replace_response.data["images"]
    ] == ["https://example.com/bird-replacement.jpg"]
    assert clear_response.status_code == status.HTTP_200_OK
    assert clear_response.data["images"] == []
    assert AssetImage.objects.filter(asset=bird).count() == 0


@pytest.mark.django_db
def test_product_images_create_replace_preserve_and_clear(
    authenticated_seller_client,
    seller_user,
):
    seller_package = create_active_package(
        seller_user.active_profile,
        remaining_points=50,
        product_cost=7,
    )

    create_response = authenticated_seller_client.post(
        product_url("supplies"),
        product_payload(
            images=[
                image_payload(
                    "https://example.com/product-primary.jpg",
                    is_primary=True,
                    order=0,
                ),
                image_payload(
                    "https://example.com/product-secondary.jpg",
                    order=1,
                ),
            ]
        ),
        format="json",
    )

    assert create_response.status_code == status.HTTP_201_CREATED
    assert "thumbnail_url" not in create_response.data
    assert len(create_response.data["images"]) == 2
    product = Supply.objects.get(pk=create_response.data["id"])
    assert product.images.count() == 2
    seller_package.refresh_from_db()
    assert seller_package.remaining_points == 43
    assert PackageUsageLog.objects.count() == 1

    retrieve_response = authenticated_seller_client.get(
        product_url("supplies", product.pk)
    )
    list_response = authenticated_seller_client.get(product_url("supplies"))
    list_item = next(
        item
        for item in list_response.data["results"]
        if item["id"] == product.pk
    )

    assert retrieve_response.status_code == status.HTTP_200_OK
    assert len(retrieve_response.data["images"]) == 2
    assert len(list_item["images"]) == 2

    preserve_response = authenticated_seller_client.patch(
        product_url("supplies", product.pk),
        {"title": "Images preserved"},
        format="json",
    )

    assert preserve_response.status_code == status.HTTP_200_OK
    assert len(preserve_response.data["images"]) == 2
    assert product.images.count() == 2

    replace_response = authenticated_seller_client.patch(
        product_url("supplies", product.pk),
        {
            "images": [
                image_payload(
                    "https://example.com/replacement.jpg",
                    is_primary=True,
                )
            ]
        },
        format="json",
    )

    assert replace_response.status_code == status.HTTP_200_OK
    assert [image["image_url"] for image in replace_response.data["images"]] == [
        "https://example.com/replacement.jpg"
    ]
    assert AssetImage.objects.filter(asset=product).count() == 1

    clear_response = authenticated_seller_client.patch(
        product_url("supplies", product.pk),
        {"images": []},
        format="json",
    )

    seller_package.refresh_from_db()
    assert clear_response.status_code == status.HTTP_200_OK
    assert clear_response.data["images"] == []
    assert AssetImage.objects.filter(asset=product).count() == 0
    assert seller_package.remaining_points == 43
    assert PackageUsageLog.objects.count() == 1


@pytest.mark.django_db
def test_thumbnail_url_is_rejected_for_asset_api_payloads(
    authenticated_seller_client,
    seller_user,
):
    create_active_package(seller_user.active_profile)

    product_response = authenticated_seller_client.post(
        product_url("supplies"),
        product_payload(thumbnail_url="https://example.com/old.jpg"),
        format="json",
    )
    bird_response = authenticated_seller_client.post(
        "/api/assets/birds/",
        bird_payload(
            ring_number="IMG-BIRD-THUMB",
            thumbnail_url="https://example.com/old.jpg",
        ),
        format="json",
    )

    assert product_response.status_code == status.HTTP_400_BAD_REQUEST
    assert bird_response.status_code == status.HTTP_400_BAD_REQUEST
    assert "thumbnail_url" in product_response.data
    assert "thumbnail_url" in bird_response.data
    assert not Supply.objects.filter(title="Premium product").exists()
    assert not Bird.objects.filter(ring_number="IMG-BIRD-THUMB").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("model,route,category", PRODUCT_ENDPOINTS)
def test_seller_can_create_product_type_with_owner_and_category(
    model,
    route,
    category,
    authenticated_seller_client,
    seller_user,
):
    seller_package = create_active_package(
        seller_user.active_profile,
        remaining_points=50,
        product_cost=7,
    )

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
    seller_package.refresh_from_db()
    assert seller_package.remaining_points == 43
    log = PackageUsageLog.objects.get()
    assert log.usage_type == PackageUsageType.PRODUCT_CREATED
    assert log.points_used == 7
    assert log.related_object_type == f"assets.{model.__name__}"
    assert log.related_object_id == product.pk


@pytest.mark.django_db
def test_seller_product_creation_fails_without_active_package(
    authenticated_seller_client,
):
    response = authenticated_seller_client.post(
        product_url("supplies"),
        product_payload(title="No package product"),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "No active package" in str(response.data)
    assert not Supply.objects.filter(title="No package product").exists()
    assert PackageUsageLog.objects.count() == 0


@pytest.mark.django_db
def test_seller_product_creation_fails_with_insufficient_points(
    authenticated_seller_client,
    seller_user,
):
    seller_package = create_active_package(
        seller_user.active_profile,
        remaining_points=2,
        product_cost=7,
    )

    response = authenticated_seller_client.post(
        product_url("supplies"),
        product_payload(title="Insufficient product"),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Insufficient package points" in str(response.data)
    seller_package.refresh_from_db()
    assert seller_package.remaining_points == 2
    assert not Supply.objects.filter(title="Insufficient product").exists()
    assert PackageUsageLog.objects.count() == 0


@pytest.mark.django_db
def test_failed_product_validation_does_not_consume_points(
    authenticated_seller_client,
    seller_user,
):
    seller_package = create_active_package(seller_user.active_profile)

    response = authenticated_seller_client.post(
        product_url("supplies"),
        product_payload(title=""),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    seller_package.refresh_from_db()
    assert seller_package.remaining_points == 50
    assert PackageUsageLog.objects.count() == 0


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
    seller_package = create_active_package(seller_user.active_profile)

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
    seller_package.refresh_from_db()
    assert seller_package.remaining_points == 50
    assert PackageUsageLog.objects.count() == 0


@pytest.mark.django_db
def test_product_listing_change_does_not_consume_points(
    authenticated_seller_client,
    seller_user,
):
    product = create_product(
        Supply,
        seller_user.active_profile,
        is_market_listed=False,
    )
    seller_package = create_active_package(seller_user.active_profile)

    response = authenticated_seller_client.patch(
        product_url("supplies", product.pk),
        {"is_market_listed": True},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    seller_package.refresh_from_db()
    assert seller_package.remaining_points == 50
    assert PackageUsageLog.objects.count() == 0


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
    listed_ids = {item["id"] for item in list_response.data["results"]}
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
    ids = {item["id"] for item in response.data["results"]}

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
    ids = {item["id"] for item in list_response.data["results"]}
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
    assert PackageUsageLog.objects.count() == 0
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
    ids = {item["id"] for item in response.data["results"]}

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
    ids = [item["id"] for item in response.data["results"]]

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
    ids = {item["id"] for item in response.data["results"]}

    assert response.status_code == status.HTTP_200_OK
    assert unlisted.pk in ids
    assert listed.pk not in ids
