from decimal import Decimal

from django.db import IntegrityError, transaction
import pytest
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.test import APIClient

from apps.assets.models import AssetCategory, AssetStatus
from apps.authentication.services import build_token_for_user
from apps.auctions.models import AuctionItemStatus
from apps.orders.models import OrderItemStatus, OrderStatus
from apps.payments.models import (
    AuctionPaymentRequest,
    MarketPaymentRequest,
    PaymentRequest,
    PaymentRequestEvent,
    PaymentRequestEventType,
    PaymentRequestStatus,
    PaymentRequestType,
)
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BirdFactory,
    CustomerProfileFactory,
    OrderFactory,
    OrderItemFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def active_customer(user):
    return CustomerProfile.objects.get(pk=user.active_profile.pk)


def active_seller(user):
    return SellerProfile.objects.get(pk=user.active_profile.pk)


def create_sold_auction_item(
    *,
    seller=None,
    buyer=None,
    price=Decimal("150.00"),
    paired_bird=None,
):
    seller = seller or UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer = buyer or UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_profile = active_seller(seller)
    buyer_profile = active_customer(buyer)
    bird = BirdFactory(owner=seller_profile)
    auction = AuctionFactory(owner=seller_profile)
    item = AuctionItemFactory(
        auction=auction,
        bird=bird,
        paired_bird=paired_bird,
        status=AuctionItemStatus.SOLD,
        winner=buyer_profile,
        current_price=price,
    )
    return item, buyer, seller


def create_approved_order_item(
    *,
    buyer=None,
    seller=None,
    asset=None,
    quantity=2,
    unit_price=Decimal("25.00"),
):
    buyer = buyer or UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = seller or UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer_profile = active_customer(buyer)
    seller_profile = active_seller(seller)
    asset = asset or SupplyFactory(owner=seller_profile, count=5)
    order = OrderFactory(owner=buyer_profile, status=OrderStatus.PROCESSING)
    item = OrderItemFactory(
        order=order,
        asset=asset,
        seller=seller_profile,
        quantity=quantity,
        unit_price_snapshot=unit_price,
        status=OrderItemStatus.APPROVED,
    )
    return item, buyer, seller, asset


