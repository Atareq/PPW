from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest

from apps.auctions.services import BiddingService
from apps.loyalty.constants import BadgeType, RewardEventType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import BadgeService, PointService
from apps.payments.models import (
    PaymentRequest,
    PaymentRequestStatus,
    PaymentRequestType,
)
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BadgeAwardFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def test_welcome_buyer_reward_is_granted_once():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    RewardEngine.handle_event(
        RewardEventType.CUSTOMER_PROFILE_CREATED,
        actor=profile,
        profile=profile,
    )
    RewardEngine.handle_event(
        RewardEventType.CUSTOMER_PROFILE_CREATED,
        actor=profile,
        profile=profile,
    )

    assert BadgeAward.objects.filter(
        profile=profile,
        badge_type=BadgeType.WELCOME_BUYER,
    ).count() == 1
    assert PointService.get_balance(user) == 100


def test_welcome_seller_reward_is_granted_once():
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    profile = user.active_profile

    RewardEngine.handle_event(
        RewardEventType.SELLER_PROFILE_CREATED,
        actor=profile,
        profile=profile,
    )
    RewardEngine.handle_event(
        RewardEventType.SELLER_PROFILE_CREATED,
        actor=profile,
        profile=profile,
    )

    assert BadgeAward.objects.filter(
        profile=profile,
        badge_type=BadgeType.WELCOME_SELLER,
    ).count() == 1
    assert PointService.get_balance(user) == 100


def test_first_bid_reward_is_idempotent_if_event_fires_twice():
    bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    auction = AuctionFactory(
        start_time=timezone.now() - timedelta(minutes=10),
        end_time=timezone.now() + timedelta(hours=1),
    )
    item = AuctionItemFactory(auction=auction)

    bid = BiddingService.place_bid(
        item,
        bidder.active_profile,
        Decimal("151.00"),
    )
    RewardEngine.handle_event(
        RewardEventType.BID_PLACED,
        actor=bidder.active_profile,
        profile=bidder.active_profile,
        context={"bid": bid},
    )

    assert BadgeAward.objects.filter(
        profile=bidder.active_profile,
        badge_type=BadgeType.FIRST_BIDDER,
    ).count() == 1
    assert PointTransaction.objects.filter(
        profile=bidder.active_profile,
        reason=f"Reward: {BadgeType.FIRST_BIDDER}",
    ).count() == 1


def test_last_minute_fighter_badge_is_temporary_and_active():
    bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    auction = AuctionFactory(
        start_time=timezone.now() - timedelta(minutes=10),
        end_time=timezone.now() + timedelta(minutes=4),
    )
    item = AuctionItemFactory(auction=auction)

    BiddingService.place_bid(item, bidder.active_profile, Decimal("151.00"))

    award = BadgeAward.objects.get(
        profile=bidder.active_profile,
        badge_type=BadgeType.LAST_MINUTE_FIGHTER,
    )
    assert award.expires_at is not None
    assert timezone.now() + timedelta(days=6, hours=23) < award.expires_at
    assert award in list(BadgeService.get_active_badges(bidder.active_profile))


def test_last_minute_fighter_not_awarded_outside_final_window():
    bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    auction = AuctionFactory(
        start_time=timezone.now() - timedelta(minutes=10),
        end_time=timezone.now() + timedelta(minutes=30),
    )
    item = AuctionItemFactory(auction=auction)

    BiddingService.place_bid(item, bidder.active_profile, Decimal("151.00"))

    assert not BadgeAward.objects.filter(
        profile=bidder.active_profile,
        badge_type=BadgeType.LAST_MINUTE_FIGHTER,
    ).exists()


def test_expired_badge_is_not_returned_by_active_badges():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    expired = BadgeAwardFactory(
        profile=user.active_profile,
        badge_type=BadgeType.LAST_MINUTE_FIGHTER,
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    assert expired not in list(BadgeService.get_active_badges(user.active_profile))
    assert expired in list(
        BadgeService.get_earned_badges(
            user.active_profile,
            include_expired=True,
        )
    )


def test_deferred_or_unknown_rule_does_not_crash():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    result = RewardEngine.handle_event(
        "shipping_receipt_confirmed",
        actor=user.active_profile,
        profile=user.active_profile,
    )

    assert result == {
        "points_awarded": [],
        "badges_awarded": [],
        "already_awarded": [],
        "deferred_tasks": [],
    }


def test_trusted_buyer_aggregate_awards_threshold_badge():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    asset = SupplyFactory(owner=seller.active_profile)
    for _ in range(10):
        PaymentRequest.objects.create(
            buyer=buyer.active_profile,
            seller=seller.active_profile,
            asset=asset,
            amount=Decimal("10.00"),
            status=PaymentRequestStatus.APPROVED,
            request_type=PaymentRequestType.MARKET,
        )

    RewardEngine.evaluate_trusted_buyer(buyer.active_profile.pk)

    assert BadgeAward.objects.filter(
        profile=buyer.active_profile,
        badge_type=BadgeType.TRUSTED_BUYER,
    ).exists()


def test_trusted_seller_aggregate_awards_threshold_badge():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    asset = SupplyFactory(owner=seller.active_profile)
    for _ in range(10):
        buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PaymentRequest.objects.create(
            buyer=buyer.active_profile,
            seller=seller.active_profile,
            asset=asset,
            amount=Decimal("10.00"),
            status=PaymentRequestStatus.APPROVED,
            request_type=PaymentRequestType.MARKET,
        )

    RewardEngine.evaluate_trusted_seller(seller.active_profile.pk)

    assert BadgeAward.objects.filter(
        profile=seller.active_profile,
        badge_type=BadgeType.TRUSTED_SELLER,
    ).exists()
