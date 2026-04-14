"""
pytest configuration and shared fixtures.

conftest.py is automatically discovered by pytest.
Put all shared fixtures here.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from tests.factories import (
    SellerProfileFactory,
    UserAccountFactory,
    BirdFactory,
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    create_seller_with_bird,
    create_buyer,
    create_active_auction_with_item,
)

User = get_user_model()

# ============================================================================
# PYTEST MARKERS
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "django_db: mark test as requiring database access"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )


# ============================================================================
# CLIENT FIXTURES
# ============================================================================


@pytest.fixture
def api_client():
    """Return a REST API client."""
    return APIClient()


@pytest.fixture
def authenticated_client(user):
    """Return authenticated API client with user token."""
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


# ============================================================================
# USER & PROFILE FIXTURES
# ============================================================================


@pytest.fixture
def user():
    """Create a test user with active profile."""
    return UserAccountFactory(with_profile="seller")


@pytest.fixture
def seller_user():
    """Create a seller user."""
    return UserAccountFactory(with_profile="seller")


@pytest.fixture
def buyer_user():
    """Create a buyer user."""
    return UserAccountFactory(with_profile="seller")


@pytest.fixture
def manager_user():
    """Create a manager user."""
    user = UserAccountFactory(with_profile="manager")
    user.is_staff = True
    user.is_superuser = True
    user.save()
    return user


@pytest.fixture
def profile(user):
    """Get the active profile of a user."""
    return user.active_profile


@pytest.fixture
def seller_profile():
    """Create a seller profile."""
    return SellerProfileFactory(type="seller")


# ============================================================================
# BIRD FIXTURES
# ============================================================================


@pytest.fixture
def bird(seller_profile):
    """Create a bird owned by seller."""
    return BirdFactory(owner=seller_profile)


@pytest.fixture
def paired_birds(seller_profile):
    """Create a male and female bird for PAIR auctions."""
    male = BirdFactory(owner=seller_profile)
    female = BirdFactory(owner=seller_profile)
    return male, female


# ============================================================================
# AUCTION FIXTURES
# ============================================================================


@pytest.fixture
def auction(seller_profile):
    """Create an active auction."""
    return AuctionFactory(owner=seller_profile, status="active")


@pytest.fixture
def auction_with_item(auction, bird):
    """Create auction with single item."""
    item = AuctionItemFactory(auction=auction, bird=bird)
    return auction, item


@pytest.fixture
def active_auction_with_item():
    """Create an active auction ready for bidding."""
    return create_active_auction_with_item()


# ============================================================================
# BID FIXTURES
# ============================================================================


@pytest.fixture
def bid(seller_profile, buyer_user, auction_with_item):
    """Create a bid on item."""
    auction, item = auction_with_item
    return BidFactory(
        item=item,
        bidder=buyer_user.active_profile,
        amount="150.00"
    )


# ============================================================================
# UTILITY FIXTURES
# ============================================================================


@pytest.fixture
def user_token(user):
    """Get JWT token for user."""
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


@pytest.fixture
def auth_headers(user_token):
    """Get authorization headers with token."""
    return {"HTTP_AUTHORIZATION": f"Bearer {user_token}"}
