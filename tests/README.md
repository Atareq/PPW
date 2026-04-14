# Testing Guide - PPW Auction Platform

## Overview

This directory contains comprehensive tests for the PPW auction platform, focusing on **Phase 1 & 2** coverage:

- **Phase 1**: Authentication app tests (registration, login, profiles, permissions)
- **Phase 2**: Auction app tests (validators, lifecycle, edge cases)

## Directory Structure

```
tests/
├── __init__.py                          # Package marker
├── conftest.py                          # Shared pytest fixtures
├── factories.py                         # Test data factories
├── authentication/                      # Auth app tests
│   ├── test_registration.py             # User registration tests
│   ├── test_login.py                    # Login & token tests
│   ├── test_profile_switching.py        # Profile switching tests
│   └── test_user_model.py               # User model & permissions tests
├── auctions/                            # Auction app tests
│   ├── test_bid_business_validator.py   # BidBusinessValidator tests
│   ├── test_auction_business_validator.py # AuctionBusinessValidator tests
│   ├── test_auction_lifecycle.py        # Auction creation & bidding tests
│   └── test_edge_cases.py               # Edge cases (decimal, timezone, etc)
└── utils/                               # Test utilities
    └── __init__.py
```

## Running Tests

### Install dependencies

```bash
pip install -r requirements/dev.txt
```

### Run all tests

```bash
pytest
```

### Run tests with coverage

```bash
pytest --cov=apps --cov-report=html
```

Coverage report will be in `htmlcov/index.html`

### Run specific test file

```bash
pytest tests/authentication/test_registration.py
```

### Run specific test class

```bash
pytest tests/authentication/test_registration.py::TestCustomerRegistration
```

### Run specific test

```bash
pytest tests/authentication/test_registration.py::TestCustomerRegistration::test_register_valid_customer
```

### Run tests matching pattern

```bash
pytest tests/ -k "bid" -v
```

### Run with verbose output

```bash
pytest -v
```

### Run tests in parallel (faster)

```bash
pip install pytest-xdist
pytest -n auto
```

## Coverage Goals

| Component | Target | Current |
|-----------|--------|---------|
| Authentication App | ≥ 90% | - |
| Auction Validators | 95%+ | - |
| Auction Views | ≥ 80% | - |
| Auction Services | ≥ 90% | - |

## Test Organization

### Authentication Tests (`tests/authentication/`)

**test_registration.py**
- Customer registration with valid data ✅
- Duplicate phone number rejection ✅
- Password mismatch validation ✅
- Missing fields validation ✅
- Invalid phone format ✅
- Automatic SELLER profile creation ✅
- Manager registration ✅

**test_login.py**
- Login with valid credentials ✅
- Invalid password rejection ✅
- Non-existent user rejection ✅
- Token contains user info ✅
- Login response includes profile ✅
- Token refresh functionality ✅

**test_profile_switching.py**
- Switch to valid profile ✅
- Reject non-existent profile ✅
- Prevent switching to other user's profile ✅
- Update active_profile on switch ✅
- Require authentication ✅

**test_user_model.py**
- User creation ✅
- Unique phone number constraint ✅
- active_profile property ✅
- role property ✅
- Permission management (add, remove, set) ✅
- Invalid permission rejection ✅

### Auction Tests (`tests/auctions/`)

**test_bid_business_validator.py**
- Valid bid passes validation ✅
- Seller cannot bid on own item ✅
- Bid must exceed current highest ✅
- Minimum increment enforcement ✅
- Auction must be active ✅
- Item must be active ✅
- Auction must not be expired ✅
- Bid amount must be positive ✅
- Sequential bid validation ✅

**test_auction_business_validator.py**
- SINGLE with 1 item ✅
- SINGLE with multiple items fails ✅
- MULTI with 2-10 items ✅
- MULTI with <2 items fails ✅
- MULTI with >10 items fails ✅
- Auto-extend configuration validation ✅
- Deposit requires payment deadline ✅
- End time after start time ✅

**test_auction_lifecycle.py**
- Create SINGLE auction ✅
- Create MULTI auction ✅
- Place valid bid ✅
- Bid requires authentication ✅
- Seller cannot bid on own item ✅
- Bid amount validation ✅
- Bid below starting price fails ✅
- Get active auctions ✅
- Get user's auctions ✅
- Get ending soon auctions ✅

