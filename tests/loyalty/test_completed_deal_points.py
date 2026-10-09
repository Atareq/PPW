"""BE-REW-08: completed deals pay floor(amount × rate) × multiplier to both parties."""

import logging
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.auctions.models import AuctionItemStatus
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.deals.models import CompletedDealSourceType, CompletedDealType
from apps.loyalty.constants import (
    BadgeType,
    RewardEventType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import PointService, RewardRuleService
from apps.lucky_wheel.models import LuckyWheelGrant, LuckyWheelGrantSourceType
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    CompletedDealFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)
from tests.payments.test_payment_requests import (
    active_customer,
    active_seller,
    create_approved_order_item,
    create_sold_auction_item,
)

pytestmark = pytest.mark.django_db

DEAL = RewardSourceType.DEAL_COMPLETED
BONUS = RewardSourceType.DEAL_COMPLETION_BONUS


def complete_market(unit_price, quantity=1, buyer=None, seller=None, currency=None):
    item, buyer, seller, _ = create_approved_order_item(
        buyer=buyer, seller=seller, quantity=quantity, unit_price=unit_price
    )
    if currency is not None:
        SellerProfile.objects.filter(user=seller).update(currency=currency)
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    return item, buyer, seller


def complete_auction(price, buyer=None, seller=None):
    item, buyer, seller = create_sold_auction_item(buyer=buyer, seller=seller, price=price)
    request = PaymentRequestService.create_for_auction_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    return item, buyer, seller


def row(source_type, user):
    return PointTransaction.objects.get(source_type=source_type, user=user)


def points(source_type, user):
    return list(
        PointTransaction.objects.filter(source_type=source_type, user=user).values_list(
            "points", flat=True
        )
    )


def fire(deal):
    return RewardEngine.handle_event(
        RewardEventType.DEAL_COMPLETED,
        actor=deal.seller_profile,
        context={"deal": deal},
        run_async_for_aggregates=False,
    )


def set_rule(code, **changes):
    RewardRuleService.update_rule(code, updated_by=None, **changes)


# --- Defaults -----------------------------------------------------------------


def test_default_rules():
    deal_rule = RewardRuleService.get_rule(RewardRuleCode.DEAL_COMPLETED)
    bonus_rule = RewardRuleService.get_rule(RewardRuleCode.DEAL_COMPLETION_BONUS)

    assert deal_rule.multiplier == Decimal("2")
    assert deal_rule.configuration == {"points_per_unit_EGP": 1, "points_per_unit_USD": 1}
    assert deal_rule.required_fields == ("multiplier",)
    assert deal_rule.is_implemented is True
    assert bonus_rule.points == 100
    assert bonus_rule.is_implemented is True


# --- Payouts through the real completion flow ---------------------------------


def test_market_deal_rewards_buyer_and_seller():
    item, buyer, seller = complete_market(Decimal("50.00"), quantity=2)

    for user, profile in ((buyer, buyer.active_profile), (seller, seller.active_profile)):
        deal_row = row(DEAL, user)
        assert deal_row.points == 200
        assert deal_row.profile_id == profile.pk
        assert deal_row.reason == "Completed deal reward"
        bonus_row = row(BONUS, user)
        assert bonus_row.points == 100
        assert bonus_row.profile_id == profile.pk
    assert row(DEAL, buyer).idempotency_key == (
        f"reward:completed_deal:order_item:{item.pk}:buyer"
    )
    assert row(DEAL, seller).idempotency_key == (
        f"reward:completed_deal:order_item:{item.pk}:seller"
    )
    # Unchanged: 30 PP purchase badge reward and 50 PP first market sale.
    # BE-REW-09 adds the seller-only 100 PP successful-sale reward.
    assert PointService.get_balance(buyer) == 200 + 100 + 30
    assert PointService.get_balance(seller) == 200 + 100 + 50 + 100


