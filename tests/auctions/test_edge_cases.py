"""
Tests for edge cases and special scenarios.

Tests:
- Decimal precision handling
- Timezone handling
- Auto-extend behavior
- Concurrent bids
- Buy now functionality
"""

import pytest
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta

from tests.factories import (
    create_active_auction_with_item,
    UserAccountFactory,
    AuctionFactory,
    AuctionItemFactory,
    BirdFactory,
    SellerProfileFactory,
)


@pytest.mark.django_db
class TestDecimalPrecision:
    """Tests for decimal precision in amounts."""

    def test_bid_amount_two_decimal_places(self):
        """Test that bid amounts work with 2 decimal places."""
        from apps.auctions.models import Bid

        auction, item, seller = create_active_auction_with_item()
        bidder = UserAccountFactory(with_profile="seller")

        bid = Bid(
            item=item,
            bidder=bidder.active_profile,
            amount=Decimal("123.45"),
        )
        bid.save()

        assert bid.amount == Decimal("123.45")
        assert str(bid.amount) == "123.45"

    def test_bid_amount_rounding(self):
        """Test that bid amounts aren't incorrectly rounded."""
        from apps.auctions.models import Bid

        auction, item, seller = create_active_auction_with_item()
        bidder = UserAccountFactory(with_profile="seller")

        # Store exact decimal
        bid = Bid(
            item=item,
            bidder=bidder.active_profile,
            amount=Decimal("999.99"),
        )
        bid.save()

        bid.refresh_from_db()
        assert bid.amount == Decimal("999.99")

    def test_min_increment_precision(self):
        """Test that minimum increment calculation is precise."""
        auction, item, seller = create_active_auction_with_item()

        auction.min_bid_increment = Decimal("0.50")
        auction.save()

        item.current_highest_bid = Decimal("100.00")
        item.save()

        # Required minimum is 100.50
        assert item.current_highest_bid + auction.min_bid_increment == Decimal("100.50")


@pytest.mark.django_db
class TestTimezoneHandling:
    """Tests for timezone-aware operations."""

    def test_auction_end_time_is_timezone_aware(self):
        """Test that auction end times are timezone aware."""
        seller_profile = SellerProfileFactory(type="seller")

        auction = AuctionFactory(
            owner=seller_profile,
            end_time=timezone.now() + timedelta(days=7),
        )

        assert auction.end_time.tzinfo is not None
        assert timezone.is_aware(auction.end_time)

    def test_bid_created_timestamp_is_timezone_aware(self):
        """Test that bid creation timestamp is timezone aware."""
        from apps.auctions.models import Bid

        auction, item, seller = create_active_auction_with_item()
        bidder = UserAccountFactory(with_profile="seller")

        bid = Bid(
            item=item,
            bidder=bidder.active_profile,
            amount=Decimal("150.00"),
            created=timezone.now(),
        )
        bid.save()

        assert bid.created.tzinfo is not None
        assert timezone.is_aware(bid.created)

    def test_auction_not_expired_calculation(self):
        """Test that auction expiry is calculated correctly."""
        now = timezone.now()
        future = now + timedelta(days=7)

        seller_profile = SellerProfileFactory(type="seller")
        auction = AuctionFactory(
            owner=seller_profile,
            end_time=future,
        )

        assert auction.end_time > now


