"""BE-REW-06: a badge never implies PP Coins; events grant points and badges explicitly."""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.feed.models import SellerFollow
from apps.loyalty.constants import BadgeType, RewardEventType, RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine, RewardResult
from apps.loyalty.services import PointService
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    CompletedDealFactory,
    SellerProfileFactory,
    SupplyFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {build_token_for_user(user).access_token}"
        )
    return client


def register_customer(username="badge-user", phone_number="+201001250001"):
    payload = customer_registration_payload(phone_number=phone_number)
    payload["username"] = username
    response = client_for().post("/api/auth/register/customer/", payload, format="json")
    assert response.status_code == status.HTTP_201_CREATED
    return UserAccount.objects.get(username=username)


def points_for(code):
    return PointTransaction.objects.filter(metadata__reward_code=str(code))


def fire(event, profile, **context):
    return RewardEngine.handle_event(
        event,
        actor=profile,
        profile=profile,
        context=context,
        run_async_for_aggregates=False,
    )


# --- Welcome badges are badge-only -------------------------------------------


def test_welcome_buyer_badge_is_badge_only():
    user = register_customer()
    customer = CustomerProfile.objects.get(user=user)

    assert BadgeAward.objects.filter(
        profile=customer, badge_type=BadgeType.WELCOME_BUYER
    ).count() == 1
    assert not points_for(BadgeType.WELCOME_BUYER).exists()
    assert list(PointTransaction.objects.values_list("source_type", "points")) == [
        (RewardSourceType.ONBOARDING, 100)
    ]


