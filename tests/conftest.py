"""
pytest configuration and shared fixtures.

conftest.py is automatically discovered by pytest.
Put all shared fixtures here.
"""

from django.contrib.auth import get_user_model
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    BirdFactory,
    SellerProfileFactory,
    UserAccountFactory,
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
    config.addinivalue_line("markers", "slow: mark test as slow running")
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


@pytest.fixture
def authenticated_customer_client(customer_user):
    """Return authenticated API client with user token."""
    client = APIClient()
    refresh = RefreshToken.for_user(customer_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def authenticated_seller_client(seller_user):
    """Return authenticated API client with user token."""
    client = APIClient()
    refresh = RefreshToken.for_user(seller_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def authenticated_seller_client_2(seller_user_2):
    """Return authenticated API client with user token."""
    client = APIClient()
    refresh = RefreshToken.for_user(seller_user_2)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


# ============================================================================
# USER & PROFILE FIXTURES
# ============================================================================


@pytest.fixture
def user():
    """Create a test user with active profile."""
    return UserAccountFactory()


@pytest.fixture
def seller_user():
    """Create a seller user."""
    return UserAccountFactory(
        with_profile=ProfileType.SELLER,
    )


@pytest.fixture
def seller_user_2():
    """Create a seller user."""
    return UserAccountFactory(
        with_profile=ProfileType.SELLER,
    )


@pytest.fixture
def customer_user():
    """Create a customer user."""
    return UserAccountFactory(with_profile=ProfileType.CUSTOMER)


@pytest.fixture
def customer_user_2():
    return UserAccountFactory(with_profile=ProfileType.CUSTOMER)


@pytest.fixture
def manager_user():
    """Create a manager user."""
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    user.is_staff = True
    user.is_superuser = False
    user.save()
    return user


@pytest.fixture
def profile(user):
    """Get the active profile of a user."""
    return user.active_profile


@pytest.fixture
def seller_profile():
    """Create a seller profile."""
    return SellerProfileFactory(type=ProfileType.SELLER)


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
def bid(seller_profile, customer_user, auction_with_item):
    """Create a bid on item."""
    auction, item = auction_with_item
    return BidFactory(
        item=item, bidder=customer_user.active_profile, amount="150.00"
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
