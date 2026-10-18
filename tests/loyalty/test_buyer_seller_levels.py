"""Tests for BE-LTY-01A — Permanent Buyer & Seller Levels Foundation."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.deals.models import CompletedDeal, CompletedDealSourceType, CompletedDealType
from apps.loyalty.constants import (
    BUYER_LEVEL_TABLE,
    LEVEL_BENEFIT_BLOCKER_CODE,
    LEVEL_METRIC_CURRENCY_BLOCKER_CODE,
    SELLER_LEVEL_TABLE,
    RewardSourceType,
)
from apps.loyalty.services import (
    BuyerLevelService,
    CustomerLevelService,
    PointService,
    SellerLevelService,
)
from apps.payments.services import PaymentRequestService
from apps.seasons.models import Season, SeasonStatus
from apps.seasons.services import SeasonService
from apps.users.constants import CurrencyOptions, ProfileType
from apps.users.models import SellerProfile
from tests.factories import (
    CompletedDealFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)
from tests.payments.test_payment_requests import (
    create_approved_order_item,
    create_sold_auction_item,
)

pytestmark = pytest.mark.django_db


def _auth_client(user) -> APIClient:
    client = APIClient()
    token = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


# ============================================================================
# 1–7. BUYER LEVEL THRESHOLDS & NAMES
# ============================================================================


def test_buyer_level_table_matches_verified_product_spec():
    expected = [
        (1, "trusted_buyer", Decimal("100"), "مشتري موثوق", "مزايدة بدون عربون"),
        (2, "active_buyer", Decimal("500"), "مشتري نشط", "خصم 2%"),
        (3, "golden_buyer", Decimal("1000"), "مشتري ذهبي", "كاش باك 1%"),
        (4, "ep_buyer", Decimal("5000"), "مشتري EP", "مهلة سداد أطول"),
        (5, "elite_buyer", Decimal("10000"), "Elite Buyer", "دعوات خاصة + شحن مجاني"),
    ]
    actual = [
        (
            spec.level_number,
            spec.code,
            spec.threshold,
            spec.level_name,
            spec.benefit_label,
        )
        for spec in BUYER_LEVEL_TABLE
    ]
    assert actual == expected


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("0.01"), Decimal("99.99")])
def test_buyer_below_100_has_no_earned_level(amount):
    res = BuyerLevelService.resolve_level_for_total(amount, currency="EGP")
    assert res.current_level is None
    assert res.level_code is None
    assert res.level_name is None
    assert res.benefit_label is None
    assert res.progress_total == amount
    assert res.next_level_threshold == Decimal("100")
    assert res.is_blocked is False


def test_buyer_at_100_reaches_trusted_buyer():
    res = BuyerLevelService.resolve_level_for_total(Decimal("100.00"), currency="EGP")
    assert res.current_level == 1
    assert res.level_code == "trusted_buyer"
    assert res.level_name == "مشتري موثوق"
    assert res.benefit_label == "مزايدة بدون عربون"
    assert res.next_level_threshold == Decimal("500")


def test_buyer_at_500_reaches_active_buyer():
    res = BuyerLevelService.resolve_level_for_total(Decimal("500.00"), currency="EGP")
    assert res.current_level == 2
    assert res.level_code == "active_buyer"
    assert res.level_name == "مشتري نشط"
    assert res.benefit_label == "خصم 2%"
    assert res.next_level_threshold == Decimal("1000")


def test_buyer_at_1000_reaches_golden_buyer():
    res = BuyerLevelService.resolve_level_for_total(Decimal("1000.00"), currency="EGP")
    assert res.current_level == 3
    assert res.level_code == "golden_buyer"
    assert res.level_name == "مشتري ذهبي"
    assert res.benefit_label == "كاش باك 1%"
    assert res.next_level_threshold == Decimal("5000")


def test_buyer_at_5000_reaches_ep_buyer():
    res = BuyerLevelService.resolve_level_for_total(Decimal("5000.00"), currency="EGP")
    assert res.current_level == 4
    assert res.level_code == "ep_buyer"
    assert res.level_name == "مشتري EP"
    assert res.benefit_label == "مهلة سداد أطول"
    assert res.next_level_threshold == Decimal("10000")


def test_buyer_at_10000_reaches_elite_buyer():
    res = BuyerLevelService.resolve_level_for_total(Decimal("10000.00"), currency="EGP")
    assert res.current_level == 5
    assert res.level_code == "elite_buyer"
    assert res.level_name == "Elite Buyer"
    assert res.benefit_label == "دعوات خاصة + شحن مجاني"
    assert res.next_level_threshold is None


@pytest.mark.parametrize(
    ("amount", "expected_level", "expected_name", "expected_next"),
    [
        (Decimal("100.01"), 1, "مشتري موثوق", Decimal("500")),
        (Decimal("499.99"), 1, "مشتري موثوق", Decimal("500")),
        (Decimal("750.50"), 2, "مشتري نشط", Decimal("1000")),
        (Decimal("999.99"), 2, "مشتري نشط", Decimal("1000")),
        (Decimal("2500.00"), 3, "مشتري ذهبي", Decimal("5000")),
        (Decimal("4999.99"), 3, "مشتري ذهبي", Decimal("5000")),
        (Decimal("7500.00"), 4, "مشتري EP", Decimal("10000")),
        (Decimal("9999.99"), 4, "مشتري EP", Decimal("10000")),
        (Decimal("25000.00"), 5, "Elite Buyer", None),
    ],
)
def test_buyer_values_between_thresholds_resolve_correctly(
    amount,
    expected_level,
    expected_name,
    expected_next,
):
    res = BuyerLevelService.resolve_level_for_total(amount, currency="EGP")
    assert res.current_level == expected_level
    assert res.level_name == expected_name
    assert res.next_level_threshold == expected_next


# ============================================================================
# 8–14. SELLER LEVEL THRESHOLDS & NAMES
# ============================================================================


def test_seller_level_table_matches_verified_product_spec():
    expected = [
        (1, "verified_account", Decimal("100"), "حساب موثّق", "شارة + ثقة"),
        (2, "certified_seller", Decimal("500"), "بائع معتمد", "شارة + ترتيب أعلى"),
        (3, "golden_seller", Decimal("1000"), "بائع ذهبي", "حجل ذهبي + إبراز"),
        (4, "elite_seller", Decimal("5000"), "بائع نخبة", "Banner + عمولة أقل"),
        (5, "legendary_seller", Decimal("10000"), "أسطوري", "توثيق دائم + دعم P"),
    ]
    actual = [
        (
            spec.level_number,
            spec.code,
            spec.threshold,
            spec.level_name,
            spec.benefit_label,
        )
        for spec in SELLER_LEVEL_TABLE
    ]
    assert actual == expected


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("0.01"), Decimal("99.99")])
def test_seller_below_100_has_no_earned_level(amount):
    res = SellerLevelService.resolve_level_for_total(amount, currency="EGP")
    assert res.current_level is None
    assert res.level_code is None
    assert res.level_name is None
    assert res.benefit_label is None
    assert res.progress_total == amount
    assert res.next_level_threshold == Decimal("100")
    assert res.is_blocked is False


def test_seller_at_100_reaches_verified_account():
    res = SellerLevelService.resolve_level_for_total(Decimal("100.00"), currency="EGP")
    assert res.current_level == 1
    assert res.level_code == "verified_account"
    assert res.level_name == "حساب موثّق"
    assert res.benefit_label == "شارة + ثقة"
    assert res.next_level_threshold == Decimal("500")


def test_seller_at_500_reaches_certified_seller():
    res = SellerLevelService.resolve_level_for_total(Decimal("500.00"), currency="EGP")
    assert res.current_level == 2
    assert res.level_code == "certified_seller"
    assert res.level_name == "بائع معتمد"
    assert res.benefit_label == "شارة + ترتيب أعلى"
    assert res.next_level_threshold == Decimal("1000")


def test_seller_at_1000_reaches_golden_seller():
    res = SellerLevelService.resolve_level_for_total(Decimal("1000.00"), currency="EGP")
    assert res.current_level == 3
    assert res.level_code == "golden_seller"
    assert res.level_name == "بائع ذهبي"
    assert res.benefit_label == "حجل ذهبي + إبراز"
    assert res.next_level_threshold == Decimal("5000")


def test_seller_at_5000_reaches_elite_seller():
    res = SellerLevelService.resolve_level_for_total(Decimal("5000.00"), currency="EGP")
    assert res.current_level == 4
    assert res.level_code == "elite_seller"
    assert res.level_name == "بائع نخبة"
    assert res.benefit_label == "Banner + عمولة أقل"
    assert res.next_level_threshold == Decimal("10000")


def test_seller_at_10000_reaches_legendary_seller():
    res = SellerLevelService.resolve_level_for_total(Decimal("10000.00"), currency="EGP")
    assert res.current_level == 5
    assert res.level_code == "legendary_seller"
    assert res.level_name == "أسطوري"
    assert res.benefit_label == "توثيق دائم + دعم P"
    assert res.next_level_threshold is None


@pytest.mark.parametrize(
    ("amount", "expected_level", "expected_name", "expected_next"),
    [
        (Decimal("100.01"), 1, "حساب موثّق", Decimal("500")),
        (Decimal("499.99"), 1, "حساب موثّق", Decimal("500")),
        (Decimal("750.50"), 2, "بائع معتمد", Decimal("1000")),
        (Decimal("999.99"), 2, "بائع معتمد", Decimal("1000")),
        (Decimal("2500.00"), 3, "بائع ذهبي", Decimal("5000")),
        (Decimal("4999.99"), 3, "بائع ذهبي", Decimal("5000")),
        (Decimal("7500.00"), 4, "بائع نخبة", Decimal("10000")),
        (Decimal("9999.99"), 4, "بائع نخبة", Decimal("10000")),
        (Decimal("25000.00"), 5, "أسطوري", None),
    ],
)
def test_seller_values_between_thresholds_resolve_correctly(
    amount,
    expected_level,
    expected_name,
    expected_next,
):
    res = SellerLevelService.resolve_level_for_total(amount, currency="EGP")
    assert res.current_level == expected_level
    assert res.level_name == expected_name
    assert res.next_level_threshold == expected_next


# ============================================================================
# 15–20. SEPARATION OF BUYER & SELLER LEVELS, PROFILE SWITCH, PP SPEND, SEASONS
# ============================================================================


def test_buyer_purchases_do_not_increase_seller_level():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    seller_profile = SellerProfileFactory(
        user=user,
        is_active=False,
        activated=True,
        is_valid=True,
        currency=CurrencyOptions.EGP,
    )
    other_seller = SellerProfileFactory(currency=CurrencyOptions.EGP)

    CompletedDealFactory(
        buyer_profile=customer_profile,
        seller_profile=other_seller,
        amount=Decimal("1500.00"),
        currency=CurrencyOptions.EGP,
    )

    buyer_status = BuyerLevelService.evaluate_permanent_level(
        customer_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    seller_status = SellerLevelService.evaluate_permanent_level(
        seller_profile,
        canonical_currency=CurrencyOptions.EGP,
    )

    assert buyer_status.current_level == 3
    assert buyer_status.level_name == "مشتري ذهبي"
    assert seller_status.current_level is None
    assert seller_status.level_name is None
    assert seller_status.progress_total == Decimal("0.00")


def test_seller_sales_do_not_increase_buyer_level():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    seller_profile = SellerProfileFactory(
        user=user,
        is_active=False,
        activated=True,
        is_valid=True,
        currency=CurrencyOptions.EGP,
    )
    other_buyer = CustomerProfileFactory(currency=CurrencyOptions.EGP)

    CompletedDealFactory(
        buyer_profile=other_buyer,
        seller_profile=seller_profile,
        amount=Decimal("6000.00"),
        currency=CurrencyOptions.EGP,
    )

    seller_status = SellerLevelService.evaluate_permanent_level(
        seller_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    buyer_status = BuyerLevelService.evaluate_permanent_level(
        customer_profile,
        canonical_currency=CurrencyOptions.EGP,
    )

    assert seller_status.current_level == 4
    assert seller_status.level_name == "بائع نخبة"
    assert buyer_status.current_level is None
    assert buyer_status.level_name is None
    assert buyer_status.progress_total == Decimal("0.00")


def test_same_user_account_has_independent_buyer_and_seller_levels_and_survives_profile_switch():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    seller_profile = SellerProfileFactory(
        user=user,
        is_active=False,
        activated=True,
        is_valid=True,
        currency=CurrencyOptions.EGP,
    )
    other_seller = SellerProfileFactory(currency=CurrencyOptions.EGP)
    other_buyer = CustomerProfileFactory(currency=CurrencyOptions.EGP)

    CompletedDealFactory(
        buyer_profile=customer_profile,
        seller_profile=other_seller,
        amount=Decimal("1200.00"),
        currency=CurrencyOptions.EGP,
    )
    CompletedDealFactory(
        buyer_profile=other_buyer,
        seller_profile=seller_profile,
        amount=Decimal("250.00"),
        currency=CurrencyOptions.EGP,
    )

    switched, new_role = switch_user_profile(user, ProfileType.SELLER)
    assert switched is True
    assert new_role == ProfileType.SELLER

    buyer_after_switch = BuyerLevelService.evaluate_permanent_level(
        customer_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    seller_after_switch = SellerLevelService.evaluate_permanent_level(
        seller_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert buyer_after_switch.level_name == "مشتري ذهبي"
    assert seller_after_switch.level_name == "حساب موثّق"

    switched_back, role_back = switch_user_profile(user, ProfileType.CUSTOMER)
    assert switched_back is True
    assert role_back == ProfileType.CUSTOMER

    assert (
        BuyerLevelService.evaluate_permanent_level(
            customer_profile,
            canonical_currency=CurrencyOptions.EGP,
        ).level_name
        == "مشتري ذهبي"
    )
    assert (
        SellerLevelService.evaluate_permanent_level(
            seller_profile,
            canonical_currency=CurrencyOptions.EGP,
        ).level_name
        == "حساب موثّق"
    )


def test_pp_spending_does_not_reduce_buyer_or_seller_level():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    other_seller = SellerProfileFactory(currency=CurrencyOptions.EGP)

    CompletedDealFactory(
        buyer_profile=customer_profile,
        seller_profile=other_seller,
        amount=Decimal("500.00"),
        currency=CurrencyOptions.EGP,
    )
    PointService.earn_points(
        user=user,
        profile=customer_profile,
        points=300,
        source_type=RewardSourceType.DEAL_COMPLETED,
        source_id="deal:1",
        reason="Test earn",
        idempotency_key="test:level:pp:earn:1",
    )
    PointService.spend_points(
        user=user,
        profile=customer_profile,
        points=250,
        source_type=RewardSourceType.LUCKY_WHEEL,
        source_id="spin:1",
        reason="Test spend",
        idempotency_key="test:level:pp:spend:1",
    )

    status_after_spend = BuyerLevelService.evaluate_permanent_level(
        customer_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert status_after_spend.current_level == 2
    assert status_after_spend.level_name == "مشتري نشط"
    assert status_after_spend.progress_total == Decimal("500.00")


def test_seller_season_finalization_does_not_reduce_permanent_seller_level():
    seller = SellerProfileFactory(currency=CurrencyOptions.EGP)
    buyer = CustomerProfileFactory(currency=CurrencyOptions.EGP)
    CompletedDealFactory(
        buyer_profile=buyer,
        seller_profile=seller,
        amount=Decimal("10000.00"),
        currency=CurrencyOptions.EGP,
    )

    now = timezone.now()
    season = SeasonService.create_season(
        title="Winter Season",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
    )
    season = SeasonService.finalize(season)
    assert season.status == SeasonStatus.FINALIZED

    seller_status = SellerLevelService.evaluate_permanent_level(
        seller,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert seller_status.current_level == 5
    assert seller_status.level_name == "أسطوري"
    assert seller_status.progress_total == Decimal("10000.00")


# ============================================================================
# 21–25. CANONICAL BUSINESS SOURCE & CURRENCY UNIT GATE
# ============================================================================


def test_incomplete_order_and_unapproved_payment_request_contribute_nothing():
    item, buyer, seller, _ = create_approved_order_item(
        quantity=1,
        unit_price=Decimal("1000.00"),
    )
    SellerProfile.objects.filter(user=seller).update(currency=CurrencyOptions.EGP)
    buyer_profile = buyer.active_profile
    seller_profile = seller.active_profile

    # Approved OrderItem alone (no PaymentRequest)
    assert (
        BuyerLevelService.evaluate_permanent_level(
            buyer_profile,
            canonical_currency=CurrencyOptions.EGP,
        ).current_level
        is None
    )
    assert (
        SellerLevelService.evaluate_permanent_level(
            seller_profile,
            canonical_currency=CurrencyOptions.EGP,
        ).current_level
        is None
    )

    # Pending PaymentRequest alone (not seller-approved)
    payment_request = PaymentRequestService.create_for_order_item(
        item.pk,
        buyer_profile,
    )
    assert (
        BuyerLevelService.evaluate_permanent_level(
            buyer_profile,
            canonical_currency=CurrencyOptions.EGP,
        ).progress_total
        == Decimal("0.00")
    )
    assert (
        SellerLevelService.evaluate_permanent_level(
            seller_profile,
            canonical_currency=CurrencyOptions.EGP,
        ).progress_total
        == Decimal("0.00")
    )

    # Seller approval creates CompletedDeal -> contributes once
    PaymentRequestService.approve(payment_request.pk, seller_profile)
    assert (
        CompletedDeal.objects.filter(
            source_type=CompletedDealSourceType.ORDER_ITEM,
            source_id=item.pk,
        ).count()
        == 1
    )

    buyer_after = BuyerLevelService.evaluate_permanent_level(
        buyer_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    seller_after = SellerLevelService.evaluate_permanent_level(
        seller_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert buyer_after.current_level == 3
    assert buyer_after.level_name == "مشتري ذهبي"
    assert buyer_after.progress_total == Decimal("1000.00")
    assert seller_after.current_level == 3
    assert seller_after.level_name == "بائع ذهبي"
    assert seller_after.progress_total == Decimal("1000.00")


def test_both_auction_and_market_completed_deals_contribute_to_totals():
    buyer = CustomerProfileFactory(currency=CurrencyOptions.EGP)
    seller = SellerProfileFactory(currency=CurrencyOptions.EGP)

    CompletedDealFactory(
        buyer_profile=buyer,
        seller_profile=seller,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        deal_type=CompletedDealType.MARKET,
        amount=Decimal("200.00"),
        currency=CurrencyOptions.EGP,
    )
    CompletedDealFactory(
        buyer_profile=buyer,
        seller_profile=seller,
        source_type=CompletedDealSourceType.AUCTION_ITEM,
        deal_type=CompletedDealType.AUCTION,
        amount=Decimal("300.00"),
        currency=CurrencyOptions.EGP,
    )

    buyer_status = BuyerLevelService.evaluate_permanent_level(
        buyer,
        canonical_currency=CurrencyOptions.EGP,
    )
    seller_status = SellerLevelService.evaluate_permanent_level(
        seller,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert buyer_status.progress_total == Decimal("500.00")
    assert buyer_status.level_name == "مشتري نشط"
    assert seller_status.progress_total == Decimal("500.00")
    assert seller_status.level_name == "بائع معتمد"


def test_currency_unit_gate_blocks_unconfigured_or_multi_currency_aggregation():
    buyer = CustomerProfileFactory(currency=CurrencyOptions.EGP)
    seller_egp = SellerProfileFactory(currency=CurrencyOptions.EGP)
    seller_usd = SellerProfileFactory(currency=CurrencyOptions.USD)

    CompletedDealFactory(
        buyer_profile=buyer,
        seller_profile=seller_egp,
        amount=Decimal("80.00"),
        currency=CurrencyOptions.EGP,
    )
    CompletedDealFactory(
        buyer_profile=buyer,
        seller_profile=seller_usd,
        amount=Decimal("50.00"),
        currency=CurrencyOptions.USD,
    )

    # Without a canonical currency unit, automatic evaluation is blocked
    unconfigured = BuyerLevelService.evaluate_permanent_level(buyer)
    assert unconfigured.is_blocked is True
    assert unconfigured.blocker_code == LEVEL_METRIC_CURRENCY_BLOCKER_CODE
    assert unconfigured.blocker_code == "LEVEL METRIC CURRENCY UNIT — BLOCKED"
    assert unconfigured.current_level is None
    assert unconfigured.progress_total is None

    # Even if EGP is requested, mixed EGP + USD deals must never be added together or ignored
    mixed_status = BuyerLevelService.evaluate_permanent_level(
        buyer,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert mixed_status.is_blocked is True
    assert mixed_status.blocker_code == LEVEL_METRIC_CURRENCY_BLOCKER_CODE
    assert mixed_status.current_level is None
    assert mixed_status.progress_total is None

    # Per-currency breakdown remains inspectable without cross-currency addition
    totals = BuyerLevelService.get_completed_deal_totals_by_currency(buyer)
    assert totals == {
        CurrencyOptions.EGP: Decimal("80.00"),
        CurrencyOptions.USD: Decimal("50.00"),
    }


# ============================================================================
# 26. PERMANENT / NO DOWNGRADE GUARANTEE
# ============================================================================


def test_completed_deal_immutability_prevents_level_downgrade():
    buyer = CustomerProfileFactory(currency=CurrencyOptions.EGP)
    seller = SellerProfileFactory(currency=CurrencyOptions.EGP)
    deal = CompletedDealFactory(
        buyer_profile=buyer,
        seller_profile=seller,
        amount=Decimal("500.00"),
        currency=CurrencyOptions.EGP,
    )

    initial_status = BuyerLevelService.evaluate_permanent_level(
        buyer,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert initial_status.current_level == 2
    assert initial_status.level_name == "مشتري نشط"

    deal.amount = Decimal("10.00")
    with pytest.raises(DjangoValidationError):
        deal.save()

    with pytest.raises(DjangoValidationError):
        deal.delete()

    after_status = BuyerLevelService.evaluate_permanent_level(
        buyer,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert after_status.current_level == 2
    assert after_status.level_name == "مشتري نشط"
    assert after_status.progress_total == Decimal("500.00")


# ============================================================================
# 27–30. API / SECURITY & PRIVACY
# ============================================================================


def test_buyer_can_see_own_profile_and_other_users_cannot_access_buyer_profile():
    buyer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    buyer_profile = buyer_user.active_profile

    own_client = _auth_client(buyer_user)
    own_resp = own_client.get(f"/api/users/mobile/customers/{buyer_profile.pk}/")
    assert own_resp.status_code == status.HTTP_200_OK
    assert own_resp.data["id"] == buyer_profile.pk
    assert "customer_level" in own_resp.data

    other_client = _auth_client(other_user)
    other_resp = other_client.get(f"/api/users/mobile/customers/{buyer_profile.pk}/")
    assert other_resp.status_code == status.HTTP_404_NOT_FOUND


def test_seller_public_representation_exposes_only_safe_level_data_without_private_deals():
    customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_profile = SellerProfileFactory(is_active=True, currency=CurrencyOptions.EGP)
    CompletedDealFactory(
        buyer_profile=customer_user.active_profile,
        seller_profile=seller_profile,
        amount=Decimal("250.00"),
        currency=CurrencyOptions.EGP,
    )

    client = _auth_client(customer_user)
    resp = client.get(f"/api/feed/sellers/{seller_profile.pk}/")
    assert resp.status_code == status.HTTP_200_OK
    assert "seller_level" in resp.data
    assert "seller_level_label" in resp.data
    assert "completed_deals" not in resp.data
    assert "bank_account" not in resp.data
    assert "wallet" not in resp.data
    assert "transfer_phone" not in resp.data


# ============================================================================
# 31–35. BENEFITS GATE — NO UNVERIFIED BENEFIT SIDE EFFECTS EXECUTED
# ============================================================================


def test_no_undefined_benefit_side_effects_are_executed_on_deal_completion():
    item, buyer, seller = create_sold_auction_item(price=Decimal("10000.00"))
    SellerProfile.objects.filter(user=seller).update(currency=CurrencyOptions.EGP)
    buyer_profile = buyer.active_profile
    seller_profile = seller.active_profile
    initial_cashback = buyer_profile.cashback_balance
    initial_deadline = item.auction.payment_deadline_days

    payment_request = PaymentRequestService.create_for_auction_item(
        item.pk,
        buyer_profile,
    )
    PaymentRequestService.approve(payment_request.pk, seller_profile)

    buyer_profile.refresh_from_db()
    seller_profile.refresh_from_db()
    item.auction.refresh_from_db()
    deal = CompletedDeal.objects.get(
        source_type=CompletedDealSourceType.AUCTION_ITEM,
        source_id=item.pk,
    )

    # 31. All level benefit statuses remain explicitly BLOCKED
    buyer_level = BuyerLevelService.evaluate_permanent_level(
        buyer_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    seller_level = SellerLevelService.evaluate_permanent_level(
        seller_profile,
        canonical_currency=CurrencyOptions.EGP,
    )
    assert buyer_level.level_name == "Elite Buyer"
    assert buyer_level.benefit_status == LEVEL_BENEFIT_BLOCKER_CODE
    assert seller_level.level_name == "أسطوري"
    assert seller_level.benefit_status == LEVEL_BENEFIT_BLOCKER_CODE

    # 32. Level 1,000 buyer 1% cashback does NOT credit cashback_balance
    assert buyer_profile.cashback_balance == initial_cashback
    # 33 & 34. Free shipping / lower commission do NOT alter deal amount or seller balance
    assert deal.amount == Decimal("10000.00")
    assert seller_profile.balance == Decimal("0.00")
    # 35. Longer payment deadline does NOT mutate auction.payment_deadline_days
    assert item.auction.payment_deadline_days == initial_deadline
