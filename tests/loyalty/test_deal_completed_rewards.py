"""BE-REW-05: completion rewards run from CompletedDeal, not from PaymentRequest."""

from decimal import Decimal

import pytest

from apps.auctions.models import AuctionItemStatus
from apps.deals.models import CompletedDeal, CompletedDealSourceType, CompletedDealType
from apps.deals.services import CompletedDealService
from apps.loyalty.constants import BadgeType, RewardEventType, RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import BadgeService, PointService
from apps.lucky_wheel.models import LuckyWheelGrant, LuckyWheelGrantSourceType
from apps.orders.models import OrderItemStatus
from apps.payments.models import PaymentRequest
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BirdFactory,
    CompletedDealFactory,
    OrderFactory,
    OrderItemFactory,
    SellerProfileFactory,
    SupplyFactory,
    UserAccountFactory,
)
from tests.payments.test_payment_requests import (
    active_customer,
    active_seller,
    create_approved_order_item,
    create_sold_auction_item,
)

pytestmark = pytest.mark.django_db


def fire(deal):
    return RewardEngine.handle_event(
        RewardEventType.DEAL_COMPLETED,
        actor=deal.seller_profile,
        context={"deal": deal},
        run_async_for_aggregates=False,
    )


def ledger():
    return set(
        PointTransaction.objects.values_list("idempotency_key", "user_id", "points")
    )


def buyer_and_seller():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    return buyer, seller, active_customer(buyer), active_seller(seller)


def auction_deal_without_payment_request():
    buyer, seller, buyer_profile, seller_profile = buyer_and_seller()
    item = AuctionItemFactory(
        auction=AuctionFactory(owner=seller_profile),
        status=AuctionItemStatus.SOLD,
        winner=buyer_profile,
        current_price=Decimal("150.00"),
    )
    deal = CompletedDealFactory(
        deal_type=CompletedDealType.AUCTION,
        source_type=CompletedDealSourceType.AUCTION_ITEM,
        source_id=item.pk,
        buyer_profile=buyer_profile,
        seller_profile=seller_profile,
        asset=item.bird,
        amount=item.current_price,
    )
    return deal, item, buyer, seller


# --- Existing behavior through the real completion flow ---------------------


def test_market_completion_grants_current_market_rewards_once():
    item, buyer, seller, _ = create_approved_order_item(unit_price=Decimal("33.40"))
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)

    PaymentRequestService.approve(request.pk, seller.active_profile)

    buyer_profile_id = buyer.active_profile.pk
    seller_profile_id = seller.active_profile.pk
    assert ledger() == {
        (
            "reward:MARKET_PURCHASE_CONFIRMED:market_payment_request_approved:"
            f"PaymentRequest:{request.pk}:profile:{buyer_profile_id}",
            buyer.pk,
            30,
        ),
        (
            "reward:FIRST_MARKET_SALE_CONFIRMED:market_payment_request_approved:"
            f"PaymentRequest:{request.pk}:profile:{seller_profile_id}",
            seller.pk,
            50,
        ),
        (f"points:order_item:{item.pk}:customer:amount", buyer.pk, 66),
        (f"points:order_item:{item.pk}:customer:bonus", buyer.pk, 100),
        (f"points:order_item:{item.pk}:seller:amount", seller.pk, 66),
        (f"points:order_item:{item.pk}:seller:bonus", seller.pk, 100),
    }
    assert CompletedDeal.objects.count() == 1


def test_auction_completion_grants_current_auction_rewards_once():
    item, buyer, seller = create_sold_auction_item()
    request = PaymentRequestService.create_for_auction_item(item.pk, buyer.active_profile)

    PaymentRequestService.approve(request.pk, seller.active_profile)

    assert ledger() == {
        (
            "reward:AUCTION_PURCHASE_CONFIRMED:auction_payment_request_approved:"
            f"PaymentRequest:{request.pk}:profile:{buyer.active_profile.pk}",
            buyer.pk,
            50,
        ),
        (f"points:first_bid_completed:user:{buyer.pk}", buyer.pk, 200),
        (
            "reward:FIRST_AUCTION_SALE_CONFIRMED:auction_payment_request_approved:"
            f"PaymentRequest:{request.pk}:profile:{seller.active_profile.pk}",
            seller.pk,
            75,
        ),
    }
    grant = LuckyWheelGrant.objects.get(
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED
    )
    assert grant.profile_id == buyer.active_profile.pk
    assert grant.source_id == str(item.pk)


