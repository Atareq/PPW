"""BE-REW-09: the seller earns SUCCESSFUL_SALE points for every completed deal."""

from decimal import Decimal

import pytest

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
from apps.payments.models import PaymentRequest
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from tests.factories import (
    CompletedDealFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)
from tests.payments.test_payment_requests import (
    active_seller,
    create_approved_order_item,
    create_sold_auction_item,
)

pytestmark = pytest.mark.django_db

SALE = RewardSourceType.SUCCESSFUL_SALE
DEAL = RewardSourceType.DEAL_COMPLETED
BONUS = RewardSourceType.DEAL_COMPLETION_BONUS


def complete_market(unit_price=Decimal("100.00"), buyer=None, seller=None):
    item, buyer, seller, _ = create_approved_order_item(
        buyer=buyer, seller=seller, quantity=1, unit_price=unit_price
    )
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    return item, buyer, seller


def complete_auction(price=Decimal("100.00"), buyer=None, seller=None):
    item, buyer, seller = create_sold_auction_item(buyer=buyer, seller=seller, price=price)
    request = PaymentRequestService.create_for_auction_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    return item, buyer, seller


def fire(deal):
    return RewardEngine.handle_event(
        RewardEventType.DEAL_COMPLETED,
        actor=deal.seller_profile,
        context={"deal": deal},
        run_async_for_aggregates=False,
    )


def sale_rows(user=None):
    queryset = PointTransaction.objects.filter(source_type=SALE)
    return queryset.filter(user=user) if user is not None else queryset


def points_by_source(user):
    rows = PointTransaction.objects.filter(user=user)
    return sorted(rows.values_list("source_type", "points"))


def set_rule(**changes):
    RewardRuleService.update_rule(RewardRuleCode.SUCCESSFUL_SALE, updated_by=None, **changes)


# --- Default rule ---------------------------------------------------------------


def test_default_rule():
    rule = RewardRuleService.get_rule(RewardRuleCode.SUCCESSFUL_SALE)

    assert rule.enabled is True
    assert rule.points == 100
    assert rule.required_fields == ("points",)
    assert rule.is_implemented is True


# --- Market, auction, seller only -----------------------------------------------


def test_market_sale_pays_the_seller():
    item, buyer, seller = complete_market()

    row = sale_rows(seller).get()
    assert row.points == 100
    assert row.idempotency_key == f"reward:successful_sale:order_item:{item.pk}:seller"
    assert not sale_rows(buyer).exists()


def test_auction_sale_pays_the_seller():
    item, buyer, seller = complete_auction()

    row = sale_rows(seller).get()
    assert row.points == 100
    assert row.idempotency_key == f"reward:successful_sale:auction_item:{item.pk}:seller"
    assert not sale_rows(buyer).exists()


def test_market_combined_outcome_for_a_100_egp_deal():
    _, buyer, seller = complete_market(Decimal("100.00"))

    assert points_by_source(buyer) == sorted(
        [
            ("PaymentRequest", 30),  # market purchase badge reward (unchanged)
            (DEAL, 200),
            (BONUS, 100),
        ]
    )
    assert points_by_source(seller) == sorted(
        [
            ("PaymentRequest", 50),  # first market sale badge reward (unchanged)
            (DEAL, 200),
            (BONUS, 100),
            (SALE, 100),
        ]
    )


def test_auction_combined_outcome_for_a_100_egp_deal():
    _, buyer, seller = complete_auction(Decimal("100.00"))

    assert points_by_source(buyer) == sorted(
        [
            ("PaymentRequest", 50),  # auction purchase badge reward (unchanged)
            (RewardSourceType.FIRST_BID_COMPLETED, 200),
            (DEAL, 200),
            (BONUS, 100),
        ]
    )
    assert points_by_source(seller) == sorted(
        [
            ("PaymentRequest", 75),  # first auction sale badge reward (unchanged)
            (DEAL, 200),
            (BONUS, 100),
            (SALE, 100),
        ]
    )


# --- Recurring and first-sale independence ----------------------------------------


def test_reward_recurs_for_every_sale():
    seller = SellerProfileFactory(is_active=True)
    deals = [CompletedDealFactory(seller_profile=seller) for _ in range(3)]
    for deal in deals:
        fire(deal)

    assert sale_rows(seller.user).count() == 3
    assert sum(sale_rows(seller.user).values_list("points", flat=True)) == 300
    assert len(set(sale_rows(seller.user).values_list("idempotency_key", flat=True))) == 3


def test_first_sale_incentive_is_separate_and_not_repeated():
    _, buyer, seller = complete_market()
    first_sale_rows = PointTransaction.objects.filter(
        user=seller, metadata__reward_code=BadgeType.FIRST_MARKET_SALE_CONFIRMED
    )
    assert first_sale_rows.count() == 1
    assert sale_rows(seller).count() == 1
    assert first_sale_rows.get().idempotency_key != sale_rows(seller).get().idempotency_key

    complete_market(buyer=buyer, seller=seller)

    assert first_sale_rows.count() == 1
    assert sale_rows(seller).count() == 2


# --- Idempotency and history --------------------------------------------------------


def test_replayed_event_pays_once():
    deal = CompletedDealFactory()

    for _ in range(3):
        fire(deal)

    assert sale_rows(deal.seller_user).count() == 1