def test_auction_deal_rewards_buyer_and_seller():
    item, buyer, seller = complete_auction(Decimal("100.00"))

    assert points(DEAL, buyer) == [200]
    assert points(DEAL, seller) == [200]
    assert points(BONUS, buyer) == [100]
    assert points(BONUS, seller) == [100]
    assert row(DEAL, seller).idempotency_key == (
        f"reward:completed_deal:auction_item:{item.pk}:seller"
    )
    # Unchanged: 50 PP purchase, 200 PP first bid completed, 75 PP first sale.
    assert PointService.get_balance(buyer) == 200 + 100 + 50 + 200
    assert PointService.get_balance(seller) == 200 + 100 + 75 + 100


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Decimal("250.75"), 500),
        (Decimal("99.99"), 198),
        (Decimal("0.99"), 0),
    ],
)
def test_base_points_are_floored_before_the_multiplier(amount, expected):
    deal = CompletedDealFactory(amount=amount)

    fire(deal)

    assert points(DEAL, deal.buyer_user) == ([expected] if expected else [])
    assert points(DEAL, deal.seller_user) == ([expected] if expected else [])
    assert points(BONUS, deal.buyer_user) == [100]


def test_rate_applies_before_floor_and_multiplier():
    set_rule(
        RewardRuleCode.DEAL_COMPLETED,
        configuration={"points_per_unit_EGP": 2, "points_per_unit_USD": 1},
    )
    deal = CompletedDealFactory(amount=Decimal("250.75"))

    fire(deal)

    # floor(250.75 × 2) = 501, × 2 = 1002
    assert points(DEAL, deal.buyer_user) == [1002]


def test_fractional_multiplier_rounds_the_result_down():
    set_rule(RewardRuleCode.DEAL_COMPLETED, multiplier=Decimal("1.5"))
    deal = CompletedDealFactory(amount=Decimal("251.00"))

    fire(deal)

    # floor(251) = 251, × 1.5 = 376.5 -> 376
    assert points(DEAL, deal.buyer_user) == [376]


def test_amount_comes_from_the_deal_snapshot():
    seller_profile = SellerProfileFactory()
    item = AuctionItemFactory(
        auction=AuctionFactory(owner=seller_profile),
        status=AuctionItemStatus.SOLD,
        current_price=Decimal("80.00"),
    )
    deal = CompletedDealFactory(
        deal_type=CompletedDealType.AUCTION,
        source_type=CompletedDealSourceType.AUCTION_ITEM,
        source_id=item.pk,
        seller_profile=seller_profile,
        asset=item.bird,
        amount=Decimal("80.00"),
    )
    item.current_price = Decimal("9000.00")
    item.save(update_fields=["current_price", "modified"])

    fire(deal)

    assert points(DEAL, deal.buyer_user) == [160]
    assert points(DEAL, deal.seller_user) == [160]


# --- Currency -----------------------------------------------------------------


def test_usd_deal_uses_its_configured_rate():
    _, buyer, seller = complete_market(Decimal("40.00"), currency="USD")

    assert points(DEAL, buyer) == [80]
    assert points(DEAL, seller) == [80]


def test_unconfigured_currency_earns_no_deal_points_but_keeps_the_bonus(caplog):
    deal = CompletedDealFactory(amount=Decimal("100.00"), currency="SAR")

    with caplog.at_level(logging.WARNING, logger="apps.loyalty.reward_engine"):
        result = fire(deal)

    assert points(DEAL, deal.buyer_user) == []
    assert points(DEAL, deal.seller_user) == []
    assert points(BONUS, deal.buyer_user) == [100]
    assert points(BONUS, deal.seller_user) == [100]
    assert result["skipped"] == [
        {
            "code": DEAL,
            "reason": "unconfigured_currency",
            "deal_id": deal.pk,
            "currency": "SAR",
        }
    ]
    assert "no valid rate for currency 'SAR'" in caplog.text


def test_blank_currency_is_not_assumed_one_to_one():
    deal = CompletedDealFactory(amount=Decimal("100.00"), currency="")

    result = fire(deal)

    assert points(DEAL, deal.buyer_user) == []
    assert result["skipped"][0]["reason"] == "unconfigured_currency"


def test_adding_a_currency_rate_enables_that_currency():
    set_rule(
        RewardRuleCode.DEAL_COMPLETED,
        configuration={"points_per_unit_EGP": 1, "points_per_unit_SAR": 3},
    )
    deal = CompletedDealFactory(amount=Decimal("10.50"), currency="SAR")

    fire(deal)

    # floor(10.50 × 3) = 31, × 2 = 62
    assert points(DEAL, deal.buyer_user) == [62]