def test_rejected_request_creates_no_deal_and_no_deal_rewards():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)

    PaymentRequestService.reject(request.pk, seller.active_profile)

    assert not CompletedDeal.objects.exists()
    assert not PointTransaction.objects.exists()
    assert not BadgeAward.objects.exists()


# --- Engine consumes CompletedDeal only -------------------------------------


def test_deal_rewards_need_no_payment_request():
    buyer, seller, buyer_profile, seller_profile = buyer_and_seller()
    market_deal = CompletedDealFactory(
        buyer_profile=buyer_profile,
        seller_profile=seller_profile,
        amount=Decimal("40.00"),
    )
    auction_deal, auction_item, auction_buyer, auction_seller = (
        auction_deal_without_payment_request()
    )

    fire(market_deal)
    fire(auction_deal)

    assert not PaymentRequest.objects.exists()
    assert PointService.get_balance(buyer) == 30 + 40 + 100
    assert PointService.get_balance(seller) == 50 + 40 + 100
    assert PointService.get_balance(auction_buyer) == 50 + 200
    assert PointService.get_balance(auction_seller) == 75
    assert PointTransaction.objects.filter(
        idempotency_key=(
            "reward:MARKET_PURCHASE_CONFIRMED:deal_completed:"
            f"order_item:{market_deal.source_id}:profile:{buyer_profile.pk}"
        ),
        source_type="order_item",
        source_id=str(market_deal.source_id),
    ).exists()
    assert LuckyWheelGrant.objects.filter(
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id=str(auction_item.pk),
    ).count() == 1


def test_repeated_deal_completed_event_is_idempotent():
    deal, _, buyer, seller = auction_deal_without_payment_request()
    market_deal = CompletedDealFactory(
        buyer_profile=deal.buyer_profile,
        seller_profile=deal.seller_profile,
    )

    for _ in range(2):
        fire(deal)
        fire(market_deal)

    assert PointTransaction.objects.count() == 1 + 1 + 1 + 1 + 1 + 4
    assert BadgeAward.objects.count() == 4
    assert LuckyWheelGrant.objects.count() == 1
    assert PointService.get_balance(buyer) == 50 + 200 + 30 + 10 + 100


def test_deal_service_retry_does_not_emit_rewards_again():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    before = ledger()

    CompletedDealService.create_from_market_completion(item)

    assert ledger() == before


def test_legacy_payment_events_no_longer_run_a_second_reward_path():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    before = ledger()

    result = RewardEngine.handle_event(
        RewardEventType.MARKET_PAYMENT_REQUEST_APPROVED,
        actor=seller.active_profile,
        context={"payment_request": request},
    )

    assert ledger() == before
    assert result["points_awarded"] == [] and result["badges_awarded"] == []


# --- Historical rewards are never paid twice --------------------------------


