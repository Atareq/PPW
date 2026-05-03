from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.assets.models import AssetStatus
from apps.authentication.services import build_token_for_user
from apps.orders.models import (
    Cart,
    CartItem,
    CartStatus,
    OrderEvent,
    OrderEventType,
    OrderItemStatus,
    OrderStatus,
)
from apps.orders.serializers import OrderCreateSerializer
from apps.orders.services import CartService, OrderService
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

        with pytest.raises(Exception, match="Insufficient stock"):
            OrderCreationValidator(
                customer.active_profile,
                [{"asset_id": asset.pk, "quantity": 2}],
            ).validate()


class TestCartEndpoints:
    def test_active_cart_is_created_lazily_once(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)

        first_response = client.get("/api/orders/cart/")
        second_response = client.get("/api/orders/cart/")

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert first_response.data["id"] == second_response.data["id"]
        assert (
            Cart.objects.filter(
                customer=customer.active_profile,
                status=CartStatus.ACTIVE,
            ).count()
            == 1
        )

    def test_add_item_to_cart(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=4, price=Decimal("12.00"))
        client = authenticated_client(customer)

        response = client.post(
            "/api/orders/cart/items/",
            {"asset_id": asset.pk, "quantity": 2},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["items_count"] == 1
        assert response.data["subtotal"] == "24.00"
        item = response.data["items"][0]
        assert item["asset_id"] == asset.pk
        assert item["quantity"] == 2
        asset.refresh_from_db()
        assert asset.count == 4

    def test_duplicate_item_add_merges_quantity(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=5)
        client = authenticated_client(customer)

        client.post(
            "/api/orders/cart/items/",
            {"asset_id": asset.pk, "quantity": 1},
            format="json",
        )
        response = client.post(
            "/api/orders/cart/items/",
            {"asset_id": asset.pk, "quantity": 2},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["items_count"] == 1
        assert response.data["items"][0]["quantity"] == 3
        assert CartItem.objects.count() == 1

    def test_update_quantity_validates_stock(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=3)
        client = authenticated_client(customer)
        cart = CartService.add_item(customer.active_profile, asset.pk, 1)
        item = cart.items.get()

        response = client.patch(
            f"/api/orders/cart/items/{item.pk}/",
            {"quantity": 4},
            format="json",
        )

        assert response.status_code == 400
        item.refresh_from_db()
        assert item.quantity == 1

    def test_remove_item(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        client = authenticated_client(customer)
        cart = CartService.add_item(customer.active_profile, asset.pk, 1)
        item = cart.items.get()

        response = client.delete(f"/api/orders/cart/items/{item.pk}/")

        assert response.status_code == 204
        assert CartItem.objects.count() == 0

    def test_clear_cart(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset_one = SupplyFactory()
        asset_two = SupplyFactory()
        client = authenticated_client(customer)
        CartService.add_item(customer.active_profile, asset_one.pk, 1)
        CartService.add_item(customer.active_profile, asset_two.pk, 1)

        response = client.delete("/api/orders/cart/clear/")

        assert response.status_code == 204
        assert CartItem.objects.count() == 0

    def test_seller_mode_cannot_add_or_checkout(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory()
        client = authenticated_client(seller)

        add_response = client.post(
            "/api/orders/cart/items/",
            {"asset_id": asset.pk, "quantity": 1},
            format="json",
        )
        checkout_response = client.post(
            "/api/orders/checkout/", {}, format="json"
        )

        assert add_response.status_code == 403
        assert checkout_response.status_code == 403

    def test_manager_mode_reaches_service_validation_for_cart_and_checkout(
        self,
    ):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        client = authenticated_client(manager)

        cart_response = client.get("/api/orders/cart/")
        checkout_response = client.post(
            "/api/orders/checkout/", {}, format="json"
        )

        assert cart_response.status_code == 400
        assert checkout_response.status_code == 400
        assert "Only customer profiles" in str(cart_response.data)
        assert "Only customer profiles" in str(checkout_response.data)

    def test_user_without_active_role_is_denied_by_app_permission(self):
        user = UserAccountFactory(with_profile=False)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get("/api/orders/cart/")

        assert response.status_code == 403

    def test_own_product_is_blocked(self):
        user = UserAccountFactory(with_profile=False)
        CustomerProfileFactory(user=user, is_active=True)
        seller = SellerProfileFactory(user=user, is_active=False)
        asset = SupplyFactory(owner=seller)
        client = authenticated_client(user)

        response = client.post(
            "/api/orders/cart/items/",
            {"asset_id": asset.pk, "quantity": 1},
            format="json",
        )

        assert response.status_code == 400
        assert "own seller assets" in str(response.data)

    def test_unavailable_asset_is_blocked(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(is_market_listed=False)
        client = authenticated_client(customer)

        response = client.post(
            "/api/orders/cart/items/",
            {"asset_id": asset.pk, "quantity": 1},
            format="json",
        )

        assert response.status_code == 400
        assert "listed" in str(response.data)


class TestCheckoutFlow:
    def test_empty_cart_checkout_is_blocked(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)
        client.get("/api/orders/cart/")

        response = client.post("/api/orders/checkout/", {}, format="json")

        assert response.status_code == 400
        assert "Cart is empty" in str(response.data)

    def test_successful_checkout_leaves_stock_and_converts_cart(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=5, price=Decimal("11.00"))
        client = authenticated_client(customer)
        CartService.add_item(customer.active_profile, asset.pk, 2)

        response = client.post("/api/orders/checkout/", {}, format="json")

        assert response.status_code == 201
        assert response.data["status"] == OrderStatus.PENDING
        assert response.data["total_price"] == "22.00"
        assert (
            response.data["items"][0]["status"]
            == OrderItemStatus.PENDING_SELLER
        )
        asset.refresh_from_db()
        assert asset.count == 5
        assert Cart.objects.get(customer=customer.active_profile).status == (
            CartStatus.CONVERTED
        )

    def test_new_cart_is_created_lazily_after_checkout(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=2)
        client = authenticated_client(customer)
        cart = CartService.add_item(customer.active_profile, asset.pk, 1)
        client.post("/api/orders/checkout/", {}, format="json")

        response = client.get("/api/orders/cart/")

        assert response.status_code == 200
        assert response.data["id"] != cart.pk
        assert response.data["items"] == []

    def test_checkout_does_not_mark_last_stock_sold_or_unlisted(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=1)
        client = authenticated_client(customer)
        CartService.add_item(customer.active_profile, asset.pk, 1)

        response = client.post("/api/orders/checkout/", {}, format="json")

        assert response.status_code == 201
        asset.refresh_from_db()
        assert asset.count == 1
        assert asset.status == AssetStatus.AVAILABLE
        assert asset.is_market_listed is True

    def test_multi_seller_checkout_creates_per_item_sellers(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_one = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_two = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset_one = SupplyFactory(owner=seller_one.active_profile, count=3)
        asset_two = SupplyFactory(owner=seller_two.active_profile, count=3)
        client = authenticated_client(customer)
        CartService.add_item(customer.active_profile, asset_one.pk, 1)
        CartService.add_item(customer.active_profile, asset_two.pk, 2)

        response = client.post("/api/orders/checkout/", {}, format="json")

        assert response.status_code == 201
        assert sorted(response.data["sellers_involved"]) == sorted(
            [seller_one.active_profile.pk, seller_two.active_profile.pk]
        )
        assert len(response.data["items"]) == 2

    def test_multiple_checkouts_for_last_stock_can_stay_pending(
        self,
    ):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=1)
        first_client = authenticated_client(first_customer)
        second_client = authenticated_client(second_customer)
        CartService.add_item(first_customer.active_profile, asset.pk, 1)
        CartService.add_item(second_customer.active_profile, asset.pk, 1)

        first_response = first_client.post(
            "/api/orders/checkout/", {}, format="json"
        )
        second_response = second_client.post(
            "/api/orders/checkout/", {}, format="json"
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 201
        asset.refresh_from_db()
        assert asset.count == 1

    def test_checkout_uses_latest_db_price(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=2, price=Decimal("10.00"))
        client = authenticated_client(customer)
        CartService.add_item(customer.active_profile, asset.pk, 1)
        asset.price = Decimal("15.00")
        asset.save(update_fields=["price"])

        response = client.post("/api/orders/checkout/", {}, format="json")

        assert response.status_code == 201
        assert response.data["total_price"] == "15.00"
        assert response.data["items"][0]["unit_price"] == "15.00"

    def test_direct_order_create_leaves_stock_unchanged(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=4)
        client = authenticated_client(customer)

        response = client.post(
            "/api/orders/",
            {"items": [{"asset_id": asset.pk, "quantity": 2}]},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["status"] == OrderStatus.PENDING
        asset.refresh_from_db()
        assert asset.count == 4


class TestOrderServiceAndSellerDecisions:
    def test_direct_service_creates_multi_seller_pending_order(self):
        customer = CustomerProfileFactory(is_active=True)
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
        assert order.status == OrderStatus.PENDING
        assert order.total_price == Decimal("32.00")
        assert order.items.count() == 2

        item_one = order.items.get(asset_id=asset_one.pk)
        assert item_one.seller_id == seller_one.pk
        assert item_one.unit_price_snapshot == Decimal("12.50")
        assert item_one.total_price == Decimal("25.00")
        assert item_one.status == OrderItemStatus.PENDING_SELLER

        asset_one.refresh_from_db()
        asset_two.refresh_from_db()
        assert asset_one.count == 5
        assert asset_two.count == 3

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
        asset.refresh_from_db()

        assert asset.count == 5

        first_response = client.post(f"/api/orders/items/{item.pk}/approve/")
        second_response = client.post(f"/api/orders/items/{item.pk}/approve/")

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        item.refresh_from_db()
        asset.refresh_from_db()
        order.refresh_from_db()
        assert item.status == OrderItemStatus.APPROVED
        assert asset.count == 3
        assert order.status == OrderStatus.PROCESSING
        assert (
            OrderEvent.objects.filter(
                order=order,
                event_type=OrderEventType.SELLER_APPROVED,
            ).count()
            == 1
        )

    def test_seller_reject_does_not_change_stock_and_cancels_order(self):
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

    def test_seller_approval_marks_sold_out_asset_sold_and_unlisted(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile, count=1)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        item = order.items.get()
        client = authenticated_client(seller)

        response = client.post(f"/api/orders/items/{item.pk}/approve/")

        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.count == 0
        assert asset.status == AssetStatus.SOLD
        assert asset.is_market_listed is False

    def test_seller_approval_revalidates_stock_under_lock(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile, count=1)
        first_order = OrderService.create_order(
            first_customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        second_order = OrderService.create_order(
            second_customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        first_item = first_order.items.get()
        second_item = second_order.items.get()
        client = authenticated_client(seller)

        first_response = client.post(
            f"/api/orders/items/{first_item.pk}/approve/"
        )
        second_response = client.post(
            f"/api/orders/items/{second_item.pk}/approve/"
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 400
        assert "not available" in str(second_response.data)
        second_item.refresh_from_db()
        asset.refresh_from_db()
        assert second_item.status == OrderItemStatus.PENDING_SELLER
        assert asset.count == 0

    def test_partial_rejection_updates_parent_status(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset_one = SupplyFactory(owner=seller.active_profile, count=5)
        asset_two = SupplyFactory(owner=seller.active_profile, count=5)
        order = OrderService.create_order(
            customer.active_profile,
            [
                {"asset_id": asset_one.pk, "quantity": 1},
                {"asset_id": asset_two.pk, "quantity": 1},
            ],
        )
        rejected_item = order.items.get(asset_id=asset_one.pk)
        client = authenticated_client(seller)

        response = client.post(f"/api/orders/items/{rejected_item.pk}/reject/")

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.status == OrderStatus.PARTIAL_REJECTED

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
        assert item.status == OrderItemStatus.PENDING_SELLER
        assert asset.count == 5


class TestOrderReadEndpoints:
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
