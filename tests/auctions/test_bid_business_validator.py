"""
Tests for BidBusinessValidator.

Tests:
- Bid amount validation
- Seller can't bid on own auction
- Auction status validation
- Minimum bid increment enforcement
- Duplicate bid prevention
"""

import pytest
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta
from rest_framework.exceptions import ValidationError

from apps.auctions.services import BidBusinessValidator
from tests.factories import (
    create_active_auction_with_item,
    UserAccountFactory,
    SellerProfileFactory,
    AuctionFactory,
    AuctionItemFactory,
    BirdFactory,
)


@pytest.mark.django_db
class TestBidBusinessValidator:
    """Tests for BidBusinessValidator."""

    def test_valid_bid_passes_validation(self):
        """Test that valid bid passes all validations."""
        auction, item, seller = create_active_auction_with_item()
        buyer = UserAccountFactory(with_profile="seller")
        bidder = buyer.active_profile

        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=Decimal("150.00"),
        )

        # Should not raise any exception
        validator.validate()

    def test_seller_cannot_bid_on_own_auction(self):
        """Test that auction owner can't bid on own items."""
        auction, item, seller = create_active_auction_with_item()
        seller_profile = seller.active_profile

        validator = BidBusinessValidator(
            item=item,
            bidder=seller_profile,
            amount=Decimal("150.00"),
        )

        with pytest.raises(ValidationError) as exc:
            validator.validate()
        assert "seller" in str(exc.value).lower()

    def test_bid_amount_must_exceed_current_price(self):
        """Test that bid must exceed current highest bid."""
        auction, item, seller = create_active_auction_with_item()
        item.current_highest_bid = Decimal("200.00")
        item.save()

        buyer = UserAccountFactory(with_profile="seller")
        bidder = buyer.active_profile

        # Bid less than current highest
        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=Decimal("150.00"),
        )

        with pytest.raises(ValidationError):
            validator.validate()

    def test_bid_must_meet_minimum_increment(self):
        """Test that bid respects minimum increment."""
        auction, item, seller = create_active_auction_with_item()
        item.current_highest_bid = Decimal("100.00")
        item.save()
        # Set min increment to 10
        auction.min_bid_increment = Decimal("10.00")
        auction.save()

        buyer = UserAccountFactory(with_profile="seller")
        bidder = buyer.active_profile

        # Bid only 105 (increment is only 5, needs 10)
        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=Decimal("105.00"),
        )

        with pytest.raises(ValidationError):
            validator.validate()

    def test_auction_must_be_active(self):
        """Test that auction must be active."""
        seller_profile = SellerProfileFactory(type="seller")
        seller_user = seller_profile.user
        bird = BirdFactory(owner=seller_profile)

        # Create ended auction
        auction = AuctionFactory(
            owner=seller_profile,
            status="ended",
            end_time=timezone.now() - timedelta(days=1),
        )
        item = AuctionItemFactory(auction=auction, bird=bird, status="active")

        buyer = UserAccountFactory(with_profile="seller")
        bidder = buyer.active_profile

        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=Decimal("150.00"),
        )

        with pytest.raises(ValidationError) as exc:
            validator.validate()
        assert "ended" in str(exc.value).lower() or "active" in str(exc.value).lower()

    def test_item_must_be_active(self):
        """Test that item must be in active status."""
        auction, item, seller = create_active_auction_with_item()
        item.status = "sold"
        item.save()

        buyer = UserAccountFactory(with_profile="seller")
        bidder = buyer.active_profile

        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=Decimal("150.00"),
        )

        with pytest.raises(ValidationError):
            validator.validate()

    def test_auction_must_not_be_expired(self):
        """Test that auction cannot be expired."""
        seller_profile = SellerProfileFactory(type="seller")
        bird = BirdFactory(owner=seller_profile)

        # Create expired auction
        auction = AuctionFactory(
            owner=seller_profile,
            status="active",
            end_time=timezone.now() - timedelta(hours=1),  # Expired
        )
        item = AuctionItemFactory(auction=auction, bird=bird, status="active")

        buyer = UserAccountFactory(with_profile="seller")
        bidder = buyer.active_profile

        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=Decimal("150.00"),
        )

        with pytest.raises(ValidationError):
            validator.validate()

    def test_bid_amount_must_be_positive(self):
        """Test that bid amount must be positive."""
        auction, item, seller = create_active_auction_with_item()

        buyer = UserAccountFactory(with_profile="seller")
        bidder = buyer.active_profile

        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=Decimal("0.00"),
        )

        with pytest.raises(ValidationError):
            validator.validate()

    def test_multiple_sequential_bids_valid(self):
        """Test that increasing bids are valid."""
        auction, item, seller = create_active_auction_with_item()

        buyer1 = UserAccountFactory(with_profile="seller")
        buyer2 = UserAccountFactory(with_profile="seller")

        # First bid
        validator1 = BidBusinessValidator(
            item=item,
            bidder=buyer1.active_profile,
            amount=Decimal("150.00"),
        )
        validator1.validate()

        # Update item with new highest bid
        item.current_highest_bid = Decimal("150.00")
        item.save()

        # Second bid (higher)
        validator2 = BidBusinessValidator(
            item=item,
            bidder=buyer2.active_profile,
            amount=Decimal("160.00"),
        )
        validator2.validate()