class TestPaymentRequestModels:
    def test_base_payment_request_can_store_shared_fields(self):
        buyer = CustomerProfileFactory()
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile)

        request = PaymentRequest.objects.create(
            buyer=buyer,
            seller=seller.active_profile,
            asset=asset,
            amount=Decimal("10.00"),
            status=PaymentRequestStatus.PENDING,
            request_type=PaymentRequestType.MARKET,
            buyer_note="paid manually",
        )

        assert request.pk
        assert request.buyer == buyer
        assert request.seller == seller.active_profile
        assert request.asset == asset

    def test_auction_payment_request_requires_auction_item(self):
        buyer = CustomerProfileFactory()
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = BirdFactory(owner=seller.active_profile)

        with pytest.raises(IntegrityError), transaction.atomic():
            AuctionPaymentRequest.objects.create(
                buyer=buyer,
                seller=seller.active_profile,
                asset=asset,
                amount=Decimal("10.00"),
            )

    def test_market_payment_request_requires_order_item(self):
        buyer = CustomerProfileFactory()
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile)

        with pytest.raises(IntegrityError), transaction.atomic():
            MarketPaymentRequest.objects.create(
                buyer=buyer,
                seller=seller.active_profile,
                asset=asset,
                amount=Decimal("10.00"),
            )

    def test_amount_cannot_be_negative(self):
        buyer = CustomerProfileFactory()
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=seller.active_profile)

        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentRequest.objects.create(
                buyer=buyer,
                seller=seller.active_profile,
                asset=asset,
                amount=Decimal("-1.00"),
                request_type=PaymentRequestType.MARKET,
            )

    def test_request_type_is_enforced_for_auction_subtype(self):
        item, buyer, seller = create_sold_auction_item()

        request = AuctionPaymentRequest.objects.create(
            buyer=buyer.active_profile,
            seller=seller.active_profile,
            asset=item.bird,
            amount=item.current_price,
            request_type=PaymentRequestType.MARKET,
            auction_item=item,
        )

        request.refresh_from_db()
        assert request.request_type == PaymentRequestType.AUCTION

    def test_request_type_is_enforced_for_market_subtype(self):
        item, buyer, seller, asset = create_approved_order_item()

        request = MarketPaymentRequest.objects.create(
            buyer=buyer.active_profile,
            seller=seller.active_profile,
            asset=asset,
            amount=item.total_price,
            request_type=PaymentRequestType.AUCTION,
            order_item=item,
        )

        request.refresh_from_db()
        assert request.request_type == PaymentRequestType.MARKET

    def test_unique_auction_item_for_auction_payment_request(self):
        item, buyer, seller = create_sold_auction_item()
        AuctionPaymentRequest.objects.create(
            buyer=buyer.active_profile,
            seller=seller.active_profile,
            asset=item.bird,
            amount=item.current_price,
            auction_item=item,
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            AuctionPaymentRequest.objects.create(
                buyer=buyer.active_profile,
                seller=seller.active_profile,
                asset=item.bird,
                amount=item.current_price,
                auction_item=item,
            )

    def test_unique_order_item_for_market_payment_request(self):
        item, buyer, seller, asset = create_approved_order_item()
        MarketPaymentRequest.objects.create(
            buyer=buyer.active_profile,
            seller=seller.active_profile,
            asset=asset,
            amount=item.total_price,
            order_item=item,
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            MarketPaymentRequest.objects.create(
                buyer=buyer.active_profile,
                seller=seller.active_profile,
                asset=asset,
                amount=item.total_price,
                order_item=item,
            )

    def test_approved_request_cannot_be_updated_through_service(self):
        item, buyer, seller, asset = create_approved_order_item()
        request = MarketPaymentRequest.objects.create(
            buyer=buyer.active_profile,
            seller=seller.active_profile,
            asset=asset,
            amount=item.total_price,
            order_item=item,
            status=PaymentRequestStatus.APPROVED,
        )

        with pytest.raises(ValidationError):
            PaymentRequestService.update_by_buyer(
                request.pk,
                buyer.active_profile,
                buyer_note="again",
            )


class TestAuctionPaymentRequests:
    def test_winner_can_create_request_for_sold_auction_item(self):
        item, buyer, seller = create_sold_auction_item(price=Decimal("175.00"))

        request = PaymentRequestService.create_for_auction_item(
            item.pk,
            buyer.active_profile,
            buyer_note="manual payment done",
        )

        assert request.request_type == PaymentRequestType.AUCTION
        assert request.status == PaymentRequestStatus.PENDING
        assert request.buyer_id == buyer.active_profile.pk
        assert request.seller_id == seller.active_profile.pk
        assert request.asset_id == item.bird_id
        assert request.amount == Decimal("175.00")
        assert request.auctionpaymentrequest.auction_item_id == item.pk

    def test_non_winner_cannot_create_request(self):
        item, _, _ = create_sold_auction_item()
        other_buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(ValidationError):
            PaymentRequestService.create_for_auction_item(
                item.pk,
                other_buyer.active_profile,
            )

    def test_unsold_auction_item_cannot_create_request(self):
        item, buyer, _ = create_sold_auction_item()
        item.status = AuctionItemStatus.UNSOLD
        item.save(update_fields=["status", "modified"])

        with pytest.raises(ValidationError):
            PaymentRequestService.create_for_auction_item(
                item.pk,
                buyer.active_profile,
            )

    def test_duplicate_pending_or_rejected_request_for_same_asset_is_blocked(
        self,
    ):
        item, buyer, _ = create_sold_auction_item()
        PaymentRequestService.create_for_auction_item(item.pk, buyer.active_profile)

        other_item = AuctionItemFactory(
            auction=item.auction,
            bird=item.bird,
            status=AuctionItemStatus.SOLD,
            winner=buyer.active_profile,
            current_price=Decimal("200.00"),
        )

        with pytest.raises(ValidationError):
            PaymentRequestService.create_for_auction_item(
                other_item.pk,
                buyer.active_profile,
            )


class TestMarketPaymentRequests:
    def test_order_owner_can_create_request_for_approved_order_item(self):
        item, buyer, seller, asset = create_approved_order_item(
            quantity=3,
            unit_price=Decimal("12.00"),
        )

        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
            buyer_note="sent cash",
        )

        assert request.request_type == PaymentRequestType.MARKET
        assert request.status == PaymentRequestStatus.PENDING
        assert request.buyer_id == buyer.active_profile.pk
        assert request.seller_id == seller.active_profile.pk
        assert request.asset_id == asset.pk
        assert request.amount == Decimal("36.00")
        assert request.marketpaymentrequest.order_item_id == item.pk

    def test_cannot_create_request_for_another_customers_order_item(self):
        item, _, _, _ = create_approved_order_item()
        other_buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(ValidationError):
            PaymentRequestService.create_for_order_item(
                item.pk,
                other_buyer.active_profile,
            )

    @pytest.mark.parametrize(
        "item_status",
        [
            OrderItemStatus.PENDING_SELLER,
            OrderItemStatus.REJECTED,
            OrderItemStatus.COMPLETED,
        ],
    )
    def test_cannot_create_request_unless_order_item_is_approved(
        self,
        item_status,
    ):
        item, buyer, _, _ = create_approved_order_item()
        item.status = item_status
        item.save(update_fields=["status", "modified"])

        with pytest.raises(ValidationError):
            PaymentRequestService.create_for_order_item(
                item.pk,
                buyer.active_profile,
            )


class TestPaymentRequestUpdates:
    def test_buyer_can_update_pending_request(self):
        item, buyer, _, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )

        updated = PaymentRequestService.update_by_buyer(
            request.pk,
            buyer.active_profile,
            buyer_note="new note",
        )

        assert updated.buyer_note == "new note"
        assert updated.status == PaymentRequestStatus.PENDING

    def test_buyer_can_resubmit_rejected_request(self):
        item, buyer, seller, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        rejected = PaymentRequestService.reject(
            request.pk,
            seller.active_profile,
            seller_note="not received",
        )

        updated = PaymentRequestService.update_by_buyer(
            rejected.pk,
            buyer.active_profile,
            buyer_note="sent again",
        )

        assert updated.status == PaymentRequestStatus.PENDING
        assert updated.buyer_note == "sent again"
        assert updated.rejected_at is None
        assert updated.rejected_by_id is None
        assert updated.seller_note == ""

    def test_unrelated_buyer_cannot_update_request(self):
        item, buyer, _, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        other_buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(NotFound):
            PaymentRequestService.update_by_buyer(
                request.pk,
                other_buyer.active_profile,
                buyer_note="mine",
            )