def test_deal_with_legacy_rewarded_identity_is_not_rewarded_again():
    buyer, seller, buyer_profile, seller_profile = buyer_and_seller()
    legacy_request_id = 555
    order_item_id = 777
    for profile, code, points in (
        (buyer_profile, BadgeType.MARKET_PURCHASE_CONFIRMED, 30),
        (seller_profile, BadgeType.FIRST_MARKET_SALE_CONFIRMED, 50),
    ):
        key = (
            f"reward:{code}:market_payment_request_approved:"
            f"PaymentRequest:{legacy_request_id}:profile:{profile.pk}"
        )
        metadata = {
            "reward_code": str(code),
            "event_type": "market_payment_request_approved",
        }
        PointService.earn_points(
            user=profile.user,
            profile=profile,
            points=points,
            reason=f"Reward: {code}",
            source_type="PaymentRequest",
            source_id=str(legacy_request_id),
            idempotency_key=key,
            metadata=metadata,
        )
        BadgeService.award_badge(
            profile=profile,
            badge_type=code,
            source_type="PaymentRequest",
            source_id=str(legacy_request_id),
            idempotency_key=key,
            metadata=metadata,
        )
    for profile, role in ((buyer_profile, "customer"), (seller_profile, "seller")):
        for suffix, source_type, points in (
            ("amount", RewardSourceType.ORDER_COMPLETED_AMOUNT, 50),
            ("bonus", RewardSourceType.ORDER_COMPLETED_BONUS, 100),
        ):
            PointService.earn_points(
                user=profile.user,
                profile=profile,
                points=points,
                reason=(
                    "Order item completed amount reward"
                    if suffix == "amount"
                    else "Order item completed bonus reward"
                ),
                source_type=source_type,
                source_id=str(order_item_id),
                idempotency_key=f"points:order_item:{order_item_id}:{role}:{suffix}",
                metadata={"reward_code": str(source_type)},
            )
    before = ledger()
    balances = (PointService.get_balance(buyer), PointService.get_balance(seller))

    # A backfilled deal for the same historical sale.
    deal = CompletedDealFactory(
        source_id=order_item_id,
        buyer_profile=buyer_profile,
        seller_profile=seller_profile,
        amount=Decimal("50.00"),
        metadata={"payment_request_id": legacy_request_id},
    )
    fire(deal)

    assert ledger() == before
    assert (PointService.get_balance(buyer), PointService.get_balance(seller)) == balances
    assert BadgeAward.objects.count() == 2


def test_seller_with_pre_deal_first_sale_is_not_paid_first_sale_again():
    buyer, seller, buyer_profile, seller_profile = buyer_and_seller()
    BadgeService.award_badge(
        profile=seller_profile,
        badge_type=BadgeType.FIRST_MARKET_SALE_CONFIRMED,
        source_type="PaymentRequest",
        source_id="1",
        idempotency_key="legacy-first-market-sale",
    )
    item, _, _, _ = create_approved_order_item(buyer=buyer, seller=seller)
    request = PaymentRequestService.create_for_order_item(item.pk, buyer_profile)

    PaymentRequestService.approve(request.pk, seller_profile)

    assert CompletedDeal.objects.count() == 1
    assert not PointTransaction.objects.filter(
        user=seller,
        metadata__reward_code=BadgeType.FIRST_MARKET_SALE_CONFIRMED,
    ).exists()
    assert BadgeAward.objects.filter(
        profile=seller_profile,
        badge_type=BadgeType.FIRST_MARKET_SALE_CONFIRMED,
    ).count() == 1


# --- Aggregates count CompletedDeal -----------------------------------------


def test_trusted_buyer_counts_completed_deals_not_payment_requests():
    buyer, seller, buyer_profile, seller_profile = buyer_and_seller()
    asset = SupplyFactory(owner=seller_profile)
    for _ in range(10):
        PaymentRequest.objects.create(
            buyer=buyer_profile,
            seller=seller_profile,
            asset=asset,
            amount=Decimal("10.00"),
            status="approved",
            request_type="market",
        )
    CompletedDealFactory.create_batch(
        9, buyer_profile=buyer_profile, seller_profile=seller_profile
    )

    RewardEngine.evaluate_trusted_buyer(buyer_profile.pk)
    assert not BadgeAward.objects.filter(badge_type=BadgeType.TRUSTED_BUYER).exists()

    CompletedDealFactory(buyer_profile=buyer_profile, seller_profile=seller_profile)
    RewardEngine.evaluate_trusted_buyer(buyer_profile.pk)
    assert BadgeAward.objects.filter(
        profile=buyer_profile,
        badge_type=BadgeType.TRUSTED_BUYER,
    ).count() == 1