def test_welcome_seller_badge_is_badge_only():
    user = register_customer()
    response = client_for(user).post(
        "/api/users/mobile/sellers/",
        {"country": CountryOptions.EGYPT, "currency": CurrencyOptions.EGP},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    seller = SellerProfile.objects.get(user=user)

    assert BadgeAward.objects.filter(
        profile=seller, badge_type=BadgeType.WELCOME_SELLER
    ).count() == 1
    assert not points_for(BadgeType.WELCOME_SELLER).exists()
    assert PointService.get_balance(user) == 100


# --- Helpers grant exactly what the caller asks for ------------------------


def test_badge_only_grant_creates_no_point_transaction():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    result = RewardResult()

    RewardEngine._award_badge(
        user.active_profile,
        BadgeType.FIRST_BIDDER,
        "test_event",
        user.active_profile,
        result,
    )

    assert BadgeAward.objects.filter(badge_type=BadgeType.FIRST_BIDDER).count() == 1
    assert not PointTransaction.objects.exists()
    assert result.points_awarded == []


def test_manager_badge_award_creates_no_point_transaction():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    response = client_for(manager).post(
        "/api/loyalty/badges/",
        {"profile_id": customer.active_profile.pk, "badge_type": BadgeType.TRUSTED_BUYER},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert not PointTransaction.objects.exists()


def test_points_only_grant_creates_no_badge():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    result = RewardResult()

    RewardEngine._grant_points(
        user.active_profile,
        "TEST_POINTS",
        7,
        "test_event",
        user.active_profile,
        result,
    )

    assert PointService.get_balance(user) == 7
    assert not BadgeAward.objects.exists()
    assert result.badges_awarded == []


def test_points_only_event_reward_creates_no_badge():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER).active_profile
    fire(RewardEventType.AUCTION_PUBLISHED, seller, auction=AuctionFactory(owner=seller))
    badges_after_first = BadgeAward.objects.count()

    fire(RewardEventType.AUCTION_PUBLISHED, seller, auction=AuctionFactory(owner=seller))

    assert PointService.get_balance(seller.user) == 50 + 25 + 50
    assert BadgeAward.objects.count() == badges_after_first == 1


def test_points_and_badge_reward_creates_one_of_each_and_is_idempotent():
    bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile
    bid = BidFactory(item=AuctionItemFactory(), bidder=bidder)

    for _ in range(2):
        fire(RewardEventType.BID_PLACED, bidder, bid=bid, was_last_minute=False)

    assert points_for(BadgeType.FIRST_BIDDER).count() == 1
    assert points_for(BadgeType.FIRST_BIDDER).get().points == 10
    assert BadgeAward.objects.filter(badge_type=BadgeType.FIRST_BIDDER).count() == 1
    assert PointService.get_balance(bidder.user) == 10


def test_explicit_combined_helper_retry_does_not_duplicate():
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    profile = user.active_profile

    for _ in range(2):
        RewardEngine._grant_points_and_badge(
            profile,
            BadgeType.FIRST_AUCTION_PUBLISHED,
            25,
            "test_event",
            profile,
            RewardResult(),
        )

    assert PointTransaction.objects.count() == 1
    assert BadgeAward.objects.count() == 1
    assert PointService.get_balance(user) == 25


# --- Current payouts of badge-paired rewards --------------------------------


def test_current_badge_paired_payouts_are_unchanged():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile
    seller = UserAccountFactory(with_profile=ProfileType.SELLER).active_profile
    item = AuctionItemFactory(auction=AuctionFactory(owner=seller))

    fire(RewardEventType.MARKET_ITEM_LISTED, seller, asset=SupplyFactory(owner=seller))
    for _ in range(2):
        fire(
            RewardEventType.BID_PLACED,
            customer,
            bid=BidFactory(item=item, bidder=customer),
            was_last_minute=True,
        )
    fire(
        RewardEventType.SELLER_FOLLOWED,
        customer,
        follow=SellerFollow.objects.create(follower=customer, seller=seller),
    )
    CompletedDealFactory.create_batch(10, buyer_profile=customer, seller_profile=seller)
    RewardEngine.evaluate_trusted_buyer(customer.pk)
    RewardEngine.evaluate_trusted_seller(seller.pk)

    expected = {
        BadgeType.FIRST_MARKET_ITEM_LISTED: [25],
        BadgeType.FIRST_BIDDER: [10],
        BadgeType.LAST_MINUTE_FIGHTER: [5, 5],
        BadgeType.LOYAL_FOLLOWER: [5],
        BadgeType.TRUSTED_BUYER: [100],
        BadgeType.TRUSTED_SELLER: [150],
    }
    for code, amounts in expected.items():
        assert sorted(points_for(code).values_list("points", flat=True)) == amounts
        assert BadgeAward.objects.filter(badge_type=code).count() == 1


# --- Public badge catalog -----------------------------------------------------


def catalog():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = client_for(user).get("/api/loyalty/badges/")
    assert response.status_code == status.HTTP_200_OK
    return {item["badge_type"]: item for item in response.data}


def test_catalog_does_not_claim_badges_pay_points():
    badges = catalog()

    assert badges[BadgeType.WELCOME_BUYER]["criteria_value"] == {}
    assert badges[BadgeType.WELCOME_SELLER]["criteria_value"] == {}
    assert all("points" not in item["criteria_value"] for item in badges.values())
    assert set(badges[BadgeType.WELCOME_BUYER]) == {
        "badge_type",
        "name",
        "description",
        "eligible_profile_type",
        "criteria_type",
        "criteria_value",
        "icon_url",
        "is_active",
    }


def test_catalog_uses_neutral_deal_wording():
    badges = catalog()

    for item in badges.values():
        text = f"{item['criteria_type']} {item['description']}".lower()
        assert "payment" not in text
    assert badges[BadgeType.AUCTION_PURCHASE_CONFIRMED]["criteria_type"] == (
        "auction_deal_completed"
    )
    assert badges[BadgeType.FIRST_MARKET_SALE_CONFIRMED]["criteria_type"] == (
        "first_market_deal_completed"
    )
    assert badges[BadgeType.TRUSTED_SELLER]["criteria_value"] == {
        "threshold": 10,
        "valid_days": 90,
    }


def test_earned_badge_list_uses_cleaned_definitions():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    fire(RewardEventType.CUSTOMER_PROFILE_CREATED, user.active_profile)
    SellerProfileFactory(user=user, is_active=False)

    response = client_for(user).get("/api/loyalty/badges/my/")

    assert response.status_code == status.HTTP_200_OK
    welcome = next(
        item for item in response.data if item["badge_type"] == BadgeType.WELCOME_BUYER
    )
    assert welcome["criteria_value"] == {}
