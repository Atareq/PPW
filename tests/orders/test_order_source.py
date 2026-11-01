from decimal import Decimal

from django.utils import translation
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.deals.models import CompletedDeal
from apps.orders.models import (
    Cart,
    CartStatus,
    Order,
    OrderItemStatus,
    OrderSource,
    OrderStatus,
)
from apps.orders.services import CartService, OrderService
from apps.payments.models import PaymentRequestStatus
from apps.users.constants import ProfileType
from tests.factories import OrderFactory, SupplyFactory, UserAccountFactory

pytestmark = pytest.mark.django_db

CHECKOUT_URL = "/api/orders/checkout/"


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    return client


@pytest.fixture(autouse=True)
def english_locale():
    with translation.override("en"):
        yield


def customer_with_cart(*assets):
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    for asset in assets or (SupplyFactory(count=5, price=Decimal("10.00")),):
        CartService.add_item(customer.active_profile, asset.pk, 1)
    return customer


@pytest.mark.parametrize("source", [OrderSource.MOBILE, OrderSource.WEB])
def test_checkout_records_the_client_source(source):
    customer = customer_with_cart()

    response = authenticated_client(customer).post(
        CHECKOUT_URL, {"source": source}, format="json"
    )

    assert response.status_code == 201
    assert response.data["source"] == source
    assert Order.objects.get().source == source


def test_checkout_without_source_records_unknown():
    customer = customer_with_cart()

    response = authenticated_client(customer).post(CHECKOUT_URL, {}, format="json")

    assert response.status_code == 201
    assert response.data["source"] == OrderSource.UNKNOWN
    assert Order.objects.get().source == OrderSource.UNKNOWN


@pytest.mark.parametrize("source", ["desktop", "", None, OrderSource.UNKNOWN])
def test_checkout_rejects_sources_clients_cannot_send(source):
    customer = customer_with_cart()

    response = authenticated_client(customer).post(
        CHECKOUT_URL, {"source": source}, format="json"
    )

    assert response.status_code == 400
    assert "source" in response.data
    assert not Order.objects.exists()
    assert Cart.objects.get(customer=customer.active_profile).status == (
        CartStatus.ACTIVE
    )


def test_direct_order_create_records_source_with_the_same_rules():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    asset = SupplyFactory(count=5)
    client = authenticated_client(customer)
    items = [{"asset_id": asset.pk, "quantity": 1}]

    web = client.post(
        "/api/orders/", {"items": items, "source": "web"}, format="json"
    )
    omitted = client.post("/api/orders/", {"items": items}, format="json")
    invalid = client.post(
        "/api/orders/", {"items": items, "source": "kiosk"}, format="json"
    )

    assert web.status_code == omitted.status_code == 201
    assert Order.objects.get(pk=web.data["id"]).source == OrderSource.WEB
    assert Order.objects.get(pk=omitted.data["id"]).source == OrderSource.UNKNOWN
    assert invalid.status_code == 400
    assert Order.objects.count() == 2


def test_internal_order_creation_defaults_to_unknown():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    asset = SupplyFactory(count=5)

    service_order = OrderService.create_order(
        customer.active_profile, [{"asset_id": asset.pk, "quantity": 1}]
    )

    assert service_order.source == OrderSource.UNKNOWN
    assert OrderFactory().source == OrderSource.UNKNOWN


def test_order_list_and_detail_return_source():
    customer = customer_with_cart()
    client = authenticated_client(customer)
    order_id = client.post(CHECKOUT_URL, {"source": "mobile"}, format="json").data[
        "id"
    ]

    listed = client.get("/api/orders/")
    detail = client.get(f"/api/orders/{order_id}/")

    assert listed.data["results"][0]["source"] == OrderSource.MOBILE
    assert detail.data["source"] == OrderSource.MOBILE


def test_customer_cannot_change_order_source():
    customer = customer_with_cart()
    client = authenticated_client(customer)
    order_id = client.post(CHECKOUT_URL, {"source": "mobile"}, format="json").data[
        "id"
    ]

    # No update action exists for orders; the permission layer rejects it
    # before method dispatch.
    for method in (client.patch, client.put):
        response = method(f"/api/orders/{order_id}/", {"source": "web"}, format="json")
        assert response.status_code in (403, 405)

    assert Order.objects.get(pk=order_id).source == OrderSource.MOBILE
    assert Order._meta.get_field("source").editable is False


def test_multi_seller_checkout_keeps_one_order_source():
    seller_one = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_two = UserAccountFactory(with_profile=ProfileType.SELLER)
    customer = customer_with_cart(
        SupplyFactory(owner=seller_one.active_profile, count=3),
        SupplyFactory(owner=seller_two.active_profile, count=3),
    )

    response = authenticated_client(customer).post(
        CHECKOUT_URL, {"source": "web"}, format="json"
    )

    assert response.status_code == 201
    assert len(response.data["sellers_involved"]) == 2
    assert Order.objects.get().source == OrderSource.WEB


def test_source_survives_approval_payment_and_completed_deal_unchanged():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    asset = SupplyFactory(
        owner=seller.active_profile, count=2, price=Decimal("40.00")
    )
    customer = customer_with_cart(asset)
    customer_client = authenticated_client(customer)
    seller_client = authenticated_client(seller)

    order_id = customer_client.post(
        CHECKOUT_URL, {"source": "web"}, format="json"
    ).data["id"]
    order = Order.objects.get(pk=order_id)
    item = order.items.get()
    approved_item = seller_client.post(f"/api/orders/items/{item.pk}/approve/")
    payment_request = customer_client.post(
        "/api/payments/requests/order-item/",
        {"order_item_id": item.pk},
        format="json",
    )
    approved_payment = seller_client.post(
        f"/api/payments/requests/{payment_request.data['id']}/approve/"
    )

    assert approved_item.status_code == 200
    assert payment_request.status_code == 201
    assert approved_payment.status_code == 200
    assert approved_payment.data["status"] == PaymentRequestStatus.APPROVED
    order.refresh_from_db()
    item.refresh_from_db()
    assert order.source == OrderSource.WEB
    assert order.status == OrderStatus.COMPLETED
    assert item.status == OrderItemStatus.COMPLETED
    deal = CompletedDeal.objects.get()
    assert deal.amount == item.total_price
    assert deal.seller_profile_id == seller.active_profile.pk