class TestPaymentRequestApproval:
    def test_seller_can_approve_own_pending_market_request(self):
        item, buyer, seller, asset = create_approved_order_item(quantity=2)
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )

        approved = PaymentRequestService.approve(
            request.pk,
            seller.active_profile,
        )

        item.refresh_from_db()
        asset.refresh_from_db()
        assert approved.status == PaymentRequestStatus.APPROVED
        assert approved.approved_by_id == seller.active_profile.pk
        assert approved.approved_at is not None
        assert item.status == OrderItemStatus.COMPLETED
        assert asset.count == 3

    def test_wrong_seller_cannot_approve_request(self):
        item, buyer, _, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        wrong_seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        with pytest.raises(NotFound):
            PaymentRequestService.approve(request.pk, wrong_seller.active_profile)

    def test_buyer_cannot_approve_request(self):
        item, buyer, _, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )

        with pytest.raises(ValidationError):
            PaymentRequestService.approve(request.pk, buyer.active_profile)

    def test_market_approval_transfers_bird_owner_to_buyer(self):
        buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        bird = BirdFactory(owner=seller.active_profile, is_market_listed=True)
        item, _, _, _ = create_approved_order_item(
            buyer=buyer,
            seller=seller,
            asset=bird,
            quantity=1,
            unit_price=Decimal("100.00"),
        )
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )

        PaymentRequestService.approve(request.pk, seller.active_profile)

        bird.refresh_from_db()
        assert bird.owner_id == buyer.active_profile.pk
        assert bird.count == 0
        assert bird.status == AssetStatus.SOLD
        assert bird.is_market_listed is False

    def test_auction_approval_transfers_primary_and_paired_birds(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        paired_bird = BirdFactory(owner=seller.active_profile)
        item, _, _ = create_sold_auction_item(
            seller=seller,
            buyer=buyer,
            paired_bird=paired_bird,
        )
        request = PaymentRequestService.create_for_auction_item(
            item.pk,
            buyer.active_profile,
        )

        PaymentRequestService.approve(request.pk, seller.active_profile)

        item.bird.refresh_from_db()
        paired_bird.refresh_from_db()
        assert item.bird.owner_id == buyer.active_profile.pk
        assert paired_bird.owner_id == buyer.active_profile.pk

    def test_market_approval_does_not_transfer_non_bird_owner(self):
        item, buyer, seller, asset = create_approved_order_item()
        assert asset.category != AssetCategory.BIRD
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )

        PaymentRequestService.approve(request.pk, seller.active_profile)

        asset.refresh_from_db()
        assert asset.owner_id == seller.active_profile.pk

    def test_market_approval_marks_zero_stock_sold_and_unlisted(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        item, buyer, seller, asset = create_approved_order_item(
            quantity=5,
            seller=seller,
            asset=SupplyFactory(owner=active_seller(seller), count=5),
        )
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )

        PaymentRequestService.approve(request.pk, seller.active_profile)

        asset.refresh_from_db()
        assert asset.count == 0
        assert asset.status == AssetStatus.SOLD
        assert asset.is_market_listed is False

    def test_market_approval_fails_when_stock_is_insufficient(self):
        item, buyer, seller, asset = create_approved_order_item(quantity=2)
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        asset.count = 1
        asset.save(update_fields=["count", "modified"])

        with pytest.raises(ValidationError):
            PaymentRequestService.approve(request.pk, seller.active_profile)

        item.refresh_from_db()
        asset.refresh_from_db()
        request.refresh_from_db()
        assert item.status == OrderItemStatus.APPROVED
        assert asset.count == 1
        assert request.status == PaymentRequestStatus.PENDING

    def test_second_approval_does_not_duplicate_stock_mutation(self):
        item, buyer, seller, asset = create_approved_order_item(quantity=2)
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        PaymentRequestService.approve(request.pk, seller.active_profile)

        with pytest.raises(ValidationError):
            PaymentRequestService.approve(request.pk, seller.active_profile)

        asset.refresh_from_db()
        assert asset.count == 3


