"""BE-REF-02: an approved referred account's first bid pays its inviter once."""

import importlib
from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.auctions.models import Bid
from apps.auctions.services import AuctionService, BiddingService, SettlementService
from apps.authentication.services import switch_user_profile
from apps.loyalty.constants import (
    FIRST_BID_POINTS,
    BadgeType,
    RewardEventType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine, RewardResult
from apps.loyalty.services import PointService, RewardRuleService
from apps.payments.services import PaymentRequestService
from apps.referrals.models import LinkSharingCode, Referral, ReferralStatus
from apps.referrals.services import ReferralService
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    ManagerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db

FIRST_BID_SOURCE = RewardSourceType.REFERRAL_FIRST_BID
backfill_module = importlib.import_module(
    "apps.referrals.migrations.0003_backfill_legacy_referrals"
)


def new_customer():
    return UserAccountFactory(with_profile=ProfileType.CUSTOMER)


def reviewer():
    user = UserAccountFactory(with_profile=False)
    ManagerProfileFactory(user=user, is_active=True)
    return user


def redeemed_referral(inviter=None, referred=None):
    inviter = inviter or new_customer()
    referred = referred or new_customer()
    code = ReferralService.create_code(inviter.active_profile)
    referral = ReferralService.redeem_code(referred.active_profile, code.code)
    return referral, inviter, referred


def approved_referral(inviter=None, referred=None):
    referral, inviter, referred = redeemed_referral(inviter, referred)
    ReferralService.approve(referral.pk, reviewer())
    referral.refresh_from_db()
    return referral, inviter, referred


def new_item(**auction_kwargs):
    return AuctionItemFactory(auction=AuctionFactory(**auction_kwargs))


def place_bid(user, item=None):
    item = item or new_item()
    item.refresh_from_db()
    return BiddingService.place_bid(
        item,
        user.active_profile,
        item.current_price + item.auction.min_bid_increment,
    )


def first_bid_rows(inviter=None):
    queryset = PointTransaction.objects.filter(source_type=FIRST_BID_SOURCE)
    if inviter is not None:
        queryset = queryset.filter(user=inviter)
    return queryset


def set_rule(**changes):
    RewardRuleService.update_rule(
        RewardRuleCode.REFERRAL_FIRST_BID, updated_by=None, **changes
    )


def replay(bid):
    return RewardEngine.handle_event(
        RewardEventType.BID_PLACED,
        actor=bid.bidder,
        context={"bid": bid},
    )


# --- Qualifying first bid -------------------------------------------------------


def test_approved_referral_first_bid_pays_inviter_default_points():
    _, inviter, referred = approved_referral()
    balance = PointService.get_balance(inviter)

    place_bid(referred)

    assert first_bid_rows(inviter).get().points == 200
    assert PointService.get_balance(inviter) == balance + 200


def test_referred_bidder_earns_no_referral_reward():
    _, _, referred = approved_referral()
    control = new_customer()

    place_bid(referred)
    place_bid(control)

    assert not first_bid_rows(referred).exists()
    assert PointService.get_balance(referred) == PointService.get_balance(control)


def test_pending_referral_first_bid_pays_nothing():
    _, inviter, referred = redeemed_referral()

    place_bid(referred)

    assert not first_bid_rows().exists()
    assert PointService.get_balance(inviter) == 0


def test_rejected_referral_first_bid_pays_nothing():
    referral, inviter, referred = redeemed_referral()
    ReferralService.reject(referral.pk, reviewer())

    place_bid(referred)

    assert not first_bid_rows().exists()
    assert PointService.get_balance(inviter) == 0


def test_bid_without_referral_pays_no_referral_reward():
    place_bid(new_customer())

    assert not first_bid_rows().exists()


def test_approval_before_first_bid_pays():
    referral, inviter, referred = approved_referral()

    bid = place_bid(referred)

    assert referral.reviewed_at <= bid.created
    assert first_bid_rows(inviter).count() == 1


# --- Account-level milestone ------------------------------------------------------


def test_second_bid_pays_nothing_more():
    _, inviter, referred = approved_referral()
    item = new_item()

    place_bid(referred, item)
    place_bid(referred, item)

    assert first_bid_rows(inviter).count() == 1


def test_first_bid_in_another_auction_is_not_a_new_milestone():
    _, inviter, referred = approved_referral()

    place_bid(referred, new_item())
    place_bid(referred, new_item())

    assert first_bid_rows(inviter).count() == 1


def test_first_bid_on_any_item_sets_the_account_milestone():
    _, inviter, referred = approved_referral()
    auction = AuctionFactory()
    first_item, second_item = (AuctionItemFactory(auction=auction) for _ in range(2))

    place_bid(referred, second_item)
    place_bid(referred, first_item)

    assert first_bid_rows(inviter).count() == 1


def test_historical_bid_blocks_the_milestone():
    _, inviter, referred = approved_referral()
    BidFactory(bidder=referred.active_profile)

    place_bid(referred)

    assert not first_bid_rows().exists()


def test_soft_deleted_rewarded_first_bid_cannot_be_farmed():
    _, inviter, referred = approved_referral()
    first = place_bid(referred)
    first.delete()
    assert Bid.all_objects.filter(pk=first.pk, deleted__isnull=False).exists()

    place_bid(referred)

    assert first_bid_rows(inviter).count() == 1


def test_soft_deleted_historical_bid_still_blocks_the_milestone():
    _, inviter, referred = approved_referral()
    BidFactory(bidder=referred.active_profile).delete()

    place_bid(referred)

    assert not first_bid_rows().exists()


def test_bid_from_another_mode_of_the_account_blocks_the_milestone():
    _, inviter, referred = approved_referral()
    seller_mode = SellerProfileFactory(user=referred, is_active=False)
    BidFactory(bidder=seller_mode)

    place_bid(referred)

    assert not first_bid_rows().exists()


def test_mode_switching_cannot_create_a_second_milestone():
    _, inviter, referred = approved_referral()
    SellerProfileFactory(user=referred, is_active=False)
    place_bid(referred)

    assert switch_user_profile(referred, ProfileType.SELLER)[0] is True
    assert switch_user_profile(referred, ProfileType.CUSTOMER)[0] is True
    place_bid(referred)

    assert first_bid_rows(inviter).count() == 1


def test_rejected_bid_attempt_does_not_use_the_milestone():
    _, inviter, referred = approved_referral()
    item = new_item()

    with pytest.raises(ValidationError):
        BiddingService.place_bid(item, referred.active_profile, Decimal("1.00"))
    assert not first_bid_rows().exists()

    place_bid(referred, item)

    assert first_bid_rows(inviter).count() == 1


# --- Buy-now ------------------------------------------------------------------------


def test_buy_now_neither_pays_nor_uses_the_milestone():
    _, inviter, referred = approved_referral()
    item = new_item(buy_now_enabled=True, buy_now_price=Decimal("500.00"))

    SettlementService.trigger_buy_now(item, referred.active_profile)

    assert not first_bid_rows().exists()

    place_bid(referred)

    assert first_bid_rows(inviter).count() == 1


# --- Approval timing ----------------------------------------------------------------


def test_approval_after_first_bid_is_not_retroactive():
    referral, inviter, referred = redeemed_referral()
    first = place_bid(referred)

    ReferralService.approve(referral.pk, reviewer())
    replay(first)
    place_bid(referred)

    assert not first_bid_rows().exists()
    approval_rows = PointTransaction.objects.filter(
        user=inviter, source_type=RewardSourceType.REFERRAL_APPROVED
    )
    assert approval_rows.get().points == 30


# --- Rule configuration -------------------------------------------------------------


def test_configured_points_are_used():
    set_rule(points=350)
    _, inviter, referred = approved_referral()

    place_bid(referred)

    assert first_bid_rows(inviter).get().points == 350


def test_disabled_rule_skips_and_is_not_topped_up_later():
    set_rule(enabled=False)
    _, inviter, referred = approved_referral()
    item = new_item()

    place_bid(referred, item)
    set_rule(enabled=True)
    place_bid(referred, item)

    assert not first_bid_rows().exists()


def test_rule_is_marked_implemented():
    rule = RewardRuleService.get_rule(RewardRuleCode.REFERRAL_FIRST_BID)

    assert rule.enabled is True
    assert rule.points == 200
    assert rule.is_implemented is True


# --- Idempotency and ledger ---------------------------------------------------------


def test_replayed_event_pays_once():
    _, inviter, referred = approved_referral()
    bid = place_bid(referred)

    for _ in range(3):
        replay(bid)

    assert first_bid_rows(inviter).count() == 1


def test_ledger_attribution_and_key():
    referral, inviter, referred = approved_referral()

    place_bid(referred)

    row = first_bid_rows(inviter).get()
    assert row.user_id == referral.inviter_user_id == inviter.pk
    assert row.profile_id == referral.inviter_profile_id
    assert row.source_id == str(referral.pk)
    assert row.reason == "Referral first bid reward"
    assert row.idempotency_key == (
        f"reward:referral_first_bid:referral:{referral.pk}:inviter"
    )
    assert row.metadata == {
        "reward_code": "referral_first_bid",
        "referral_id": referral.pk,
        "referred_user_id": referred.pk,
    }


def test_stale_concurrent_insert_keeps_one_row(monkeypatch):
    _, inviter, referred = approved_referral()
    bid = place_bid(referred)
    balance = PointService.get_balance(inviter)

    # Another transaction already paid but this evaluation cannot see it yet.
    monkeypatch.setattr(RewardEngine, "_paid_keys", staticmethod(lambda keys: set()))
    monkeypatch.setattr(
        PointService,
        "_get_idempotent_transaction",
        staticmethod(lambda key: None),
    )
    result = RewardResult()
    RewardEngine._grant_referral_first_bid_reward(bid, result)

    assert result.points_awarded == []
    assert first_bid_rows(inviter).count() == 1
    assert PointService.get_balance(inviter) == balance


def test_stale_paid_check_replays_the_same_payload(monkeypatch):
    _, inviter, referred = approved_referral()
    bid = place_bid(referred)

    # The paid check misses the committed row, so PointService finds it by key:
    # the payload must match so the bid is not rejected.
    monkeypatch.setattr(RewardEngine, "_paid_keys", staticmethod(lambda keys: set()))
    RewardEngine._grant_referral_first_bid_reward(bid, RewardResult())

    assert first_bid_rows(inviter).count() == 1


def test_separate_referrals_pay_their_own_inviters():
    referral_a, inviter_a, referred_a = approved_referral()
    referral_b, inviter_b, referred_b = approved_referral()
    item = new_item()

    place_bid(referred_a, item)
    place_bid(referred_b, item)

    assert first_bid_rows(inviter_a).get().source_id == str(referral_a.pk)
    assert first_bid_rows(inviter_b).get().source_id == str(referral_b.pk)


def test_reward_lands_in_the_shared_wallet_while_inviter_is_in_seller_mode():
    referral, inviter, referred = approved_referral()
    SellerProfileFactory(user=inviter, is_active=False)
    assert switch_user_profile(inviter, ProfileType.SELLER)[0] is True

    place_bid(referred)

    row = first_bid_rows(inviter).get()
    assert row.profile_id == referral.inviter_profile_id
    assert PointService.get_balance(inviter) == 30 + 200


# --- Independence from other bid rewards --------------------------------------------


def test_referred_bidder_keeps_the_first_bidder_reward():
    _, _, referred = approved_referral()

    place_bid(referred)

    assert BadgeAward.objects.filter(
        profile=referred.active_profile, badge_type=BadgeType.FIRST_BIDDER
    ).exists()
    assert PointService.get_balance(referred) == FIRST_BID_POINTS


def test_active_auction_bidder_reward_is_unchanged():
    _, inviter, referred = approved_referral()
    item = new_item()
    bidders = [referred] + [new_customer() for _ in range(4)]

    for user in bidders:
        place_bid(user, item)

    active_rows = PointTransaction.objects.filter(
        source_type=RewardSourceType.ACTIVE_AUCTION_BIDDER
    )
    assert set(active_rows.values_list("user_id", flat=True)) == {
        user.pk for user in bidders
    }
    assert first_bid_rows(inviter).count() == 1
    assert not active_rows.filter(user=inviter).exists()


def test_first_successful_auction_purchase_reward_stays_separate():
    _, inviter, referred = approved_referral()
    item = new_item()
    place_bid(referred, item)

    AuctionService.close_auction(item.auction)
    request = PaymentRequestService.create_for_auction_item(
        item.pk, referred.active_profile
    )
    PaymentRequestService.approve(request.pk, item.auction.owner)

    purchase_row = PointTransaction.objects.get(
        user=referred, source_type=RewardSourceType.FIRST_BID_COMPLETED
    )
    assert purchase_row.points == 200
    assert purchase_row.idempotency_key == (
        f"points:first_bid_completed:user:{referred.pk}"
    )
    assert first_bid_rows(inviter).count() == 1
    assert not first_bid_rows(referred).exists()


# --- Legacy and BE-REF-01 -----------------------------------------------------------


def _backfilled_referral(inviter, referred):
    code = LinkSharingCode.objects.create(
        owner_profile=inviter.active_profile,
        code=f"LEG{LinkSharingCode.objects.count()}",
        expires_at=timezone.now() - timedelta(days=29),
        is_used=True,
        used_by_profile=referred.active_profile,
        used_at=timezone.now() - timedelta(days=30),
    )
    backfill_module.backfill_legacy_referrals(django_apps, None)
    return Referral.objects.get(code=code)


def test_legacy_approved_referral_with_old_bids_pays_nothing():
    inviter, referred = new_customer(), new_customer()
    BidFactory(bidder=referred.active_profile)
    referral = _backfilled_referral(inviter, referred)
    assert referral.status == ReferralStatus.APPROVED

    place_bid(referred)

    assert not first_bid_rows().exists()


def test_legacy_approved_referral_without_bids_pays_on_first_new_bid():
    inviter, referred = new_customer(), new_customer()
    _backfilled_referral(inviter, referred)

    place_bid(referred)

    assert first_bid_rows(inviter).get().points == 200


def test_referral_lifecycle_keeps_approval_and_first_bid_rewards_separate():
    referral, inviter, referred = redeemed_referral()
    assert referral.status == ReferralStatus.PENDING
    assert PointService.get_balance(inviter) == 0

    ReferralService.approve(referral.pk, reviewer())
    assert PointService.get_balance(inviter) == 30

    place_bid(referred)

    sources = PointTransaction.objects.filter(user=inviter).values_list(
        "source_type", "points"
    )
    assert sorted(sources) == [
        (RewardSourceType.REFERRAL_APPROVED, 30),
        (FIRST_BID_SOURCE, 200),
    ]
    assert ReferralService.approved_count(referral.inviter_profile) == 1
