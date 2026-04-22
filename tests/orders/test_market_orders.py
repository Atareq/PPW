from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.assets.models import Asset, AssetStatus
from apps.authentication.services import build_token_for_user
from apps.orders.models import OrderItemStatus, OrderStatus
from apps.orders.serializers import OrderCreateSerializer
from apps.orders.services import OrderService
from apps.orders.validators import OrderCreationValidator
from apps.users.constants import ProfileType
from tests.factories import (
    BirdFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


class TestOrderCreateSerializer:
    def test_rejects_empty_items(self):
        serializer = OrderCreateSerializer(data={"items": []})

        assert not serializer.is_valid()
        assert "items" in serializer.errors

    def test_rejects_bad_quantity(self):
        serializer = OrderCreateSerializer(
            data={"items": [{"asset_id": 1, "quantity": 0}]}
        )

        assert not serializer.is_valid()
        assert "items" in serializer.errors

    def test_rejects_duplicate_asset_ids(self, customer_user):
        items_payload = [
            {"asset_id": 1, "quantity": 1},
            {"asset_id": 1, "quantity": 2},
        ]
        with pytest.raises(ValidationError) as exc_info:
            OrderCreationValidator(
                customer_user.active_profile, items_payload
            ).validate()

        assert "Duplicate asset_id entries are not allowed." in str(
            exc_info.value
        )


class TestOrderCreationValidator:
    def test_rejects_non_customer_profile(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory()

        with pytest.raises(Exception, match="Only customer"):
            OrderCreationValidator(
                seller.active_profile,
                [{"asset_id": asset.pk, "quantity": 1}],
            ).validate()

    def test_rejects_unlisted_asset(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(is_market_listed=False)

        with pytest.raises(Exception, match="listed"):
            OrderCreationValidator(
                customer.active_profile,
                [{"asset_id": asset.pk, "quantity": 1}],
            ).validate()

    def test_rejects_same_user_seller_asset(self):
        user = UserAccountFactory(with_profile=False)
        customer = CustomerProfileFactory(user=user, is_active=True)
        seller = SellerProfileFactory(user=user, is_active=False)
        asset = SupplyFactory(owner=seller)

        with pytest.raises(Exception, match="own seller assets"):
            OrderCreationValidator(
                customer,
                [{"asset_id": asset.pk, "quantity": 1}],
            ).validate()

    def test_rejects_bird_quantity_above_one(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        bird = BirdFactory()

        with pytest.raises(Exception, match="quantity must be 1"):
            OrderCreationValidator(
                customer.active_profile,
                [{"asset_id": bird.pk, "quantity": 2}],
            ).validate()

    def test_rejects_product_quantity_above_stock(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=1)

        with pytest.raises(Exception, match="enough stock"):
            OrderCreationValidator(
                customer.active_profile,
                [{"asset_id": asset.pk, "quantity": 2}],
            ).validate()


class TestOrderService:
    def test_creates_multi_item_multi_seller_order_without_decrementing_stock(
        self,
    ):
        customer = CustomerProfileFactory()
        seller_one = SellerProfileFactory()
        seller_two = SellerProfileFactory()
        asset_one = SupplyFactory(
            owner=seller_one,
            count=5,
            price=Decimal("12.50"),
        )
        asset_two = SupplyFactory(
            owner=seller_two,
            count=3,
            price=Decimal("7.00"),
        )

        order = OrderService.create_order(
            customer,
            [
                {"asset_id": asset_one.pk, "quantity": 2},
                {"asset_id": asset_two.pk, "quantity": 1},
            ],
        )

        assert order.owner_id == customer.pk
        assert order.status == OrderStatus.OPEN
        assert order.items.count() == 2

        item_one = order.items.get(asset_id=asset_one.pk)
        assert item_one.seller_id == seller_one.pk
        assert item_one.unit_price_snapshot == Decimal("12.50")
        assert item_one.total_price == Decimal("25.00")
        assert item_one.status == OrderItemStatus.PENDING_SELLER_APPROVAL

        asset_one.refresh_from_db()
        asset_two.refresh_from_db()
        assert asset_one.count == 5
        assert asset_two.count == 3


class TestOrderEndpoints:
    def test_customer_can_create_and_list_only_own_orders(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=4)
        other_asset = SupplyFactory(count=4)
        OrderService.create_order(
            other_customer.active_profile,
            [{"asset_id": other_asset.pk, "quantity": 1}],
        )
        client = authenticated_client(customer)

        create_response = client.post(
            "/api/orders/",
            {"items": [{"asset_id": asset.pk, "quantity": 2}]},
            format="json",
        )
        list_response = client.get("/api/orders/")

        assert create_response.status_code == 201
        assert list_response.status_code == 200
        assert [order["id"] for order in list_response.data] == [
            create_response.data["id"]
        ]

    def test_customer_can_retrieve_own_order(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=4)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        client = authenticated_client(customer)

        response = client.get(f"/api/orders/{order.pk}/")

        assert response.status_code == 200
        assert response.data["id"] == order.pk

    def test_customer_cannot_retrieve_another_customers_order(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=4)
        order = OrderService.create_order(
            other_customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        client = authenticated_client(customer)

        response = client.get(f"/api/orders/{order.pk}/")

        assert response.status_code == 404

    def test_seller_cannot_list_or_retrieve_orders(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile, count=4)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        client = authenticated_client(seller)

        list_response = client.get("/api/orders/")
        detail_response = client.get(f"/api/orders/{order.pk}/")

        assert list_response.status_code == 403
        assert detail_response.status_code == 403

    def test_seller_item_list_is_filtered_to_own_items(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_one = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_two = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset_one = SupplyFactory(owner=seller_one.active_profile)
        asset_two = SupplyFactory(owner=seller_two.active_profile)
        order = OrderService.create_order(
            customer.active_profile,
            [
                {"asset_id": asset_one.pk, "quantity": 1},
                {"asset_id": asset_two.pk, "quantity": 1},
            ],
        )
        seller_one_item = order.items.get(asset_id=asset_one.pk)
        client = authenticated_client(seller_one)

        response = client.get("/api/orders/items/")

        assert response.status_code == 200
        assert [item["id"] for item in response.data] == [seller_one_item.pk]

    def test_customer_cannot_list_order_items(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)

        response = client.get("/api/orders/items/")

        assert response.status_code == 403

    def test_manager_can_list_all_orders_and_items(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset_one = SupplyFactory()
        asset_two = SupplyFactory()
        OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset_one.pk, "quantity": 1}],
        )
        OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset_two.pk, "quantity": 1}],
        )
        client = authenticated_client(manager)

        orders_response = client.get("/api/orders/")
        items_response = client.get("/api/orders/items/")

        assert orders_response.status_code == 200
        assert len(orders_response.data) == 2
        assert items_response.status_code == 200
        assert len(items_response.data) == 2

    def test_seller_approve_decrements_stock_once(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile, count=5)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 2}],
        )
        item = order.items.get()
        client = authenticated_client(seller)

        first_response = client.post(f"/api/orders/items/{item.pk}/approve/")
        second_response = client.post(f"/api/orders/items/{item.pk}/approve/")

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        item.refresh_from_db()
        asset.refresh_from_db()
        order.refresh_from_db()
        assert item.status == OrderItemStatus.SELLER_APPROVED
        assert asset.count == 3
        assert asset.status == AssetStatus.AVAILABLE
        assert order.status == OrderStatus.OPEN

    def test_seller_reject_does_not_decrement_stock_and_cancels_order(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile, count=5)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 2}],
        )
        item = order.items.get()
        client = authenticated_client(seller)

        response = client.post(f"/api/orders/items/{item.pk}/reject/")

        assert response.status_code == 200
        item.refresh_from_db()
        asset.refresh_from_db()
        order.refresh_from_db()
        assert item.status == OrderItemStatus.REJECTED
        assert asset.count == 5
        assert order.status == OrderStatus.CANCELLED

    def test_approval_fails_if_stock_drops_before_approval(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile, count=2)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 2}],
        )
        item = order.items.get()
        asset.count = 1
        asset.save(update_fields=["count"])
        client = authenticated_client(seller)

        response = client.post(f"/api/orders/items/{item.pk}/approve/")

        assert response.status_code == 400
        item.refresh_from_db()
        asset.refresh_from_db()
        assert item.status == OrderItemStatus.PENDING_SELLER_APPROVAL
        assert asset.count == 1

    def test_seller_cannot_approve_another_sellers_order_item(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_one = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_two = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller_two.active_profile, count=5)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 2}],
        )
        item = order.items.get()
        client = authenticated_client(seller_one)

        response = client.post(f"/api/orders/items/{item.pk}/approve/")

        assert response.status_code == 404
        item.refresh_from_db()
        asset.refresh_from_db()
        assert item.status == OrderItemStatus.PENDING_SELLER_APPROVAL
        assert asset.count == 5

    def test_bird_approval_marks_root_asset_sold_without_transfer(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        bird = BirdFactory(owner=seller.active_profile)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": bird.pk, "quantity": 1}],
        )
        item = order.items.get()
        client = authenticated_client(seller)

        response = client.post(f"/api/orders/items/{item.pk}/approve/")

        assert response.status_code == 200
        item.refresh_from_db()
        root_asset = Asset.objects.get(pk=bird.pk)
        assert item.status == OrderItemStatus.SELLER_APPROVED
        assert root_asset.count == 0
        assert root_asset.status == AssetStatus.SOLD
        assert root_asset.is_market_listed is False
        assert root_asset.owner_id == seller.active_profile.pk
