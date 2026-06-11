"""
Tests for AuctionBusinessValidator.

Tests:
- Auction type specific validation
- SINGLE has exactly 1 item
- PAIR has male + female
- MULTI has 2-10 items
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework.exceptions import ValidationError

from apps.auctions.models import Auction, AuctionType
from apps.auctions.serializers import AuctionCreateSerializer
from apps.auctions.services import AuctionBusinessValidator
from apps.users.models import ProfileType
from tests.factories import BirdFactory, SellerProfileFactory


@pytest.mark.django_db
class TestAuctionBusinessValidator:
    """Tests for AuctionBusinessValidator."""

    def test_single_auction_with_one_item_valid(self):
        """Test SINGLE auction with exactly 1 item passes validation."""
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        bird = BirdFactory(owner=seller_profile)

        auction = Auction(
            owner=seller_profile,
            title="Test Auction",
            description="Test",
            auction_type=AuctionType.SINGLE,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(days=7),
        )

        items_data = [
            {"bird_id": bird.id, "starting_price": Decimal("100.00")}
        ]

        validator = AuctionBusinessValidator(
            auction=auction, items_data=items_data
        )
        # Should not raise
        validator.validate()

    def test_single_auction_with_multiple_items_fails(self):
        """Test SINGLE auction with >1 item fails."""
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        bird1 = BirdFactory(owner=seller_profile)
        bird2 = BirdFactory(owner=seller_profile)

        auction = Auction(
            owner=seller_profile,
            title="Test Auction",
            description="Test",
            auction_type=AuctionType.SINGLE,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(days=7),
        )

        items_data = [
            {"bird_id": bird1.id, "starting_price": Decimal("100.00")},
            {"bird_id": bird2.id, "starting_price": Decimal("100.00")},
        ]

        validator = AuctionBusinessValidator(
            auction=auction, items_data=items_data
        )

        with pytest.raises(ValidationError):
            validator.validate()

    def test_multi_auction_with_valid_item_count(self):
        """Test MULTI auction with 2-10 items passes."""
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        birds = [BirdFactory(owner=seller_profile) for _ in range(5)]

        auction = Auction(
            owner=seller_profile,
            title="Test Auction",
            description="Test",
            auction_type=AuctionType.MULTI,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(days=7),
        )

        items_data = [
            {"bird_id": bird.id, "starting_price": Decimal("100.00")}
            for bird in birds
        ]

        validator = AuctionBusinessValidator(
            auction=auction, items_data=items_data
        )
        # Should not raise
        validator.validate()

    def test_multi_auction_with_too_few_items_fails(self):
        """Test MULTI auction with <2 items fails."""
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        bird = BirdFactory(owner=seller_profile)

        auction = Auction(
            owner=seller_profile,
            title="Test Auction",
            description="Test",
            auction_type=AuctionType.MULTI,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(days=7),
        )

        items_data = [
            {"bird_id": bird.id, "starting_price": Decimal("100.00")}
        ]

        validator = AuctionBusinessValidator(
            auction=auction, items_data=items_data
        )

        with pytest.raises(ValidationError):
            validator.validate()

    def test_multi_auction_with_too_many_items_fails(self):
        """Test MULTI auction with >10 items fails."""
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        birds = [BirdFactory(owner=seller_profile) for _ in range(15)]

        auction = Auction(
            owner=seller_profile,
            title="Test Auction",
            description="Test",
            auction_type=AuctionType.MULTI,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(days=7),
        )

        items_data = [
            {"bird_id": bird.id, "starting_price": Decimal("100.00")}
            for bird in birds
        ]

        validator = AuctionBusinessValidator(
            auction=auction, items_data=items_data
        )

        with pytest.raises(ValidationError):
            validator.validate()


class TestAuctionCreateSerializer:
    def _valid_payload(self):
        return {
            "title": "Test Auction",
            "description": "Test",
            "auction_type": AuctionType.SINGLE,
            "start_time": timezone.now() + timedelta(days=1),
            "end_time": timezone.now() + timedelta(days=2),
            "items": [
                {
                    "bird_id": 1,
                    "starting_price": Decimal("100.00"),
                }
            ],
        }

    def test_auto_extend_requires_minutes_value(self):
        """Test that auto_extend_enabled requires auto_extend_minutes."""
        payload = self._valid_payload()
        payload.update(
            {
                "auto_extend_enabled": True,
                "auto_extend_minutes": 0,
            }
        )

        serializer = AuctionCreateSerializer(data=payload)

        assert not serializer.is_valid()
        assert "auto_extend_minutes" in serializer.errors

    def test_deposit_requires_payment_deadline(self):
        """Test that deposit_required needs payment_deadline_days."""
        payload = self._valid_payload()
        payload.update(
            {
                "deposit_required": True,
                "payment_deadline_days": None,
            }
        )

        serializer = AuctionCreateSerializer(data=payload)

        assert not serializer.is_valid()
        assert "payment_deadline_days" in serializer.errors

    def test_pair_auction_requires_paired_bird_id(self):
        payload = self._valid_payload()
        payload["auction_type"] = AuctionType.PAIR

        serializer = AuctionCreateSerializer(data=payload)

        assert not serializer.is_valid()
        assert "items" in serializer.errors
