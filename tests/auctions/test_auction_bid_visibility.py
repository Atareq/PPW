from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.auctions.models import (
    AuctionBidVisibility,
    AuctionItemStatus,
)
from apps.auctions.services import BiddingService, SettlementService
from apps.notifications.models import Notification, NotificationType
from apps.users.constants import ProfileType
from tests.factories import AuctionFactory, AuctionItemFactory, BidFactory, UserAccountFactory

pytestmark = pytest.mark.django_db


def auth_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def test_sealed_bid_does_not_require_increment_or_update_current_price(customer_user):
    auction = AuctionFactory(
        bid_visibility=AuctionBidVisibility.SEALED,
        min_bid_increment=Decimal("25.00"),
    )
    item = AuctionItemFactory(
        auction=auction,
        starting_price=Decimal("100.00"),
        current_price=Decimal("100.00"),
    )
    other_bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    BidFactory(item=item, bidder=other_bidder.active_profile, amount=Decimal("180.00"))

    bid = BiddingService.place_bid(
        item=item,
        bidder=customer_user.active_profile,
        amount=Decimal("120.00"),
    )

    assert bid.amount == Decimal("120.00")
    item.refresh_from_db()
    assert item.current_price == Decimal("100.00")


def test_sealed_item_detail_hides_competitive_data_from_buyer(customer_user):
    auction = AuctionFactory(bid_visibility=AuctionBidVisibility.SEALED)
    item = AuctionItemFactory(
        auction=auction,
        reserve_price=Decimal("250.00"),
        current_price=Decimal("175.00"),
    )
    BidFactory(item=item, amount=Decimal("175.00"))

    response = auth_client(customer_user).get(f"/api/auctions/items/{item.pk}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["current_price"] is None
    assert response.data["reserve_price"] is None
    assert response.data["bids"] == []
    assert response.data["current_highest_bidder"] is None


def test_sealed_bid_list_exposes_only_own_bids_to_customer(customer_user):
    auction = AuctionFactory(bid_visibility=AuctionBidVisibility.SEALED)
    item = AuctionItemFactory(auction=auction)
    own_bid = BidFactory(
        item=item,
        bidder=customer_user.active_profile,
        amount=Decimal("150.00"),
    )
    other_bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    BidFactory(item=item, bidder=other_bidder.active_profile, amount=Decimal("175.00"))

    response = auth_client(customer_user).get(f"/api/auctions/bids/?item_id={item.pk}")

    assert response.status_code == status.HTTP_200_OK
    assert [bid["id"] for bid in response.data["results"]] == [own_bid.pk]


def test_settlement_uses_earliest_bid_tie_break():
    auction = AuctionFactory(bid_visibility=AuctionBidVisibility.SEALED)
    item = AuctionItemFactory(auction=auction)
    later_bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    earlier_bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    later_bid = BidFactory(
        item=item,
        bidder=later_bidder.active_profile,
        amount=Decimal("200.00"),
    )
    earlier_bid = BidFactory(
        item=item,
        bidder=earlier_bidder.active_profile,
        amount=Decimal("200.00"),
    )
    earlier_bid.created = timezone.now() - timedelta(minutes=2)
    later_bid.created = timezone.now() - timedelta(minutes=1)
    earlier_bid.save(update_fields=["created"])
    later_bid.save(update_fields=["created"])

    SettlementService._settle_item(item)

    item.refresh_from_db()
    assert item.status == AuctionItemStatus.SOLD
    assert item.winner_id == earlier_bidder.active_profile.pk
    assert item.winner_bid_id == earlier_bid.pk
    assert item.current_price == Decimal("200.00")


def test_reserve_not_met_marks_unsold_and_notifies_bidders(
    customer_user,
    django_capture_on_commit_callbacks,
):
    auction = AuctionFactory(bid_visibility=AuctionBidVisibility.SEALED)
    item = AuctionItemFactory(
        auction=auction,
        reserve_price=Decimal("250.00"),
        current_price=Decimal("100.00"),
    )
    BidFactory(
        item=item,
        bidder=customer_user.active_profile,
        amount=Decimal("200.00"),
    )

    with django_capture_on_commit_callbacks(execute=True):
        SettlementService._settle_item(item)

    item.refresh_from_db()
    assert item.status == AuctionItemStatus.UNSOLD
    assert item.winner_id is None
    assert item.winner_bid_id is None
    assert item.current_price == Decimal("100.00")
    notification = Notification.objects.get(
        notification_type=NotificationType.AUCTION_RESERVE_NOT_MET,
    )
    assert notification.recipient_profile_id == customer_user.active_profile.pk
    assert "winning_amount" not in notification.metadata
