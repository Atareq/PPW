"""
Tests for auction lifecycle.

Tests:
- Auction creation with items
- Bid placement on items
- Settlement (winner determination)
- Status transitions
"""

import pytest
from decimal import Decimal
from rest_framework import status
from django.utils import timezone

from tests.factories import (
    create_seller_with_bird,
    create_active_auction_with_item,
    UserAccountFactory,
)


@pytest.mark.django_db
class TestAuctionCreation:
    """Tests for auction creation endpoint."""

    def test_create_single_auction(self, authenticated_client, seller_profile):
        """Test creating a SINGLE auction with one item."""
        from tests.factories import BirdFactory

        bird = BirdFactory(owner=seller_profile)

        data = {
            "title": "Beautiful Pigeon",
            "description": "High quality bird",
            "auction_type": "single",
            "start_time": timezone.now().isoformat(),
            "end_time": (timezone.now().replace(day=10)).isoformat(),
            "items": [
                {
                    "bird_id": bird.id,
                    "starting_price": "100.00",
                }
            ],
        }

        response = authenticated_client.post("/api/auctions/", data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["title"] == "Beautiful Pigeon"
        assert response.data["auction_type"] == "single"

    def test_create_multi_auction(self, authenticated_client, seller_profile):
        """Test creating a MULTI auction with multiple items."""
        from tests.factories import BirdFactory

        birds = [BirdFactory(owner=seller_profile) for _ in range(3)]

        data = {
            "title": "Multiple Birds",
            "description": "Auction with multiple items",
            "auction_type": "multi",
            "start_time": timezone.now().isoformat(),
            "end_time": (timezone.now().replace(day=10)).isoformat(),
            "items": [
                {"bird_id": bird.id, "starting_price": "100.00"}
                for bird in birds
            ],
        }

        response = authenticated_client.post("/api/auctions/", data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert len(response.data["items"]) == 3

    def test_create_auction_without_profile_fails(self, api_client):
        """Test that creating auction without profile fails."""
        # Unauthenticated request
        data = {
            "title": "Auction",
            "auction_type": "single",
        }

        response = api_client.post("/api/auctions/", data, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_auction_invalid_type_fails(self, authenticated_client, seller_profile):
        """Test that invalid auction type fails."""
        from tests.factories import BirdFactory

        bird = BirdFactory(owner=seller_profile)

        data = {
            "title": "Invalid Auction",
            "auction_type": "invalid_type",
            "items": [{"bird_id": bird.id, "starting_price": "100.00"}],
        }

        response = authenticated_client.post("/api/auctions/", data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestBidPlacement:
    """Tests for placing bids on auction items."""

    def test_place_valid_bid(self, authenticated_client):
        """Test placing a valid bid on an item."""
        auction, item, seller = create_active_auction_with_item()
        
        # Create a different user for bidding
        bidder = UserAccountFactory(with_profile="seller")
        bidder_client = authenticated_client.__class__()
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(bidder)
        bidder_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        data = {"amount": "150.00"}

        response = bidder_client.post(
            f"/api/auctions/items/{item.id}/place_bid/",
            data,
            format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Decimal(response.data["amount"]) == Decimal("150.00")

    def test_bid_requires_authentication(self, api_client):
        """Test that bidding requires authentication."""
        auction, item, seller = create_active_auction_with_item()

        data = {"amount": "150.00"}

        response = api_client.post(
            f"/api/auctions/items/{item.id}/place_bid/",
            data,
            format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_seller_cannot_bid_on_own_item(self, authenticated_client, seller_profile):
        """Test that seller cannot bid on own item."""
        from tests.factories import BirdFactory, AuctionFactory, AuctionItemFactory

        bird = BirdFactory(owner=seller_profile)
        auction = AuctionFactory(owner=seller_profile, status="active")
        item = AuctionItemFactory(auction=auction, bird=bird, status="active")

        # Try to bid as the seller
        from rest_framework_simplejwt.tokens import RefreshToken
        from rest_framework.test import APIClient
        
        seller_client = APIClient()
        refresh = RefreshToken.for_user(seller_profile.user)
        seller_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        data = {"amount": "150.00"}

        response = seller_client.post(
            f"/api/auctions/items/{item.id}/place_bid/",
            data,
            format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bid_amount_validation(self, authenticated_client):
        """Test bid amount format validation."""
        auction, item, seller = create_active_auction_with_item()

        from rest_framework_simplejwt.tokens import RefreshToken
        from rest_framework.test import APIClient
        
        bidder = UserAccountFactory(with_profile="seller")
        bidder_client = APIClient()
        refresh = RefreshToken.for_user(bidder)
        bidder_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        # Invalid amount format
        data = {"amount": "not_a_number"}

        response = bidder_client.post(
            f"/api/auctions/items/{item.id}/place_bid/",
            data,
            format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bid_below_starting_price_fails(self, authenticated_client):
        """Test that bid below starting price fails."""
        auction, item, seller = create_active_auction_with_item()
        # Item starting price is 100.00

        from rest_framework_simplejwt.tokens import RefreshToken
        from rest_framework.test import APIClient
        
        bidder = UserAccountFactory(with_profile="seller")
        bidder_client = APIClient()
        refresh = RefreshToken.for_user(bidder)
        bidder_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        data = {"amount": "50.00"}  # Below starting price

        response = bidder_client.post(
            f"/api/auctions/items/{item.id}/place_bid/",
            data,
            format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestAuctionFiltering:
    """Tests for auction list filtering."""

    def test_get_active_auctions(self, api_client):
        """Test getting active auctions."""
        create_active_auction_with_item()

        response = api_client.get("/api/auctions/active/")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) > 0

    def test_get_my_auctions(self, authenticated_client, seller_profile):
        """Test getting user's own auctions."""
        from tests.factories import AuctionFactory

        auction = AuctionFactory(owner=seller_profile)

        response = authenticated_client.get("/api/auctions/my_auctions/")

        assert response.status_code == status.HTTP_200_OK

    def test_get_ending_soon(self, api_client):
        """Test getting auctions ending soon."""
        create_active_auction_with_item()

        response = api_client.get("/api/auctions/ending_soon/?minutes=100")

        assert response.status_code == status.HTTP_200_OK