**test_edge_cases.py**
- Decimal precision (2 places) ✅
- Amount rounding ✅
- Minimum increment precision ✅
- Timezone awareness ✅
- Auto-extend behavior ✅
- Concurrent bids ✅
- Bid ordering ✅
- Buy now functionality ✅
- Deposit & payment deadline ✅

## Using Factories

The `factories.py` file provides test data factories:

```python
from tests.factories import (
    UserAccountFactory,
    SellerProfileFactory,
    BirdFactory,
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    create_seller_with_bird,
    create_active_auction_with_item,
)

# Create a user with profile
user = UserAccountFactory(with_profile="seller")

# Create birds
bird1, bird2 = BirdFactory.create_batch(2)

# Create active auction ready for testing
auction, item, seller = create_active_auction_with_item()
```

## Using Fixtures

The `conftest.py` provides common pytest fixtures:

```python
def test_something(user, profile, authenticated_client, auth_headers):
    # user: UserAccount with active_profile
    # profile: Active Profile
    # authenticated_client: APIClient with auth token
    # auth_headers: Authorization headers dict
    pass
```

### Common Fixtures

| Fixture | Type | Description |
|---------|------|-------------|
| `api_client` | APIClient | Unauthenticated REST client |
| `authenticated_client` | APIClient | Authenticated REST client |
| `user` | UserAccount | Test user with active profile |
| `seller_user` | UserAccount | Seller user |
| `customer_user` | UserAccount | customer user |
| `manager_user` | UserAccount | Manager user (admin) |
| `profile` | Profile | Active profile |
| `bird` | Bird | Test bird |
| `paired_birds` | Tuple | Male & female birds |
| `auction` | Auction | Active auction |
| `auction_with_item` | Tuple | (Auction, AuctionItem) |
| `active_auction_with_item` | Tuple | Ready-to-bid auction |
| `bid` | Bid | Test bid |
| `user_token` | str | JWT access token |
| `auth_headers` | dict | Authorization headers |

## Test Markers

Use pytest markers for organization:

```bash
# Run only integration tests
pytest -m integration

# Run only slow tests
pytest -m slow

# Skip slow tests
pytest -m "not slow"
```

## Best Practices

1. **Use factories** instead of hardcoded fixtures
2. **Test one thing per test** - small, focused tests
3. **Use descriptive names** - `test_seller_cannot_bid_on_own_item`
4. **Use fixtures** for common setup
5. **Keep tests independent** - don't rely on test execution order
6. **Mock external dependencies** - not needed yet but good to know
7. **Test both happy and sad paths** - valid AND invalid scenarios

## Common Test Patterns

### Testing API endpoints

```python
@pytest.mark.django_db
def test_endpoint(authenticated_client, user):
    response = authenticated_client.post("/api/endpoint/", data)
    assert response.status_code == status.HTTP_201_CREATED
```

### Testing validators

```python
@pytest.mark.django_db
def test_validator():
    validator = BidBusinessValidator(item=item, bidder=bidder, amount=amount)
    
    # Should pass
    validator.validate()
    
    # Should fail
    with pytest.raises(ValidationError):
        validator.validate()
```

### Testing with fixtures

```python
@pytest.mark.django_db
def test_something(authenticated_client, user, bird):
    # User and bird are ready to use
    response = authenticated_client.post("/api/endpoint/", {"bird_id": bird.id})
    assert response.status_code == status.HTTP_201_CREATED
```

## Troubleshooting

### Import errors for pytest

Ensure `pytest.ini` is in project root and dependencies are installed:

```bash
pip install -r requirements/dev.txt
```

### Database not available

Tests use in-memory SQLite. If database error occurs, ensure `DJANGO_SETTINGS_MODULE` is set:

```bash
export DJANGO_SETTINGS_MODULE=ppw.settings.test
pytest
```

### Tests too slow

Run in parallel with pytest-xdist:

```bash
pip install pytest-xdist
pytest -n auto
```

### Coverage below target

Check coverage report:

```bash
pytest --cov=apps --cov-report=html
open htmlcov/index.html
```

## Next Steps (Phase 3)

When ready, add:
- Property-based tests (hypothesis)
- Load/stress tests
- E2E tests
- Performance benchmarks

## References

- [pytest Documentation](https://docs.pytest.org)
- [pytest-django Documentation](https://pytest-django.readthedocs.io)
- [factory_boy Documentation](https://factoryboy.readthedocs.io)
- [DRF Testing](https://www.django-rest-framework.org/api-guide/testing/)
