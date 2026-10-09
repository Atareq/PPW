from datetime import timedelta
from decimal import Decimal
import tempfile

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.auctions.models import (
    Auction,
    AuctionItemStatus,
    AuctionShippingMethod,
    AuctionStatus,
    AuctionType,
)
from apps.auctions.services import (
    AuctionBusinessValidator,
    AuctionService,
    BiddingService,
)
from apps.authentication.services import build_token_for_user
from apps.deals.models import CompletedDeal
from apps.payments.models import PaymentRequest, PaymentRequestStatus
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile, SellerRoom
from tests.factories import (
    ActiveAssetSellerPackageFactory,
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    BirdFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db

PAYMENT_DETAILS_FIELDS = ("bank_account", "wallet", "transfer_phone")


def _make_client(user=None, lang="en") -> APIClient:
    client = APIClient()
    headers = {"HTTP_ACCEPT_LANGUAGE": lang}
    if user is not None:
        refresh = build_token_for_user(user)
        headers["HTTP_AUTHORIZATION"] = f"Bearer {refresh.access_token}"
    client.credentials(**headers)
    return client


def _concrete_seller(user) -> SellerProfile:
    profile = user.active_profile
    if isinstance(profile, SellerProfile):
        return profile
    return SellerProfile.objects.get(pk=profile.pk)


def _create_seller_with_package():
    seller_user = UserAccountFactory(
        phone_number="+201001112233",
        with_profile=ProfileType.SELLER,
    )
    seller_profile = _concrete_seller(seller_user)
    seller_profile.bank_account = "EG990001234567890"
    seller_profile.wallet = "VF-CASH-010999"
    seller_profile.transfer_phone = "+201099988877"
    seller_profile.save(update_fields=["bank_account", "wallet", "transfer_phone"])
    seller_package = ActiveAssetSellerPackageFactory(owner=seller_profile)
    bird = BirdFactory(
        owner=seller_profile,
        is_for_auction=True,
        is_market_listed=False,
    )
    return seller_user, seller_profile, seller_package, bird


def _auction_create_payload(*, bird_id: int, seller_package_id: int, **overrides) -> dict:
    now = timezone.now()
    payload = {
        "seller_package_id": seller_package_id,
        "title": "Championship Pigeon Auction",
        "description": "Verified pedigree bird",
        "auction_type": AuctionType.SINGLE,
        "start_time": (now + timedelta(hours=2)).isoformat(),
        "end_time": (now + timedelta(days=2)).isoformat(),
        "auto_extend_enabled": False,
        "auto_extend_minutes": 5,
        "buy_now_enabled": False,
        "deposit_required": False,
        "payment_deadline_days": None,
        "min_bid_increment": "10.00",
        "tags": "champion,pigeon",
        "items": [
            {
                "bird_id": bird_id,
                "starting_price": "100.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


class TestPerAuctionShippingOptionsAndEta:
    """Comprehensive tests for BE-SHIP-01 — Per-Auction Shipping Options & ETA."""

    def test_01_seller_creates_auction_with_personal_pickup_and_fixed_duration(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.PERSONAL_PICKUP,
                min_shipping_days=7,
                max_shipping_days=7,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["shipping_method"] == AuctionShippingMethod.PERSONAL_PICKUP
        assert response.data["shipping_method_display"] == "Personal Pickup"
        assert response.data["min_shipping_days"] == 7
        assert response.data["max_shipping_days"] == 7

    def test_02_seller_creates_auction_with_shipping_company_and_range_duration(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
                min_shipping_days=3,
                max_shipping_days=5,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["shipping_method"] == AuctionShippingMethod.SHIPPING_COMPANY
        assert response.data["shipping_method_display"] == "Shipping Company"
        assert response.data["min_shipping_days"] == 3
        assert response.data["max_shipping_days"] == 5

    def test_03_seller_creates_auction_with_international_shipping_and_range_duration(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.INTERNATIONAL_SHIPPING,
                min_shipping_days=5,
                max_shipping_days=10,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert (
            response.data["shipping_method"]
            == AuctionShippingMethod.INTERNATIONAL_SHIPPING
        )
        assert response.data["shipping_method_display"] == "International Shipping"
        assert response.data["min_shipping_days"] == 5
        assert response.data["max_shipping_days"] == 10

    def test_04_seller_creates_auction_with_hand_delivery_and_fixed_duration_convenience_field(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.HAND_DELIVERY,
                shipping_days=7,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["shipping_method"] == AuctionShippingMethod.HAND_DELIVERY
        assert response.data["shipping_method_display"] == "Hand Delivery"
        assert response.data["min_shipping_days"] == 7
        assert response.data["max_shipping_days"] == 7

    def test_05_invalid_shipping_method_rejected_with_400(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method="DRONE_DELIVERY",
                min_shipping_days=3,
                max_shipping_days=5,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "shipping_method" in response.data

    def test_06_min_shipping_days_zero_rejected_with_400(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
                min_shipping_days=0,
                max_shipping_days=5,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "min_shipping_days" in response.data

    def test_07_negative_min_shipping_days_rejected_with_400(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
                min_shipping_days=-3,
                max_shipping_days=5,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "min_shipping_days" in response.data

    def test_08_max_shipping_days_zero_rejected_with_400(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
                min_shipping_days=1,
                max_shipping_days=0,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "max_shipping_days" in response.data

    def test_09_negative_max_shipping_days_rejected_with_400(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
                min_shipping_days=1,
                max_shipping_days=-2,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "max_shipping_days" in response.data

    def test_10_min_shipping_days_greater_than_max_shipping_days_rejected_with_400(self):
        seller_user, _, seller_package, bird = _create_seller_with_package()
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
                min_shipping_days=7,
                max_shipping_days=3,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "max_shipping_days" in response.data

    def test_11_seller_updates_shipping_fields_when_auction_is_editable(self):
        seller_user, seller_profile, _, bird = _create_seller_with_package()
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(days=2),
            shipping_method=AuctionShippingMethod.PERSONAL_PICKUP,
            min_shipping_days=7,
            max_shipping_days=7,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        client = _make_client(seller_user)
        response = client.patch(
            f"/api/auctions/{auction.pk}/",
            {
                "shipping_method": AuctionShippingMethod.SHIPPING_COMPANY,
                "min_shipping_days": 3,
                "max_shipping_days": 5,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["shipping_method"] == AuctionShippingMethod.SHIPPING_COMPANY
        assert response.data["shipping_method_display"] == "Shipping Company"
        assert response.data["min_shipping_days"] == 3
        assert response.data["max_shipping_days"] == 5

        auction.refresh_from_db()
        assert auction.shipping_method == AuctionShippingMethod.SHIPPING_COMPANY
        assert auction.min_shipping_days == 3
        assert auction.max_shipping_days == 5

    def test_12_seller_cannot_update_shipping_fields_when_auction_not_editable(self):
        seller_user, seller_profile, _, bird = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()

        # Case A: Auction has already started
        started_auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now - timedelta(minutes=10),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.PERSONAL_PICKUP,
            min_shipping_days=7,
            max_shipping_days=7,
        )
        AuctionItemFactory(
            auction=started_auction,
            bird=bird,
            status=AuctionItemStatus.ACTIVE,
        )

        client = _make_client(seller_user)
        resp_started = client.patch(
            f"/api/auctions/{started_auction.pk}/",
            {
                "shipping_method": AuctionShippingMethod.HAND_DELIVERY,
                "min_shipping_days": 2,
                "max_shipping_days": 4,
            },
            format="json",
        )
        assert resp_started.status_code == status.HTTP_400_BAD_REQUEST
        started_auction.refresh_from_db()
        assert (
            started_auction.shipping_method
            == AuctionShippingMethod.PERSONAL_PICKUP
        )

        # Case B: Future start_time but bids already exist
        bird_with_bid = BirdFactory(owner=seller_profile)
        bidded_auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(days=2),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        bidded_item = AuctionItemFactory(
            auction=bidded_auction,
            bird=bird_with_bid,
            status=AuctionItemStatus.ACTIVE,
        )
        BidFactory(
            item=bidded_item,
            bidder=customer_user.active_profile,
            amount=Decimal("150.00"),
        )

        resp_bidded = client.patch(
            f"/api/auctions/{bidded_auction.pk}/",
            {"shipping_method": AuctionShippingMethod.HAND_DELIVERY},
            format="json",
        )
        assert resp_bidded.status_code == status.HTTP_400_BAD_REQUEST
        bidded_auction.refresh_from_db()
        assert (
            bidded_auction.shipping_method
            == AuctionShippingMethod.SHIPPING_COMPANY
        )

    def test_13_other_seller_cannot_update_auction_shipping_fields(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        other_seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(days=2),
            shipping_method=AuctionShippingMethod.PERSONAL_PICKUP,
            min_shipping_days=7,
            max_shipping_days=7,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        other_client = _make_client(other_seller_user)
        response = other_client.patch(
            f"/api/auctions/{auction.pk}/",
            {
                "shipping_method": AuctionShippingMethod.HAND_DELIVERY,
                "min_shipping_days": 2,
                "max_shipping_days": 3,
            },
            format="json",
        )

        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        auction.refresh_from_db()
        assert auction.shipping_method == AuctionShippingMethod.PERSONAL_PICKUP
        assert auction.min_shipping_days == 7
        assert auction.max_shipping_days == 7

    def test_14_customer_cannot_update_auction_shipping_fields(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(days=2),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        customer_client = _make_client(customer_user)
        response = customer_client.patch(
            f"/api/auctions/{auction.pk}/",
            {
                "shipping_method": AuctionShippingMethod.PERSONAL_PICKUP,
                "min_shipping_days": 1,
                "max_shipping_days": 1,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        auction.refresh_from_db()
        assert auction.shipping_method == AuctionShippingMethod.SHIPPING_COMPANY

    def test_15_auction_detail_response_includes_shipping_method_and_eta(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.INTERNATIONAL_SHIPPING,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        customer_client = _make_client(customer_user)
        response = customer_client.get(f"/api/auctions/{auction.pk}/")

        assert response.status_code == status.HTTP_200_OK
        assert (
            response.data["shipping_method"]
            == AuctionShippingMethod.INTERNATIONAL_SHIPPING
        )
        assert response.data["shipping_method_display"] == "International Shipping"
        assert response.data["min_shipping_days"] == 3
        assert response.data["max_shipping_days"] == 5

    def test_16_auction_list_response_includes_shipping_fields(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.HAND_DELIVERY,
            min_shipping_days=7,
            max_shipping_days=7,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        customer_client = _make_client(customer_user)
        response = customer_client.get("/api/auctions/")

        assert response.status_code == status.HTTP_200_OK
        results = (
            response.data["results"]
            if isinstance(response.data, dict) and "results" in response.data
            else response.data
        )
        row = next(item for item in results if item["id"] == auction.pk)
        assert row["shipping_method"] == AuctionShippingMethod.HAND_DELIVERY
        assert row["shipping_method_display"] == "Hand Delivery"
        assert row["min_shipping_days"] == 7
        assert row["max_shipping_days"] == 7

    def test_17_legacy_auction_without_shipping_values_serializes_safely(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            shipping_method=None,
            min_shipping_days=None,
            max_shipping_days=None,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        customer_client = _make_client(customer_user)
        detail_resp = customer_client.get(f"/api/auctions/{auction.pk}/")
        assert detail_resp.status_code == status.HTTP_200_OK
        assert detail_resp.data["shipping_method"] is None
        assert detail_resp.data["shipping_method_display"] is None
        assert detail_resp.data["min_shipping_days"] is None
        assert detail_resp.data["max_shipping_days"] is None

        list_resp = customer_client.get("/api/auctions/")
        assert list_resp.status_code == status.HTTP_200_OK
        results = (
            list_resp.data["results"]
            if isinstance(list_resp.data, dict) and "results" in list_resp.data
            else list_resp.data
        )
        row = next(item for item in results if item["id"] == auction.pk)
        assert row["shipping_method"] is None
        assert row["shipping_method_display"] is None
        assert row["min_shipping_days"] is None
        assert row["max_shipping_days"] is None

    def test_18_seller_payment_details_remain_hidden_on_auction_detail_and_list(self):
        seller_user, seller_profile, _, bird = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        customer_client = _make_client(customer_user)
        detail_resp = customer_client.get(f"/api/auctions/{auction.pk}/")
        list_resp = customer_client.get("/api/auctions/")

        for payload in (detail_resp.data, list_resp.data):
            serialized = str(payload)
            for field in PAYMENT_DETAILS_FIELDS:
                assert f"'{field}'" not in serialized
                assert f'"{field}"' not in serialized
            for secret in (
                seller_profile.bank_account,
                seller_profile.wallet,
                seller_profile.transfer_phone,
                str(seller_user.phone_number),
            ):
                assert secret not in serialized

    def test_19_bidding_still_works_on_auction_with_shipping_fields(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=5),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        item = AuctionItemFactory(
            auction=auction,
            bird=bird,
            status=AuctionItemStatus.ACTIVE,
            starting_price=Decimal("100.00"),
            current_price=Decimal("100.00"),
        )

        customer_client = _make_client(customer_user)
        response = customer_client.post(
            f"/api/auctions/items/{item.pk}/place_bid/",
            {"amount": "150.00"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        item.refresh_from_db()
        assert item.current_price == Decimal("150.00")

    def test_20_auction_close_and_winner_resolution_still_work_with_shipping_fields(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        winner_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now - timedelta(hours=3),
            end_time=now + timedelta(hours=1),
            shipping_method=AuctionShippingMethod.INTERNATIONAL_SHIPPING,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        item = AuctionItemFactory(
            auction=auction,
            bird=bird,
            status=AuctionItemStatus.ACTIVE,
            starting_price=Decimal("100.00"),
            current_price=Decimal("100.00"),
        )
        BiddingService.place_bid(
            item=item,
            bidder=winner_user.active_profile,
            amount=Decimal("200.00"),
        )

        closed_auction = AuctionService.close_auction(auction)
        assert closed_auction.status == AuctionStatus.CLOSED
        assert (
            closed_auction.shipping_method
            == AuctionShippingMethod.INTERNATIONAL_SHIPPING
        )
        assert closed_auction.min_shipping_days == 3
        assert closed_auction.max_shipping_days == 5
        item.refresh_from_db()
        assert item.status == AuctionItemStatus.SOLD
        assert item.winner_id == winner_user.active_profile.pk

    def test_21_payment_request_flow_still_works_on_auction_with_shipping_fields(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        winner_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now - timedelta(hours=3),
            end_time=now + timedelta(hours=1),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        item = AuctionItemFactory(
            auction=auction,
            bird=bird,
            status=AuctionItemStatus.ACTIVE,
            starting_price=Decimal("100.00"),
            current_price=Decimal("100.00"),
        )
        BiddingService.place_bid(
            item=item,
            bidder=winner_user.active_profile,
            amount=Decimal("180.00"),
        )
        AuctionService.close_auction(auction)
        item.refresh_from_db()

        payment_request = PaymentRequestService.create_for_auction_item(
            auction_item_id=item.pk,
            buyer_profile=winner_user.active_profile,
            buyer_note="Ready for shipping company dispatch",
        )
        assert payment_request.status == PaymentRequestStatus.PENDING
        assert PaymentRequest.objects.filter(pk=payment_request.pk).exists()

    def test_22_completed_deal_creation_still_works_on_auction_with_shipping_fields(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        winner_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now - timedelta(hours=3),
            end_time=now + timedelta(hours=1),
            shipping_method=AuctionShippingMethod.HAND_DELIVERY,
            min_shipping_days=7,
            max_shipping_days=7,
        )
        item = AuctionItemFactory(
            auction=auction,
            bird=bird,
            status=AuctionItemStatus.ACTIVE,
            starting_price=Decimal("100.00"),
            current_price=Decimal("100.00"),
        )
        BiddingService.place_bid(
            item=item,
            bidder=winner_user.active_profile,
            amount=Decimal("220.00"),
        )
        AuctionService.close_auction(auction)
        item.refresh_from_db()

        payment_request = PaymentRequestService.create_for_auction_item(
            auction_item_id=item.pk,
            buyer_profile=winner_user.active_profile,
            buyer_note="Ready for hand delivery",
        )
        approved_pr = PaymentRequestService.approve(
            payment_request_id=payment_request.pk,
            seller_profile=seller_profile,
        )
        assert approved_pr.status == PaymentRequestStatus.APPROVED
        assert CompletedDeal.objects.filter(
            source_id=item.pk,
            buyer_profile=winner_user.active_profile,
            seller_profile=seller_profile,
        ).exists()

    def test_23_seller_room_auctions_still_work_with_shipping_fields(self):
        seller_user, seller_profile, seller_package, bird = _create_seller_with_package()
        room = SellerRoom.objects.create(
            seller=seller_profile,
            name="Elite International Room",
            description="International shipping room",
        )
        client = _make_client(seller_user)

        response = client.post(
            "/api/auctions/",
            _auction_create_payload(
                bird_id=bird.pk,
                seller_package_id=seller_package.pk,
                room=room.pk,
                shipping_method=AuctionShippingMethod.INTERNATIONAL_SHIPPING,
                min_shipping_days=4,
                max_shipping_days=7,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["room"] == room.pk
        assert response.data["room_name"] == "Elite International Room"
        assert (
            response.data["shipping_method"]
            == AuctionShippingMethod.INTERNATIONAL_SHIPPING
        )
        assert response.data["min_shipping_days"] == 4
        assert response.data["max_shipping_days"] == 7

    def test_24_service_level_create_validation_works(self):
        _, seller_profile, seller_package, bird = _create_seller_with_package()
        now = timezone.now()

        invalid_auction = Auction(
            owner=seller_profile,
            title="Service Invalid Shipping",
            description="Desc",
            auction_type=AuctionType.SINGLE,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=10,
            max_shipping_days=3,
        )
        items_data = [{"bird_id": bird.pk, "starting_price": Decimal("100.00")}]

        validator = AuctionBusinessValidator(
            auction=invalid_auction,
            items_data=items_data,
        )
        with pytest.raises(ValidationError) as exc:
            validator.validate()
        assert "max_shipping_days" in exc.value.detail

        with pytest.raises(ValidationError):
            AuctionService.create_auction(
                owner=seller_profile,
                auction=invalid_auction,
                items_data=items_data,
                seller_package_id=seller_package.pk,
            )

    def test_25_service_level_update_validation_works(self):
        _, seller_profile, _, bird = _create_seller_with_package()
        now = timezone.now()
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.PERSONAL_PICKUP,
            min_shipping_days=3,
            max_shipping_days=5,
        )
        AuctionItemFactory(auction=auction, bird=bird, status=AuctionItemStatus.ACTIVE)

        with pytest.raises(ValidationError) as exc:
            AuctionService.update_auction(
                auction,
                {
                    "min_shipping_days": 8,
                    "max_shipping_days": 4,
                },
            )
        assert "max_shipping_days" in exc.value.detail

        updated = AuctionService.update_auction(
            auction,
            {
                "shipping_method": AuctionShippingMethod.HAND_DELIVERY,
                "min_shipping_days": 7,
                "max_shipping_days": 7,
            },
        )
        assert updated.shipping_method == AuctionShippingMethod.HAND_DELIVERY
        assert updated.min_shipping_days == 7
        assert updated.max_shipping_days == 7

    def test_26_model_clean_and_database_constraint_validation_work(self):
        _, seller_profile, _, _ = _create_seller_with_package()
        now = timezone.now()
        auction = Auction(
            owner=seller_profile,
            title="Constraint Test Auction",
            description="Desc",
            auction_type=AuctionType.SINGLE,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=6,
            max_shipping_days=2,
        )
        with pytest.raises(DjangoValidationError) as exc:
            auction.clean()
        assert "max_shipping_days" in exc.value.message_dict

        with pytest.raises(IntegrityError), transaction.atomic():
            auction.save()

    def test_27_query_count_on_auction_list_and_detail_does_not_regress(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        _, seller_profile, _, _ = _create_seller_with_package()
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        now = timezone.now()

        legacy_bird = BirdFactory(owner=seller_profile)
        legacy_auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            shipping_method=None,
            min_shipping_days=None,
            max_shipping_days=None,
            title="Legacy No Shipping",
        )
        AuctionItemFactory(
            auction=legacy_auction,
            bird=legacy_bird,
            status=AuctionItemStatus.ACTIVE,
        )

        customer_client = _make_client(customer_user)
        customer_client.get("/api/auctions/")

        with CaptureQueriesContext(connection) as legacy_detail_queries:
            resp1 = customer_client.get(f"/api/auctions/{legacy_auction.pk}/")
        assert resp1.status_code == status.HTTP_200_OK

        shipping_bird = BirdFactory(owner=seller_profile)
        shipping_auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            shipping_method=AuctionShippingMethod.SHIPPING_COMPANY,
            min_shipping_days=3,
            max_shipping_days=5,
            title="Shipping Configured",
        )
        AuctionItemFactory(
            auction=shipping_auction,
            bird=shipping_bird,
            status=AuctionItemStatus.ACTIVE,
        )

        with CaptureQueriesContext(connection) as shipping_detail_queries:
            resp2 = customer_client.get(f"/api/auctions/{shipping_auction.pk}/")
        assert resp2.status_code == status.HTTP_200_OK
        assert len(shipping_detail_queries) == len(legacy_detail_queries)

    def test_28_makemigrations_check_dry_run_passes(self, monkeypatch):
        monkeypatch.setattr(settings, "MIGRATION_MODULES", {})
        call_command("makemigrations", "--check", "--dry-run", "--skip-checks", verbosity=0)

    def test_29_migration_apply_rollback_reapply_works(self):
        import subprocess
        import sys

        script = (
            "import os, tempfile, django\n"
            "from django.conf import settings\n"
            "from django.core.management import call_command\n"
            "os.environ['DJANGO_SETTINGS_MODULE'] = 'ppw.settings.test'\n"
            "django.setup()\n"
            "settings.MIGRATION_MODULES = {}\n"
            "with tempfile.NamedTemporaryFile(suffix='.sqlite3') as tmp:\n"
            "    settings.DATABASES['default']['NAME'] = tmp.name\n"
            "    call_command('migrate', verbosity=0)\n"
            "    call_command('migrate', 'auctions', '0005_auction_room_auction_auctions_au_room_id_d29905_idx', verbosity=0)\n"
            "    call_command('migrate', 'auctions', '0006_auction_max_shipping_days_auction_min_shipping_days_and_more', verbosity=0)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