def test_already_processed_historical_deal_is_not_topped_up():
    buyer = CustomerProfileFactory(is_active=True)
    seller = SellerProfileFactory(is_active=True)
    request_id = 951
    # The buyer's purchase-reward row is what marks a deal as already processed.
    PointService.earn_points(
        user=buyer.user,
        profile=buyer,
        points=30,
        reason=f"Reward: {BadgeType.MARKET_PURCHASE_CONFIRMED}",
        source_type="PaymentRequest",
        source_id=str(request_id),
        idempotency_key=(
            f"reward:{BadgeType.MARKET_PURCHASE_CONFIRMED}:"
            f"market_payment_request_approved:PaymentRequest:{request_id}:"
            f"profile:{buyer.pk}"
        ),
        metadata={
            "reward_code": str(BadgeType.MARKET_PURCHASE_CONFIRMED),
            "event_type": "market_payment_request_approved",
        },
    )
    deal = CompletedDealFactory(
        buyer_profile=buyer,
        seller_profile=seller,
        metadata={"payment_request_id": request_id},
    )

    fire(deal)

    assert not sale_rows().exists()


def test_config_change_is_not_retroactive():
    seller = SellerProfileFactory(is_active=True)
    deal_a = CompletedDealFactory(seller_profile=seller)
    fire(deal_a)
    set_rule(points=150)

    fire(deal_a)
    deal_b = CompletedDealFactory(seller_profile=seller)
    fire(deal_b)

    assert sale_rows(seller.user).get(source_id=str(deal_a.pk)).points == 100
    assert sale_rows(seller.user).get(source_id=str(deal_b.pk)).points == 150
    assert sale_rows(seller.user).count() == 2


def test_configured_points_apply_to_new_deals():
    set_rule(points=150)
    _, _, seller = complete_market()

    assert sale_rows(seller).get().points == 150


def test_disabled_rule_keeps_other_deal_rewards():
    set_rule(enabled=False)
    _, buyer, seller = complete_auction(Decimal("100.00"))

    assert not sale_rows().exists()
    assert PointTransaction.objects.filter(user=seller, source_type=DEAL).get().points == 200
    assert PointTransaction.objects.filter(user=seller, source_type=BONUS).get().points == 100
    assert PointTransaction.objects.filter(user=buyer, source_type=DEAL).exists()
    assert BadgeAward.objects.filter(
        badge_type=BadgeType.FIRST_AUCTION_SALE_CONFIRMED
    ).exists()


def test_deal_processed_while_disabled_is_not_topped_up_later():
    set_rule(enabled=False)
    deal = CompletedDealFactory()
    fire(deal)
    set_rule(enabled=True)

    fire(deal)

    assert not sale_rows().exists()


def test_concurrent_duplicate_insert_keeps_one_row(monkeypatch):
    deal = CompletedDealFactory()
    fire(deal)
    seller = deal.seller_profile
    balance = PointService.get_balance(deal.seller_user)

    # A concurrent evaluation that could not yet see the committed row: the
    # insert hits the unique idempotency key and is treated as already paid.
    monkeypatch.setattr(RewardEngine, "_paid_keys", staticmethod(lambda keys: set()))
    monkeypatch.setattr(
        PointService,
        "_get_idempotent_transaction",
        staticmethod(lambda key: None),
    )
    result = RewardResult()
    for _ in range(3):
        RewardEngine._grant_successful_sale_reward(deal, seller, result)

    assert result.points_awarded == []
    assert sale_rows(deal.seller_user).count() == 1
    assert PointService.get_balance(deal.seller_user) == balance


# --- Ledger, wallet, independence -----------------------------------------------------


def test_ledger_attribution():
    deal = CompletedDealFactory()

    fire(deal)

    row = sale_rows(deal.seller_user).get()
    assert row.user_id == deal.seller_user_id
    assert row.profile_id == deal.seller_profile_id
    assert row.reason == "Successful sale reward"
    assert row.source_id == str(deal.pk)
    assert row.metadata == {
        "reward_code": "successful_sale",
        "deal_id": deal.pk,
        "deal_type": deal.deal_type,
    }


def test_reward_is_points_only():
    deal = CompletedDealFactory()

    fire(deal)

    # Only the existing deal badges; the successful-sale reward adds none.
    assert set(BadgeAward.objects.values_list("badge_type", flat=True)) == {
        BadgeType.MARKET_PURCHASE_CONFIRMED,
        BadgeType.FIRST_MARKET_SALE_CONFIRMED,
    }


def test_reward_runs_from_completed_deal_without_payment_request():
    deal = CompletedDealFactory()

    fire(deal)

    assert not PaymentRequest.objects.exists()
    assert sale_rows(deal.seller_user).count() == 1


def test_reward_lands_in_the_seller_account_wallet():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    CustomerProfileFactory(user=seller, is_active=False)
    deal = CompletedDealFactory(seller_profile=active_seller(seller))
    fire(deal)
    before = PointService.get_balance(seller)

    switch_user_profile(seller, ProfileType.CUSTOMER)

    assert PointService.get_balance(seller) == before
    assert sale_rows(seller).get().profile_id == deal.seller_profile_id


def test_reward_is_independent_of_deal_currency_rate():
    deal = CompletedDealFactory(currency="SAR")

    fire(deal)

    assert not PointTransaction.objects.filter(source_type=DEAL).exists()
    assert sale_rows(deal.seller_user).get().points == 100


def test_same_account_deal_pays_the_seller_role():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_profile = SellerProfileFactory(user=user, is_active=False)
    deal = CompletedDealFactory(buyer_profile=user.active_profile, seller_profile=seller_profile)

    fire(deal)

    # Current behavior (not blocked): the one wallet also gets the seller reward.
    assert sale_rows(user).get().profile_id == seller_profile.pk