def test_trusted_seller_counts_completed_deals():
    _, _, _, seller_profile = buyer_and_seller()
    CompletedDealFactory.create_batch(9, seller_profile=seller_profile)

    RewardEngine.evaluate_trusted_seller(seller_profile.pk)
    assert not BadgeAward.objects.filter(badge_type=BadgeType.TRUSTED_SELLER).exists()

    CompletedDealFactory(seller_profile=seller_profile)
    RewardEngine.evaluate_trusted_seller(seller_profile.pk)
    assert BadgeAward.objects.filter(
        profile=seller_profile,
        badge_type=BadgeType.TRUSTED_SELLER,
    ).count() == 1


def test_completion_enqueues_trusted_aggregates_on_commit(
    django_capture_on_commit_callbacks,
):
    deal = CompletedDealFactory()

    with django_capture_on_commit_callbacks() as callbacks:
        result = RewardEngine.handle_event(
            RewardEventType.DEAL_COMPLETED,
            actor=deal.seller_profile,
            context={"deal": deal},
        )

    assert len(callbacks) >= 1
    assert result["deferred_tasks"] == [
        "loyalty.tasks.evaluate_trusted_buyer",
        "loyalty.tasks.evaluate_trusted_seller",
    ]


# --- Snapshot amount and attribution ----------------------------------------


def test_market_amount_reward_uses_deal_snapshot():
    buyer, seller, buyer_profile, seller_profile = buyer_and_seller()
    asset = SupplyFactory(owner=seller_profile, price=Decimal("20.00"))
    item = OrderItemFactory(
        order=OrderFactory(owner=buyer_profile),
        asset=asset,
        seller=seller_profile,
        quantity=2,
        unit_price_snapshot=Decimal("20.00"),
        status=OrderItemStatus.COMPLETED,
    )
    deal = CompletedDealFactory(
        source_id=item.pk,
        buyer_profile=buyer_profile,
        seller_profile=seller_profile,
        asset=asset,
        amount=item.total_price,
    )
    item.unit_price_snapshot = Decimal("500.00")
    item.save(update_fields=["unit_price_snapshot", "modified"])
    asset.price = Decimal("500.00")
    asset.save(update_fields=["price", "modified"])

    fire(deal)

    amount_tx = PointTransaction.objects.get(
        idempotency_key=f"points:order_item:{item.pk}:customer:amount"
    )
    assert amount_tx.points == 40


def test_rewards_are_attributed_to_deal_accounts_and_modes():
    buyer, seller, buyer_profile, seller_profile = buyer_and_seller()
    SellerProfileFactory(user=buyer, is_active=False)
    deal = CompletedDealFactory(buyer_profile=buyer_profile, seller_profile=seller_profile)

    fire(deal)

    buyer_rows = PointTransaction.objects.filter(user=buyer)
    seller_rows = PointTransaction.objects.filter(user=seller)
    assert buyer_rows.count() == 3
    assert set(buyer_rows.values_list("profile_id", flat=True)) == {buyer_profile.pk}
    assert seller_rows.count() == 3
    assert set(seller_rows.values_list("profile_id", flat=True)) == {seller_profile.pk}


# --- Event vocabulary --------------------------------------------------------


def test_account_onboarded_event_awards_onboarding_once_without_badges():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    for _ in range(2):
        RewardEngine.handle_event(
            RewardEventType.ACCOUNT_ONBOARDED,
            actor=user.active_profile,
        )

    assert PointService.get_balance(user) == 100
    assert PointTransaction.objects.filter(user=user).count() == 1
    assert not BadgeAward.objects.exists()


def test_multi_bird_auction_deal_still_grants_single_lucky_wheel_attempt():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    item, buyer, seller = create_sold_auction_item(
        seller=seller,
        paired_bird=BirdFactory(owner=seller.active_profile),
    )
    request = PaymentRequestService.create_for_auction_item(item.pk, buyer.active_profile)

    PaymentRequestService.approve(request.pk, seller.active_profile)

    assert LuckyWheelGrant.objects.filter(
        profile=buyer.active_profile,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
    ).count() == 1