@pytest.mark.django_db
class TestAutoExtend:
    """Tests for auto-extend behavior."""

    def test_auto_extend_enabled_with_valid_minutes(self):
        """Test that auto_extend works with valid minutes."""
        seller_profile = SellerProfileFactory(type="seller")

        auction = AuctionFactory(
            owner=seller_profile,
            auto_extend_enabled=True,
            auto_extend_minutes=10,
        )

        assert auction.auto_extend_enabled is True
        assert auction.auto_extend_minutes == 10

    def test_auto_extend_disabled_by_default(self):
        """Test that auto_extend is disabled by default."""
        seller_profile = SellerProfileFactory(type="seller")

        auction = AuctionFactory(owner=seller_profile)

        assert auction.auto_extend_enabled is False

    def test_auto_extend_requires_positive_minutes(self):
        """Test that auto_extend_minutes must be positive."""
        seller_profile = SellerProfileFactory(type="seller")
        bird = BirdFactory(owner=seller_profile)

        from apps.auctions.services import AuctionBusinessValidator
        from apps.auctions.models import Auction

        auction = Auction(
            owner=seller_profile,
            title="Test",
            description="Test",
            auction_type="single",
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(days=7),
            auto_extend_enabled=True,
            auto_extend_minutes=0,  # Invalid
        )

        items_data = [{"bird_id": bird.id, "starting_price": "100.00"}]

        validator = AuctionBusinessValidator(auction=auction, items_data=items_data)

        with pytest.raises(Exception):
            validator.validate()


@pytest.mark.django_db
class TestConcurrentBids:
    """Tests for handling concurrent bids."""

    def test_multiple_bids_on_same_item(self):
        """Test that multiple bids can be placed on same item."""
        from apps.auctions.models import Bid

        auction, item, seller = create_active_auction_with_item()

        bidder1 = UserAccountFactory(with_profile="seller")
        bidder2 = UserAccountFactory(with_profile="seller")

        bid1 = Bid(
            item=item,
            bidder=bidder1.active_profile,
            amount=Decimal("150.00"),
        )
        bid1.save()

        bid2 = Bid(
            item=item,
            bidder=bidder2.active_profile,
            amount=Decimal("160.00"),
        )
        bid2.save()

        assert Bid.objects.filter(item=item).count() == 2

    def test_bid_order_by_amount(self):
        """Test that bids can be ordered by amount."""
        from apps.auctions.models import Bid

        auction, item, seller = create_active_auction_with_item()

        bidders = [UserAccountFactory(with_profile="seller") for _ in range(3)]
        amounts = [Decimal("150.00"), Decimal("160.00"), Decimal("155.00")]

        for bidder, amount in zip(bidders, amounts):
            bid = Bid(
                item=item,
                bidder=bidder.active_profile,
                amount=amount,
            )
            bid.save()

        bids = Bid.objects.filter(item=item).order_by("-amount")
        assert bids[0].amount == Decimal("160.00")
        assert bids[1].amount == Decimal("155.00")
        assert bids[2].amount == Decimal("150.00")


@pytest.mark.django_db
class TestBuyNow:
    """Tests for buy now functionality."""

    def test_buy_now_enabled_auction(self):
        """Test auction with buy now enabled."""
        seller_profile = SellerProfileFactory(type="seller")
        bird = BirdFactory(owner=seller_profile)

        auction = AuctionFactory(
            owner=seller_profile,
            buy_now_enabled=True,
            buy_now_price=Decimal("500.00"),
        )

        item = AuctionItemFactory(auction=auction, bird=bird)

        assert auction.buy_now_enabled is True
        assert auction.buy_now_price == Decimal("500.00")

    def test_buy_now_disabled_by_default(self):
        """Test that buy now is disabled by default."""
        auction, item, seller = create_active_auction_with_item()

        assert auction.buy_now_enabled is False
        assert auction.buy_now_price is None


@pytest.mark.django_db
class TestDepositAndPayment:
    """Tests for deposit and payment deadline."""

    def test_deposit_auction_requires_payment_deadline(self):
        """Test that deposit auctions require payment deadline."""
        seller_profile = SellerProfileFactory(type="seller")

        auction = AuctionFactory(
            owner=seller_profile,
            deposit_required=True,
            payment_deadline_days=7,
        )

        assert auction.deposit_required is True
        assert auction.payment_deadline_days == 7

    def test_no_deposit_by_default(self):
        """Test that deposits are not required by default."""
        auction, item, seller = create_active_auction_with_item()

        assert auction.deposit_required is False
        assert auction.payment_deadline_days is None