class TestPaymentRequestRejection:
    def test_seller_can_reject_own_pending_request(self):
        item, buyer, seller, asset = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )

        rejected = PaymentRequestService.reject(
            request.pk,
            seller.active_profile,
            seller_note="not received",
        )

        item.refresh_from_db()
        asset.refresh_from_db()
        assert rejected.status == PaymentRequestStatus.REJECTED
        assert rejected.rejected_by_id == seller.active_profile.pk
        assert rejected.seller_note == "not received"
        assert item.status == OrderItemStatus.APPROVED
        assert asset.owner_id == seller.active_profile.pk

    def test_wrong_seller_cannot_reject_request(self):
        item, buyer, _, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        wrong_seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        with pytest.raises(NotFound):
            PaymentRequestService.reject(request.pk, wrong_seller.active_profile)


class TestPaymentRequestApi:
    def test_buyer_can_create_market_request_via_api(self):
        item, buyer, _, _ = create_approved_order_item()
        client = authenticated_client(buyer)

        response = client.post(
            "/api/payments/requests/order-item/",
            {"order_item_id": item.pk, "buyer_note": "done"},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["request_type"] == PaymentRequestType.MARKET
        assert response.data["order_item_id"] == item.pk

    def test_seller_can_approve_via_api(self):
        item, buyer, seller, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        client = authenticated_client(seller)

        response = client.post(f"/api/payments/requests/{request.pk}/approve/")

        assert response.status_code == 200
        assert response.data["status"] == PaymentRequestStatus.APPROVED

    def test_customer_list_returns_only_own_requests(self):
        item, buyer, _, _ = create_approved_order_item()
        own_request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        other_item, other_buyer, _, _ = create_approved_order_item()
        PaymentRequestService.create_for_order_item(
            other_item.pk,
            other_buyer.active_profile,
        )

        response = authenticated_client(buyer).get("/api/payments/requests/")

        assert response.status_code == 200
        ids = {row["id"] for row in response.data["results"]}
        assert ids == {own_request.pk}

    def test_seller_list_returns_only_seller_requests(self):
        item, buyer, seller, _ = create_approved_order_item()
        own_request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        other_item, other_buyer, _, _ = create_approved_order_item()
        PaymentRequestService.create_for_order_item(
            other_item.pk,
            other_buyer.active_profile,
        )

        response = authenticated_client(seller).get("/api/payments/requests/")

        assert response.status_code == 200
        ids = {row["id"] for row in response.data["results"]}
        assert ids == {own_request.pk}

    def test_manager_list_returns_all_requests(self):
        item, buyer, _, _ = create_approved_order_item()
        PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        other_item, other_buyer, _, _ = create_approved_order_item()
        PaymentRequestService.create_for_order_item(
            other_item.pk,
            other_buyer.active_profile,
        )
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

        response = authenticated_client(manager).get("/api/payments/requests/")

        assert response.status_code == 200
        assert response.data["count"] == 2

    def test_unauthenticated_access_is_denied(self):
        response = APIClient().get("/api/payments/requests/")

        assert response.status_code == 401


class TestPaymentRequestEvents:
    def test_created_updated_approved_and_rejected_events_are_recorded(self):
        item, buyer, seller, _ = create_approved_order_item()
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            buyer.active_profile,
        )
        PaymentRequestService.update_by_buyer(
            request.pk,
            buyer.active_profile,
            buyer_note="updated",
        )
        PaymentRequestService.reject(
            request.pk,
            seller.active_profile,
            seller_note="not received",
        )
        PaymentRequestService.update_by_buyer(
            request.pk,
            buyer.active_profile,
            buyer_note="resubmitted",
        )
        PaymentRequestService.approve(request.pk, seller.active_profile)

        event_types = list(
            PaymentRequestEvent.objects.filter(payment_request_id=request.pk)
            .order_by("created")
            .values_list("event_type", flat=True)
        )
        assert event_types == [
            PaymentRequestEventType.CREATED,
            PaymentRequestEventType.UPDATED,
            PaymentRequestEventType.REJECTED,
            PaymentRequestEventType.UPDATED,
            PaymentRequestEventType.APPROVED,
        ]