@pytest.mark.parametrize("rate", ["one", -1, True, [1]])
def test_api_rejects_invalid_currency_rates(rate):
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {build_token_for_user(manager).access_token}"
    )

    response = client.patch(
        "/api/loyalty/admin/reward-rules/DEAL_COMPLETED/",
        {"configuration": {"points_per_unit_EGP": rate}},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "configuration" in response.data


# --- Configuration --------------------------------------------------------------


def test_configured_multiplier_applies_to_future_deals():
    set_rule(RewardRuleCode.DEAL_COMPLETED, multiplier=Decimal("3"))
    _, buyer, seller = complete_market(Decimal("100.00"))

    assert points(DEAL, buyer) == [300]
    assert points(DEAL, seller) == [300]


def test_disabled_deal_rule_keeps_bonus_badges_and_wheel():
    set_rule(RewardRuleCode.DEAL_COMPLETED, enabled=False)
    item, buyer, seller = complete_auction(Decimal("100.00"))

    assert not PointTransaction.objects.filter(source_type=DEAL).exists()
    assert points(BONUS, buyer) == [100]
    assert points(BONUS, seller) == [100]
    assert BadgeAward.objects.filter(
        profile=buyer.active_profile, badge_type=BadgeType.AUCTION_PURCHASE_CONFIRMED
    ).exists()
    assert BadgeAward.objects.filter(
        profile=seller.active_profile,
        badge_type=BadgeType.FIRST_AUCTION_SALE_CONFIRMED,
    ).exists()
    assert LuckyWheelGrant.objects.filter(
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id=str(item.pk),
    ).count() == 1


def test_disabled_bonus_rule_keeps_deal_points():
    set_rule(RewardRuleCode.DEAL_COMPLETION_BONUS, enabled=False)
    _, buyer, seller = complete_market(Decimal("100.00"))

    assert not PointTransaction.objects.filter(source_type=BONUS).exists()
    assert points(DEAL, buyer) == [200]
    assert points(DEAL, seller) == [200]


def test_configured_bonus_points_apply():
    set_rule(RewardRuleCode.DEAL_COMPLETION_BONUS, points=150)
    deal = CompletedDealFactory()

    fire(deal)

    assert points(BONUS, deal.buyer_user) == [150]
    assert points(BONUS, deal.seller_user) == [150]


# --- Idempotency, legacy, no backfill -------------------------------------------


def test_replayed_event_pays_nothing_twice():
    deal = CompletedDealFactory(amount=Decimal("100.00"))

    for _ in range(3):
        fire(deal)

    assert points(DEAL, deal.buyer_user) == [200]
    assert points(DEAL, deal.seller_user) == [200]
    assert points(BONUS, deal.buyer_user) == [100]
    assert points(BONUS, deal.seller_user) == [100]


def test_config_change_does_not_recalculate_a_rewarded_deal():
    deal = CompletedDealFactory(amount=Decimal("100.00"))
    fire(deal)
    set_rule(RewardRuleCode.DEAL_COMPLETED, multiplier=Decimal("5"))
    set_rule(RewardRuleCode.DEAL_COMPLETION_BONUS, points=500)

    fire(deal)

    assert points(DEAL, deal.buyer_user) == [200]
    assert points(BONUS, deal.buyer_user) == [100]


def test_deal_rewarded_while_rule_disabled_is_not_compensated_later():
    set_rule(RewardRuleCode.DEAL_COMPLETED, enabled=False)
    deal = CompletedDealFactory(amount=Decimal("100.00"))
    fire(deal)
    set_rule(RewardRuleCode.DEAL_COMPLETED, enabled=True)

    fire(deal)

    assert not PointTransaction.objects.filter(source_type=DEAL).exists()


def _legacy_rows(code, event, request_id, profile, points_value):
    key = f"reward:{code}:{event}:PaymentRequest:{request_id}:profile:{profile.pk}"
    PointService.earn_points(
        user=profile.user,
        profile=profile,
        points=points_value,
        reason=f"Reward: {code}",
        source_type="PaymentRequest",
        source_id=str(request_id),
        idempotency_key=key,
        metadata={"reward_code": str(code), "event_type": event},
    )


def test_legacy_rewarded_market_deal_gets_no_new_formula():
    buyer = CustomerProfileFactory(is_active=True)
    seller = SellerProfileFactory(is_active=True)
    _legacy_rows(
        BadgeType.MARKET_PURCHASE_CONFIRMED,
        "market_payment_request_approved",
        901,
        buyer,
        30,
    )
    for profile, role in ((buyer, "customer"), (seller, "seller")):
        PointService.earn_points(
            user=profile.user,
            profile=profile,
            points=100,
            reason="Order item completed amount reward",
            source_type=RewardSourceType.ORDER_COMPLETED_AMOUNT,
            source_id="902",
            idempotency_key=f"points:order_item:902:{role}:amount",
            metadata={"reward_code": "order_completed_amount"},
        )
    before = set(PointTransaction.objects.values_list("idempotency_key", "points"))

    deal = CompletedDealFactory(
        source_id=902,
        buyer_profile=buyer,
        seller_profile=seller,
        amount=Decimal("100.00"),
        metadata={"payment_request_id": 901},
    )
    fire(deal)

    after = set(PointTransaction.objects.values_list("idempotency_key", "points"))
    assert not PointTransaction.objects.filter(source_type__in=[DEAL, BONUS]).exists()
    # Only the seller's first-sale reward is new: that seller holds no
    # first-sale badge in this synthetic history.
    assert {key for key, _ in after - before} == {
        "reward:FIRST_MARKET_SALE_CONFIRMED:market_payment_request_approved:"
        f"PaymentRequest:901:profile:{seller.pk}"
    }


def test_legacy_rewarded_auction_deal_gets_no_new_formula():
    seller_profile = SellerProfileFactory(is_active=True)
    buyer = CustomerProfileFactory(is_active=True)
    item = AuctionItemFactory(
        auction=AuctionFactory(owner=seller_profile),
        status=AuctionItemStatus.SOLD,
        winner=buyer,
        current_price=Decimal("100.00"),
    )
    _legacy_rows(
        BadgeType.AUCTION_PURCHASE_CONFIRMED,
        "auction_payment_request_approved",
        903,
        buyer,
        50,
    )
    deal = CompletedDealFactory(
        deal_type=CompletedDealType.AUCTION,
        source_type=CompletedDealSourceType.AUCTION_ITEM,
        source_id=item.pk,
        buyer_profile=buyer,
        seller_profile=seller_profile,
        asset=item.bird,
        amount=Decimal("100.00"),
        metadata={"payment_request_id": 903},
    )

    fire(deal)

    assert not PointTransaction.objects.filter(source_type__in=[DEAL, BONUS]).exists()


# --- Unchanged neighbours -------------------------------------------------------


def test_first_time_rewards_are_unchanged_on_a_second_deal():
    _, buyer, seller = complete_auction(Decimal("100.00"))
    complete_auction(Decimal("60.00"), buyer=buyer, seller=seller)

    assert points(RewardSourceType.FIRST_BID_COMPLETED, buyer) == [200]
    assert PointTransaction.objects.filter(
        user=seller, metadata__reward_code=BadgeType.FIRST_AUCTION_SALE_CONFIRMED
    ).count() == 1
    assert sorted(points(DEAL, buyer)) == [120, 200]
    assert sorted(points(DEAL, seller)) == [120, 200]
    assert points(BONUS, seller) == [100, 100]
    assert LuckyWheelGrant.objects.filter(
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED
    ).count() == 2


def test_trusted_aggregates_still_count_completed_deals():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    CompletedDealFactory.create_batch(
        10,
        buyer_profile=active_customer(buyer),
        seller_profile=active_seller(seller),
    )

    RewardEngine.evaluate_trusted_buyer(buyer.active_profile.pk)
    RewardEngine.evaluate_trusted_seller(seller.active_profile.pk)

    assert BadgeAward.objects.filter(badge_type=BadgeType.TRUSTED_BUYER).count() == 1
    assert BadgeAward.objects.filter(badge_type=BadgeType.TRUSTED_SELLER).count() == 1


def test_deal_points_land_in_the_shared_account_wallet():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    SellerProfileFactory(user=buyer, is_active=False)
    complete_market(Decimal("100.00"), buyer=buyer)

    switch_user_profile(buyer, ProfileType.SELLER)

    assert PointService.get_balance(buyer) == 200 + 100 + 30
    assert row(DEAL, buyer).profile_id == active_customer_profile_id(buyer)


def active_customer_profile_id(user):
    return user.profiles.get(type=ProfileType.CUSTOMER).pk


def test_same_account_deal_pays_both_roles_into_one_wallet():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_profile = SellerProfileFactory(user=user, is_active=False)
    deal = CompletedDealFactory(
        buyer_profile=user.active_profile,
        seller_profile=seller_profile,
        amount=Decimal("100.00"),
    )

    fire(deal)

    # Current behavior (not blocked): both roles are paid to the same account.
    assert sorted(points(DEAL, user)) == [200, 200]
    assert sorted(points(BONUS, user)) == [100, 100]
