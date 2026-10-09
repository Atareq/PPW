"""BE-REW-07: every distinct bidder of an auction with enough bidders gets PP once."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.auctions.models import Bid
from apps.auctions.services import BiddingService
from apps.authentication.services import switch_user_profile
from apps.loyalty.constants import (
    BadgeType,
    RewardEventType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine, RewardResult
from apps.loyalty.services import PointService, RewardRuleService
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db

RULE = RewardRuleCode.ACTIVE_AUCTION_BIDDER


def make_auction(items=1, **kwargs):
    auction = AuctionFactory(**kwargs)
    return auction, [AuctionItemFactory(auction=auction) for _ in range(items)]


def bid(item, user):
    item.refresh_from_db()
    return BiddingService.place_bid(
        item,
        user.active_profile,
        item.current_price + item.auction.min_bid_increment,
    )


def customers(count):
    return [UserAccountFactory(with_profile=ProfileType.CUSTOMER) for _ in range(count)]


def rewards(auction=None):
    queryset = PointTransaction.objects.filter(
        source_type=RewardSourceType.ACTIVE_AUCTION_BIDDER
    )
    if auction is not None:
        queryset = queryset.filter(source_id=str(auction.pk))
    return queryset


def rewarded_users(auction):
    return set(rewards(auction).values_list("user_id", flat=True))


def set_rule(**changes):
    RewardRuleService.update_rule(RULE, updated_by=None, **changes)


# --- Threshold semantics ------------------------------------------------------


def test_fewer_than_threshold_distinct_bidders_earn_nothing():
    _, (item,) = make_auction()
    for user in customers(4):
        bid(item, user)

    assert not rewards().exists()


def test_fifth_distinct_bidder_rewards_all_five_with_default_points():
    auction, (item,) = make_auction()
    users = customers(5)
    for user in users:
        bid(item, user)

    assert rewarded_users(auction) == {user.pk for user in users}
    assert set(rewards(auction).values_list("points", flat=True)) == {50}
    for user in users:
        # 10 PP first-bid reward plus the 50 PP active auction reward.
        assert PointService.get_balance(user) == 10 + 50


def test_repeated_bids_by_the_same_users_do_not_reach_the_threshold():
    _, (item,) = make_auction()
    a, b, c, d = customers(4)
    for _ in range(15):
        bid(item, a)
    for _ in range(5):
        bid(item, b)
    bid(item, c)
    bid(item, d)

    assert Bid.objects.count() == 22
    assert not rewards().exists()


def test_bidders_after_the_threshold_are_each_rewarded_once():
    auction, (item,) = make_auction()
    first_five = customers(5)
    for user in first_five:
        bid(item, user)
    before = {user.pk: PointService.get_balance(user) for user in first_five}

    sixth, seventh, eighth = customers(3)
    bid(item, sixth)
    assert rewards(auction).count() == 6
    bid(item, seventh)
    bid(item, eighth)
    for user in first_five + [sixth]:
        bid(item, user)

    assert rewards(auction).count() == 8
    assert rewarded_users(auction) == {
        user.pk for user in first_five + [sixth, seventh, eighth]
    }
    for user in first_five:
        assert PointService.get_balance(user) == before[user.pk]


def test_threshold_counts_bidders_across_all_items_of_the_auction():
    auction, (first_item, second_item) = make_auction(items=2)
    users = customers(5)
    for user in users[:3]:
        bid(first_item, user)
    for user in users[3:]:
        bid(second_item, user)

    assert rewarded_users(auction) == {user.pk for user in users}


def test_same_user_is_rewarded_once_per_auction():
    auction_a, (item_a,) = make_auction()
    auction_b, (item_b,) = make_auction()
    users = customers(5)
    for user in users:
        bid(item_a, user)
        bid(item_b, user)

    assert rewards(auction_a).count() == 5
    assert rewards(auction_b).count() == 5
    assert rewards().filter(user=users[0]).count() == 2


def test_replayed_bid_event_creates_no_duplicate_reward():
    auction, (item,) = make_auction()
    last_bid = None
    for user in customers(5):
        last_bid = bid(item, user)

    for _ in range(3):
        RewardEngine.handle_event(
            RewardEventType.BID_PLACED,
            actor=last_bid.bidder,
            context={"bid": last_bid, "was_last_minute": False},
        )

    assert rewards(auction).count() == 5


# --- Configuration ------------------------------------------------------------


def test_configured_points_are_used():
    set_rule(points=75)
    auction, (item,) = make_auction()
    for user in customers(5):
        bid(item, user)

    assert set(rewards(auction).values_list("points", flat=True)) == {75}


def test_configured_threshold_is_used():
    set_rule(threshold=3)
    auction, (item,) = make_auction()
    a, b, c = customers(3)
    bid(item, a)
    bid(item, b)
    assert not rewards().exists()

    bid(item, c)

    assert rewarded_users(auction) == {a.pk, b.pk, c.pk}


def test_disabled_rule_awards_nothing():
    set_rule(enabled=False)
    _, (item,) = make_auction()
    for user in customers(7):
        bid(item, user)

    assert not rewards().exists()


def test_config_change_does_not_change_existing_rewards():
    auction, (item,) = make_auction()
    for user in customers(5):
        bid(item, user)
    set_rule(points=75, threshold=2)

    late = customers(1)[0]
    bid(item, late)

    assert set(rewards(auction).exclude(user=late).values_list("points", flat=True)) == {
        50
    }
    assert rewards(auction).get(user=late).points == 75


# --- Which bids count ---------------------------------------------------------


def test_deleted_bids_and_non_customer_bid_rows_do_not_count():
    auction, (item,) = make_auction()
    users = customers(4)
    for user in users:
        bid(item, user)
    Bid.objects.filter(bidder=users[0].active_profile).delete()
    # A seller-profile bid row (possible through buy-now) is not a customer bid.
    BidFactory(item=item, bidder=SellerProfileFactory(), amount=Decimal("1.00"))
    deleted_item = AuctionItemFactory(auction=auction)
    stranger = customers(1)[0]
    BidFactory(item=deleted_item, bidder=stranger.active_profile)
    deleted_item.delete()

    bid(item, customers(1)[0])

    assert not rewards().exists()


def test_rejected_bid_does_not_count_or_trigger():
    _, (item,) = make_auction()
    users = customers(4)
    for user in users:
        bid(item, user)
    late = customers(1)[0]

    with pytest.raises(ValidationError):
        BiddingService.place_bid(item, late.active_profile, Decimal("1.00"))

    assert not Bid.objects.filter(bidder=late.active_profile).exists()
    assert not rewards().exists()


# --- Ledger, wallet, badges ---------------------------------------------------


def test_reward_ledger_attribution():
    auction, (item,) = make_auction()
    users = customers(5)
    for user in users:
        bid(item, user)

    tx = rewards(auction).get(user=users[0])
    assert tx.profile_id == users[0].active_profile.pk
    assert tx.points == 50
    assert tx.reason == "Active auction bidder reward"
    assert tx.source_id == str(auction.pk)
    assert tx.idempotency_key == (
        f"reward:active_auction_bidder:auction:{auction.pk}:user:{users[0].pk}"
    )
    assert tx.metadata == {
        "reward_code": "active_auction_bidder",
        "auction_id": auction.pk,
        "threshold": 5,
    }


def test_reward_is_points_only():
    _, (item,) = make_auction()
    for user in customers(5):
        bid(item, user)

    assert set(BadgeAward.objects.values_list("badge_type", flat=True)) == {
        BadgeType.FIRST_BIDDER
    }


def test_reward_is_visible_from_the_shared_wallet_in_seller_mode():
    auction, (item,) = make_auction()
    users = customers(5)
    SellerProfileFactory(user=users[0], is_active=False)
    for user in users:
        bid(item, user)

    switched, _ = switch_user_profile(users[0], ProfileType.SELLER)

    assert switched is True
    assert PointService.get_balance(users[0]) == 10 + 50


# --- Concurrency-oriented -----------------------------------------------------


def test_concurrent_duplicate_insert_is_treated_as_already_paid(monkeypatch):
    auction, (item,) = make_auction()
    users = customers(5)
    for user in users:
        bid(item, user)
    balances = {user.pk: PointService.get_balance(user) for user in users}
    last_bid = Bid.objects.filter(bidder=users[-1].active_profile).latest("created")

    # Another transaction already paid everyone but this evaluation cannot see
    # it yet: the stale lookups make every insert hit the unique key.
    monkeypatch.setattr(RewardEngine, "_paid_keys", staticmethod(lambda keys: set()))
    monkeypatch.setattr(
        PointService,
        "_get_idempotent_transaction",
        staticmethod(lambda key: None),
    )
    result = RewardResult()
    RewardEngine._grant_active_auction_bidder_rewards(last_bid, result)

    assert result.points_awarded == []
    assert rewards(auction).count() == 5
    assert {user.pk: PointService.get_balance(user) for user in users} == balances


def test_repeated_evaluations_keep_one_transaction_per_auction_user():
    auction, (item,) = make_auction()
    users = customers(6)
    bids = [bid(item, user) for user in users]

    for placed in bids * 2:
        RewardEngine._grant_active_auction_bidder_rewards(placed, RewardResult())

    for user in users:
        assert rewards(auction).filter(user=user).count() == 1


# --- Existing bid rewards -----------------------------------------------------


def test_first_bid_and_last_minute_rewards_are_unchanged():
    auction, (item,) = make_auction(end_time=timezone.now() + timedelta(minutes=3))
    users = customers(5)
    for user in users:
        bid(item, user)
    bid(item, users[0])

    first_bid_rows = PointTransaction.objects.filter(
        metadata__reward_code=BadgeType.FIRST_BIDDER
    )
    last_minute_rows = PointTransaction.objects.filter(
        metadata__reward_code=BadgeType.LAST_MINUTE_FIGHTER
    )
    assert first_bid_rows.count() == 5
    assert set(first_bid_rows.values_list("points", flat=True)) == {10}
    assert last_minute_rows.count() == 6
    assert set(last_minute_rows.values_list("points", flat=True)) == {5}
    assert BadgeAward.objects.filter(badge_type=BadgeType.FIRST_BIDDER).count() == 5
    assert BadgeAward.objects.filter(
        badge_type=BadgeType.LAST_MINUTE_FIGHTER
    ).count() == 5
    assert rewards(auction).count() == 5
    assert PointService.get_balance(users[0]) == 10 + 5 + 5 + 50
