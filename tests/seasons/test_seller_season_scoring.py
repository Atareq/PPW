from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auctions.models import (
    AuctionEvent,
    AuctionItemStatus,
    AuctionStatus,
    AuctionType,
)
from apps.auctions.services import AuctionService
from apps.authentication.services import build_token_for_user
from apps.chat.models import Conversation, Message
from apps.deals.models import (
    CompletedDeal,
    CompletedDealSourceType,
    CompletedDealType,
)
from apps.loyalty.constants import RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.services import PointService
from apps.notifications.models import Notification
from apps.orders.models import (
    Order,
    OrderEvent,
    OrderEventType,
    OrderItem,
    OrderItemStatus,
    OrderStatus,
)
from apps.orders.services import OrderItemService, OrderService
from apps.payments.models import MarketPaymentRequest, PaymentRequestStatus
from apps.payments.services import PaymentRequestService
from apps.ratings.models import DealRating, DealRatingDirection, SellerRating
from apps.seasons.models import Season, SeasonSellerInsight, SeasonStatus
from apps.seasons.services import (
    INTERACTION_WEIGHT,
    INTERACTION_WEIGHT_PERCENT,
    RATING_WEIGHT,
    RATING_WEIGHT_PERCENT,
    RELIABILITY_WEIGHT,
    RELIABILITY_WEIGHT_PERCENT,
    SALES_WEIGHT,
    SALES_WEIGHT_PERCENT,
    TOTAL_WEIGHT_PERCENT,
    SeasonScoreService,
    SeasonService,
)
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BirdFactory,
    CompletedDealFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    return client


def make_season(*, starts_at=None, ends_at=None, status=SeasonStatus.DRAFT):
    now = timezone.now()
    starts_at = starts_at or (now - timedelta(days=30))
    ends_at = ends_at or now
    return Season.objects.create(
        title="Seller Scoring Season",
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
    )


def make_seller():
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    return user.active_profile


def make_customer():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    return user.active_profile


def create_deal(
    *,
    seller,
    buyer=None,
    deal_type=CompletedDealType.MARKET,
    completed_at=None,
    amount=Decimal("100.00"),
):
    buyer = buyer or make_customer()
    completed_at = completed_at or (timezone.now() - timedelta(days=5))
    source_type = (
        CompletedDealSourceType.ORDER_ITEM
        if deal_type == CompletedDealType.MARKET
        else CompletedDealSourceType.AUCTION_ITEM
    )
    return CompletedDealFactory(
        seller_user=seller.user,
        seller_profile=seller,
        buyer_user=buyer.user,
        buyer_profile=buyer,
        deal_type=deal_type,
        source_type=source_type,
        amount=amount,
        completed_at=completed_at,
    )


def create_deal_rating(
    *,
    deal,
    stars=5,
    direction=DealRatingDirection.BUYER_TO_SELLER,
    created_at=None,
):
    if direction == DealRatingDirection.BUYER_TO_SELLER:
        rater_profile = deal.buyer_profile
        rated_profile = deal.seller_profile
    else:
        rater_profile = deal.seller_profile
        rated_profile = deal.buyer_profile

    rating = DealRating.objects.create(
        deal=deal,
        direction=direction,
        rater_profile=rater_profile,
        rated_profile=rated_profile,
        stars=stars,
        comment="Season rating",
    )
    if created_at is not None:
        DealRating.objects.filter(pk=rating.pk).update(created=created_at)
        rating.refresh_from_db()
    return rating


def send_chat_message(*, customer, seller, sender, body="Hello", created_at=None):
    conversation, _ = Conversation.objects.get_or_create(
        customer=customer,
        seller=seller,
        defaults={"created_by": sender},
    )
    receiver = seller if sender.pk == customer.pk else customer
    msg = Message.objects.create(
        conversation=conversation,
        sender=sender,
        receiver=receiver,
        body=body,
    )
    if created_at is not None:
        Message.objects.filter(pk=msg.pk).update(created=created_at)
        msg.refresh_from_db()
    return msg


# ============================================================================
# TEST 1 — Weight total
# ============================================================================


def test_01_weights_sum_to_100():
    assert (
        SALES_WEIGHT_PERCENT
        + RATING_WEIGHT_PERCENT
        + RELIABILITY_WEIGHT_PERCENT
        + INTERACTION_WEIGHT_PERCENT
        == 100
    )
    assert TOTAL_WEIGHT_PERCENT == 100
    assert (
        SALES_WEIGHT + RATING_WEIGHT + RELIABILITY_WEIGHT + INTERACTION_WEIGHT
        == Decimal("1.00")
    )


# ============================================================================
# TEST 2 & TEST 3 — Participation & removal of legacy 10-sale threshold
# ============================================================================


def test_02_seller_with_one_completed_deal_participates_and_zero_excluded():
    season = make_season()
    seller_with_deal = make_seller()
    seller_without_deal = make_seller()

    create_deal(
        seller=seller_with_deal,
        completed_at=season.starts_at + timedelta(days=1),
    )

    insights = SeasonService.calculate_winners(season)

    assert len(insights) == 1
    assert insights[0].seller_id == seller_with_deal.pk
    assert not SeasonSellerInsight.objects.filter(
        season=season,
        seller=seller_without_deal,
    ).exists()


def test_03_legacy_10_sale_threshold_removed():
    season = make_season()
    seller = make_seller()
    for day in range(1, 4):
        create_deal(
            seller=seller,
            completed_at=season.starts_at + timedelta(days=day),
        )

    insights = SeasonService.calculate_winners(season)

    assert len(insights) == 1
    assert insights[0].completed_sales_count == 3
    assert insights[0].sales_score == Decimal("100.00")


# ============================================================================
# TEST 4, 5, 6, 7 — Completed sales (market, auction, window, normalization)
# ============================================================================


def test_04_market_completed_deal_counts():
    season = make_season()
    seller = make_seller()
    create_deal(
        seller=seller,
        deal_type=CompletedDealType.MARKET,
        completed_at=season.starts_at + timedelta(days=2),
    )

    insights = SeasonService.calculate_winners(season)

    assert len(insights) == 1
    assert insights[0].completed_sales_count == 1


def test_05_auction_completed_deal_counts():
    season = make_season()
    seller = make_seller()
    create_deal(
        seller=seller,
        deal_type=CompletedDealType.AUCTION,
        completed_at=season.starts_at + timedelta(days=2),
    )

    insights = SeasonService.calculate_winners(season)

    assert len(insights) == 1
    assert insights[0].completed_sales_count == 1


def test_06_outside_season_deals_excluded_and_half_open_boundaries_respected():
    season = make_season()
    seller_in = make_seller()
    seller_out = make_seller()

    # Exact start boundary is included: starts_at <= completed_at < ends_at
    create_deal(seller=seller_in, completed_at=season.starts_at)
    # Before starts_at -> excluded
    create_deal(
        seller=seller_in,
        completed_at=season.starts_at - timedelta(seconds=1),
    )
    # Exact ends_at -> excluded
    create_deal(seller=seller_in, completed_at=season.ends_at)
    create_deal(seller=seller_out, completed_at=season.ends_at)

    insights = SeasonService.calculate_winners(season)

    assert len(insights) == 1
    assert insights[0].seller_id == seller_in.pk
    assert insights[0].completed_sales_count == 1


def test_07_sales_normalization_relative_to_season_max():
    season = make_season()
    seller_a = make_seller()
    seller_b = make_seller()

    for i in range(5):
        create_deal(
            seller=seller_a,
            completed_at=season.starts_at + timedelta(days=1, minutes=i),
        )
    for i in range(10):
        create_deal(
            seller=seller_b,
            completed_at=season.starts_at + timedelta(days=2, minutes=i),
        )

    insights = {
        insight.seller_id: insight
        for insight in SeasonService.calculate_winners(season)
    }

    assert insights[seller_a.pk].completed_sales_count == 5
    assert insights[seller_a.pk].sales_score == Decimal("50.00")
    assert insights[seller_b.pk].completed_sales_count == 10
    assert insights[seller_b.pk].sales_score == Decimal("100.00")


# ============================================================================
# TEST 8–14 — Buyer ratings (DealRating BUYER_TO_SELLER only)
# ============================================================================


def test_08_deal_rating_buyer_to_seller_affects_rating_component():
    season = make_season()
    seller = make_seller()
    deal = create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )
    create_deal_rating(
        deal=deal,
        stars=5,
        direction=DealRatingDirection.BUYER_TO_SELLER,
        created_at=season.starts_at + timedelta(days=3),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.ratings_count == 1
    assert insight.avg_rating == Decimal("5.00")
    assert insight.rating_score == Decimal("100.00")


def test_09_legacy_seller_rating_is_ignored():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=2),
    )
    SellerRating.objects.create(owner=buyer, seller=seller, stars=5)
    seller.avg_rating = Decimal("5.00")
    seller.ratings_count = 1
    seller.save(update_fields=["avg_rating", "ratings_count", "modified"])

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.ratings_count == 0
    assert insight.avg_rating == Decimal("0.00")
    assert insight.rating_score == Decimal("0.00")


def test_10_deal_rating_seller_to_buyer_is_ignored():
    season = make_season()
    seller = make_seller()
    deal = create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )
    create_deal_rating(
        deal=deal,
        stars=5,
        direction=DealRatingDirection.SELLER_TO_BUYER,
        created_at=season.starts_at + timedelta(days=3),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.ratings_count == 0
    assert insight.avg_rating == Decimal("0.00")
    assert insight.rating_score == Decimal("0.00")


def test_11_rating_for_deal_completed_outside_season_is_excluded():
    season = make_season()
    seller = make_seller()
    create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )
    old_deal = create_deal(
        seller=seller,
        completed_at=season.starts_at - timedelta(days=1),
    )
    create_deal_rating(
        deal=old_deal,
        stars=5,
        direction=DealRatingDirection.BUYER_TO_SELLER,
        created_at=season.starts_at + timedelta(days=3),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.ratings_count == 0
    assert insight.rating_score == Decimal("0.00")


def test_12_rating_created_outside_season_is_excluded():
    season = make_season()
    seller = make_seller()
    deal = create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )
    create_deal_rating(
        deal=deal,
        stars=5,
        direction=DealRatingDirection.BUYER_TO_SELLER,
        created_at=season.ends_at,
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.ratings_count == 0
    assert insight.rating_score == Decimal("0.00")


def test_13_no_ratings_gives_zero_rating_score():
    season = make_season()
    seller = make_seller()
    create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.ratings_count == 0
    assert insight.avg_rating == Decimal("0.00")
    assert insight.rating_score == Decimal("0.00")


def test_14_average_rating_4_5_gives_rating_score_90():
    season = make_season()
    seller = make_seller()
    deal_1 = create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )
    deal_2 = create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=3),
    )
    create_deal_rating(
        deal=deal_1,
        stars=5,
        created_at=season.starts_at + timedelta(days=4),
    )
    create_deal_rating(
        deal=deal_2,
        stars=4,
        created_at=season.starts_at + timedelta(days=4),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.ratings_count == 2
    assert insight.avg_rating == Decimal("4.50")
    assert insight.rating_score == Decimal("90.00")


# ============================================================================
# TEST 15–22 — Seller reliability (cancellations)
# ============================================================================


def test_15_zero_cancellations_gives_100_reliability():
    season = make_season()
    seller = make_seller()
    for i in range(10):
        create_deal(
            seller=seller,
            completed_at=season.starts_at + timedelta(days=1, minutes=i),
        )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.seller_cancellations_count == 0
    assert insight.reliability_score == Decimal("100.00")


def test_16_nine_completed_and_one_seller_cancellation_gives_90_reliability():
    season = make_season()
    seller = make_seller()
    customer = make_customer()
    for i in range(9):
        create_deal(
            seller=seller,
            completed_at=season.starts_at + timedelta(days=1, minutes=i),
        )

    asset = SupplyFactory(owner=seller, count=5)
    order = OrderService.create_order(
        customer,
        [{"asset_id": asset.pk, "quantity": 1}],
    )
    item = order.items.get()
    OrderItemService.reject_item(item, seller)
    OrderItem.objects.filter(pk=item.pk).update(
        modified=season.starts_at + timedelta(days=2)
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.completed_sales_count == 9
    assert insight.seller_cancellations_count == 1
    assert insight.reliability_score == Decimal("90.00")


def test_17_market_seller_rejection_counts():
    season = make_season()
    seller = make_seller()
    customer = make_customer()
    create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=1),
    )

    asset = SupplyFactory(owner=seller, count=5)
    order = OrderService.create_order(
        customer,
        [{"asset_id": asset.pk, "quantity": 1}],
    )
    item = order.items.get()
    OrderItemService.reject_item(item, seller)
    OrderItem.objects.filter(pk=item.pk).update(
        modified=season.starts_at + timedelta(days=2)
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.seller_cancellations_count == 1
    assert insight.reliability_score == Decimal("50.00")


def test_18_market_seller_timeout_auto_cancellation_counts():
    season = make_season()
    seller = make_seller()
    customer = make_customer()
    create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=1),
    )

    asset = SupplyFactory(owner=seller, count=5)
    order = OrderService.create_order(
        customer,
        [{"asset_id": asset.pk, "quantity": 1}],
    )
    item = order.items.get()
    OrderItem.objects.filter(pk=item.pk).update(
        created=timezone.now() - timedelta(days=10)
    )
    OrderItemService.cancel_expired_pending_items(timeout_days=3)
    in_window = season.starts_at + timedelta(days=5)
    OrderItem.objects.filter(pk=item.pk).update(modified=in_window)
    OrderEvent.objects.filter(
        item=item,
        event_type=OrderEventType.SELLER_AUTO_CANCELLED,
    ).update(created=in_window)

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.seller_cancellations_count == 1
    assert insight.reliability_score == Decimal("50.00")


def test_19_seller_auction_cancellation_counts_business_units():
    season = make_season()
    seller = make_seller()
    for i in range(7):
        create_deal(
            seller=seller,
            completed_at=season.starts_at + timedelta(days=1, minutes=i),
        )

    auction = AuctionFactory(
        owner=seller,
        auction_type=AuctionType.MULTI,
        status=AuctionStatus.ACTIVE,
        start_time=season.starts_at + timedelta(days=1),
        end_time=season.starts_at + timedelta(days=10),
    )
    for _ in range(3):
        AuctionItemFactory(
            auction=auction,
            bird=BirdFactory(owner=seller),
            status=AuctionItemStatus.ACTIVE,
        )

    AuctionService.cancel_auction(auction)
    in_window = season.starts_at + timedelta(days=3)
    AuctionEvent.objects.filter(
        auction=auction,
        event_type=AuctionItemStatus.CANCELLED,
    ).update(created=in_window)

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.seller_cancellations_count == 3
    assert insight.reliability_score == Decimal("70.00")


def test_20_buyer_cancellation_excluded_from_seller_reliability():
    season = make_season()
    seller = make_seller()
    customer = make_customer()
    create_deal(
        seller=seller,
        buyer=customer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    asset = SupplyFactory(owner=seller, count=5)
    order = OrderService.create_order(
        customer,
        [{"asset_id": asset.pk, "quantity": 1}],
    )
    in_window = season.starts_at + timedelta(days=2)
    Order.objects.filter(pk=order.pk).update(
        status=OrderStatus.CANCELLED,
        modified=in_window,
    )
    OrderItem.objects.filter(order=order).update(
        status=OrderItemStatus.CANCELLED,
        modified=in_window,
    )

    auction = AuctionFactory(owner=seller, status=AuctionStatus.CANCELLED)
    AuctionItemFactory(
        auction=auction,
        bird=BirdFactory(owner=seller),
        status=AuctionItemStatus.CANCELLED,
    )
    ev = AuctionEvent.objects.create(
        auction=auction,
        event_type=AuctionItemStatus.CANCELLED,
        performer=customer,
        metadata={"reason": "Cancelled by buyer"},
    )
    AuctionEvent.objects.filter(pk=ev.pk).update(created=in_window)

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.seller_cancellations_count == 0
    assert insight.reliability_score == Decimal("100.00")


def test_21_admin_cancellation_excluded_from_seller_reliability():
    season = make_season()
    seller = make_seller()
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER).active_profile
    create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=1),
    )

    in_window = season.starts_at + timedelta(days=2)

    auction_1 = AuctionFactory(owner=seller, status=AuctionStatus.ACTIVE)
    AuctionItemFactory(
        auction=auction_1,
        bird=BirdFactory(owner=seller),
        status=AuctionItemStatus.ACTIVE,
    )
    AuctionService.cancel_auction(
        auction_1,
        reason="Cancelled by manager",
        performer=manager,
    )
    AuctionEvent.objects.filter(auction=auction_1).update(created=in_window)

    auction_2 = AuctionFactory(owner=seller, status=AuctionStatus.ACTIVE)
    AuctionItemFactory(
        auction=auction_2,
        bird=BirdFactory(owner=seller),
        status=AuctionItemStatus.ACTIVE,
    )
    AuctionService.close_auction(auction_2, performer=manager)

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.seller_cancellations_count == 0
    assert insight.reliability_score == Decimal("100.00")


def test_22_payment_request_rejection_excluded_from_seller_reliability():
    season = make_season()
    seller = make_seller()
    customer = make_customer()
    create_deal(
        seller=seller,
        buyer=customer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    asset = SupplyFactory(owner=seller, count=5)
    order = OrderService.create_order(
        customer,
        [{"asset_id": asset.pk, "quantity": 1}],
    )
    item = order.items.get()
    OrderItemService.approve_item(item, seller)
    pr = MarketPaymentRequest.objects.create(
        buyer=customer,
        seller=seller,
        asset=asset,
        amount=item.total_price,
        status=PaymentRequestStatus.PENDING,
        order_item=item,
    )
    PaymentRequestService.reject(pr.pk, seller, seller_note="Invalid receipt")

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.seller_cancellations_count == 0
    assert insight.reliability_score == Decimal("100.00")


# ============================================================================
# TEST 23–32 — Buyer interaction (verified-buyer reply rate)
# ============================================================================


def test_23_verified_buyer_message_and_seller_reply_counts():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=buyer,
        body="When can I pick up?",
        created_at=season.starts_at + timedelta(days=2),
    )
    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=seller,
        body="Tomorrow morning works.",
        created_at=season.starts_at + timedelta(days=2, hours=1),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 1
    assert insight.interaction_replied_count == 1
    assert insight.interaction_score == Decimal("100.00")


def test_24_verified_buyer_message_without_seller_reply():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=buyer,
        body="Hello?",
        created_at=season.starts_at + timedelta(days=2),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 1
    assert insight.interaction_replied_count == 0
    assert insight.interaction_score == Decimal("0.00")


def test_25_repeated_messages_from_same_buyer_count_as_one_opportunity():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    for i in range(10):
        send_chat_message(
            customer=buyer,
            seller=seller,
            sender=buyer,
            body=f"Msg {i}",
            created_at=season.starts_at + timedelta(days=2, minutes=i),
        )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 1
    assert insight.interaction_replied_count == 0


def test_26_repeated_seller_replies_count_as_one_replied_buyer():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=buyer,
        body="Question",
        created_at=season.starts_at + timedelta(days=2),
    )
    for i in range(5):
        send_chat_message(
            customer=buyer,
            seller=seller,
            sender=seller,
            body=f"Reply {i}",
            created_at=season.starts_at + timedelta(days=2, minutes=10 + i),
        )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 1
    assert insight.interaction_replied_count == 1
    assert insight.interaction_score == Decimal("100.00")


def test_27_buyer_without_season_completed_deal_does_not_count():
    season = make_season()
    seller = make_seller()
    verified_buyer = make_customer()
    unverified_buyer = make_customer()

    create_deal(
        seller=seller,
        buyer=verified_buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )
    create_deal(
        seller=seller,
        buyer=unverified_buyer,
        completed_at=season.starts_at - timedelta(days=5),
    )

    send_chat_message(
        customer=unverified_buyer,
        seller=seller,
        sender=unverified_buyer,
        body="Hi",
        created_at=season.starts_at + timedelta(days=2),
    )
    send_chat_message(
        customer=unverified_buyer,
        seller=seller,
        sender=seller,
        body="Hello",
        created_at=season.starts_at + timedelta(days=2, hours=1),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 0
    assert insight.interaction_replied_count == 0
    assert insight.interaction_score == Decimal("0.00")


def test_28_seller_initiated_message_without_buyer_message_does_not_count():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=seller,
        body="Thanks for buying!",
        created_at=season.starts_at + timedelta(days=2),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 0
    assert insight.interaction_replied_count == 0
    assert insight.interaction_score == Decimal("0.00")


def test_29_seller_message_before_buyer_first_message_does_not_count_as_reply():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=seller,
        body="Initial note from seller",
        created_at=season.starts_at + timedelta(days=2),
    )
    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=buyer,
        body="Buyer follow-up question",
        created_at=season.starts_at + timedelta(days=3),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 1
    assert insight.interaction_replied_count == 0
    assert insight.interaction_score == Decimal("0.00")


def test_30_messages_outside_season_window_excluded():
    season = make_season()
    seller = make_seller()
    buyer = make_customer()
    create_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=1),
    )

    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=buyer,
        body="In-season question",
        created_at=season.starts_at + timedelta(days=2),
    )
    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=seller,
        body="Late reply at ends_at",
        created_at=season.ends_at,
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 1
    assert insight.interaction_replied_count == 0
    assert insight.interaction_score == Decimal("0.00")


def test_31_interaction_formula_10_opportunities_8_replied_scores_80():
    season = make_season()
    seller = make_seller()

    for idx in range(10):
        buyer = make_customer()
        create_deal(
            seller=seller,
            buyer=buyer,
            completed_at=season.starts_at + timedelta(days=1, minutes=idx),
        )
        send_chat_message(
            customer=buyer,
            seller=seller,
            sender=buyer,
            body="Question",
            created_at=season.starts_at + timedelta(days=2, minutes=idx),
        )
        if idx < 8:
            send_chat_message(
                customer=buyer,
                seller=seller,
                sender=seller,
                body="Answer",
                created_at=season.starts_at + timedelta(days=3, minutes=idx),
            )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 10
    assert insight.interaction_replied_count == 8
    assert insight.interaction_score == Decimal("80.00")


def test_32_no_interaction_opportunities_scores_zero():
    season = make_season()
    seller = make_seller()
    create_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=1),
    )

    insight = SeasonService.calculate_winners(season)[0]

    assert insight.interaction_opportunities_count == 0
    assert insight.interaction_replied_count == 0
    assert insight.interaction_score == Decimal("0.00")


# ============================================================================
# TEST 33, 34, 35 — Composite formula, ROUND_HALF_UP, deterministic tie-break
# ============================================================================


def test_33_exact_composite_score_50_90_100_80_equals_75():
    season = make_season()
    seller_a = make_seller()
    seller_b = make_seller()

    for i in range(10):
        create_deal(
            seller=seller_b,
            completed_at=season.starts_at + timedelta(days=1, minutes=i),
        )

    deals_a = []
    for i in range(5):
        buyer = make_customer()
        deal = create_deal(
            seller=seller_a,
            buyer=buyer,
            completed_at=season.starts_at + timedelta(days=2, minutes=i),
        )
        deals_a.append((deal, buyer))
        send_chat_message(
            customer=buyer,
            seller=seller_a,
            sender=buyer,
            body="Hello",
            created_at=season.starts_at + timedelta(days=3, minutes=i),
        )
        if i < 4:
            send_chat_message(
                customer=buyer,
                seller=seller_a,
                sender=seller_a,
                body="Reply",
                created_at=season.starts_at + timedelta(days=4, minutes=i),
            )

    create_deal_rating(
        deal=deals_a[0][0],
        stars=5,
        created_at=season.starts_at + timedelta(days=5),
    )
    create_deal_rating(
        deal=deals_a[1][0],
        stars=4,
        created_at=season.starts_at + timedelta(days=5),
    )

    insights = {
        insight.seller_id: insight
        for insight in SeasonService.calculate_winners(season)
    }
    insight_a = insights[seller_a.pk]

    assert insight_a.sales_score == Decimal("50.00")
    assert insight_a.rating_score == Decimal("90.00")
    assert insight_a.reliability_score == Decimal("100.00")
    assert insight_a.interaction_score == Decimal("80.00")
    assert insight_a.season_score == Decimal("75.00")


def test_34_decimal_round_half_up_to_two_decimal_places():
    score = SeasonScoreService.compute_weighted_score(
        sales_score=Decimal("12.5125"),
        rating_score=Decimal("0"),
        reliability_score=Decimal("0"),
        interaction_score=Decimal("0"),
    )
    assert score == Decimal("5.01")


def test_35_deterministic_tie_break_orders_by_seller_profile_id_asc():
    season = make_season()
    seller_1 = make_seller()
    seller_2 = make_seller()
    low_seller, high_seller = sorted([seller_1, seller_2], key=lambda s: s.pk)

    high_seller.avg_rating = Decimal("5.00")
    high_seller.save(update_fields=["avg_rating", "modified"])

    create_deal(
        seller=high_seller,
        amount=Decimal("9999.00"),
        completed_at=season.starts_at + timedelta(days=2),
    )
    create_deal(
        seller=low_seller,
        amount=Decimal("10.00"),
        completed_at=season.starts_at + timedelta(days=2),
    )

    insights = SeasonService.calculate_winners(season)

    assert len(insights) == 2
    assert insights[0].season_score == insights[1].season_score == Decimal("60.00")
    assert insights[0].seller_id == low_seller.pk
    assert insights[0].rank == 1
    assert insights[1].seller_id == high_seller.pk
    assert insights[1].rank == 2


# ============================================================================
# TEST 36–40 — Pure calculation (0 PointTransaction, 0 badges/prizes/notifications)
# ============================================================================


def test_36_calculation_creates_zero_point_transactions_badges_or_notifications():
    season = make_season()
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    for i in range(4):
        seller = make_seller()
        create_deal(
            seller=seller,
            completed_at=season.starts_at + timedelta(days=1, minutes=i),
        )

    tx_before = PointTransaction.objects.count()
    badges_before = BadgeAward.objects.count()
    notif_before = Notification.objects.count()

    SeasonService.calculate_winners(season)
    SeasonService.calculate_scores(season)
    SeasonService.recalculate(season)

    client = authenticated_client(manager)
    resp_calc = client.post(f"/api/seasons/{season.pk}/calculate-winners/")
    resp_list = client.get(f"/api/seasons/{season.pk}/seller-insights/")

    assert resp_calc.status_code == 200
    assert resp_list.status_code == 200
    assert PointTransaction.objects.count() == tx_before
    assert BadgeAward.objects.count() == badges_before
    assert Notification.objects.count() == notif_before


def test_37_rank_change_on_recalculation_creates_zero_point_transactions():
    season = make_season()
    seller_a = make_seller()
    seller_b = make_seller()

    create_deal(seller=seller_a, completed_at=season.starts_at + timedelta(days=1))
    create_deal(seller=seller_a, completed_at=season.starts_at + timedelta(days=2))
    create_deal(seller=seller_b, completed_at=season.starts_at + timedelta(days=1))

    tx_before = PointTransaction.objects.count()
    first_run = {
        i.seller_id: i.rank for i in SeasonService.calculate_winners(season)
    }
    assert first_run[seller_a.pk] == 1
    assert first_run[seller_b.pk] == 2

    for d in range(3, 6):
        create_deal(seller=seller_b, completed_at=season.starts_at + timedelta(days=d))

    second_run = {
        i.seller_id: i.rank for i in SeasonService.calculate_winners(season)
    }
    assert second_run[seller_b.pk] == 1
    assert second_run[seller_a.pk] == 2
    assert PointTransaction.objects.count() == tx_before


def test_38_repeated_calculation_is_idempotent():
    season = make_season()
    seller_1 = make_seller()
    seller_2 = make_seller()
    create_deal(seller=seller_1, completed_at=season.starts_at + timedelta(days=1))
    create_deal(seller=seller_1, completed_at=season.starts_at + timedelta(days=2))
    create_deal(seller=seller_2, completed_at=season.starts_at + timedelta(days=1))

    run_1 = SeasonService.calculate_winners(season)
    run_2 = SeasonService.calculate_winners(season)
    run_3 = SeasonService.calculate_winners(season)

    assert SeasonSellerInsight.all_objects.filter(season=season).count() == 2
    assert [(i.seller_id, i.season_score, i.rank) for i in run_1] == [
        (i.seller_id, i.season_score, i.rank) for i in run_2
    ] == [(i.seller_id, i.season_score, i.rank) for i in run_3]
    assert PointTransaction.objects.count() == 0


def test_39_historical_point_transaction_ledger_remains_untouched():
    season = make_season()
    seller = make_seller()
    create_deal(seller=seller, completed_at=season.starts_at + timedelta(days=1))

    historical_tx = PointService.earn_points(
        user=seller.user,
        profile=seller,
        points=100000,
        reason="Season winner rank 1",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"legacy:season:{season.pk}:seller:{seller.pk}:rank:1",
    )
    balance_before = PointService.get_balance(seller.user)

    SeasonService.calculate_winners(season)

    historical_tx.refresh_from_db()
    assert historical_tx.points == 100000
    assert PointService.get_balance(seller.user) == balance_before
    assert (
        PointTransaction.objects.filter(
            source_type=RewardSourceType.SEASON_WINNER
        ).count()
        == 1
    )


def test_40_old_100k_50k_25k_payout_removed_from_calculation():
    season = make_season()
    sellers = [make_seller() for _ in range(3)]
    for idx, seller in enumerate(sellers, start=1):
        for n in range(idx):
            create_deal(
                seller=seller,
                completed_at=season.starts_at + timedelta(days=1, minutes=n),
            )

    insights = SeasonService.calculate_winners(season)

    assert len(insights) == 3
    for insight in insights:
        assert insight.points_awarded == 0
        assert PointService.get_balance(insight.seller.user) == 0
    assert not PointTransaction.objects.filter(
        source_type=RewardSourceType.SEASON_WINNER
    ).exists()


# ============================================================================
# TEST 41 — Leaderboard / API metrics exposed
# ============================================================================


def test_41_season_calculate_and_insights_api_expose_all_component_fields():
    season = make_season()
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    seller = make_seller()
    buyer = make_customer()

    deal = create_deal(
        seller=seller,
        buyer=buyer,
        amount=Decimal("250.00"),
        completed_at=season.starts_at + timedelta(days=2),
    )
    create_deal_rating(
        deal=deal,
        stars=5,
        created_at=season.starts_at + timedelta(days=3),
    )
    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=buyer,
        body="Hi",
        created_at=season.starts_at + timedelta(days=4),
    )
    send_chat_message(
        customer=buyer,
        seller=seller,
        sender=seller,
        body="Hello!",
        created_at=season.starts_at + timedelta(days=4, minutes=5),
    )

    client = authenticated_client(manager)
    calc_response = client.post(f"/api/seasons/{season.pk}/calculate-winners/")
    list_response = client.get(f"/api/seasons/{season.pk}/seller-insights/")

    assert calc_response.status_code == 200
    assert list_response.status_code == 200
    for payload in (calc_response.data[0], list_response.data[0]):
        assert payload["seller_id"] == seller.pk
        assert payload["completed_sales_count"] == 1
        assert Decimal(payload["sales_score"]) == Decimal("100.00")
        assert payload["ratings_count"] == 1
        assert Decimal(payload["avg_rating"]) == Decimal("5.00")
        assert Decimal(payload["rating_score"]) == Decimal("100.00")
        assert payload["seller_cancellations_count"] == 0
        assert Decimal(payload["reliability_score"]) == Decimal("100.00")
        assert payload["interaction_opportunities_count"] == 1
        assert payload["interaction_replied_count"] == 1
        assert Decimal(payload["interaction_score"]) == Decimal("100.00")
        assert Decimal(payload["season_score"]) == Decimal("100.00")
        assert payload["rank"] == 1
        assert payload["completed_orders_count"] == 1
        assert Decimal(payload["total_sales_amount"]) == Decimal("250.00")
        assert payload["points_awarded"] == 0


# ============================================================================
# TEST 42 & TEST 43 — BE-REF and BE-REW regression smoke checks
# ============================================================================


def test_42_be_ref_regression():
    from apps.auctions.services import BiddingService
    from apps.referrals.models import (
        LinkSharingCode,
        ReferralChallenge,
        ReferralStatus,
    )
    from apps.referrals.services import ReferralChallengeService, ReferralService

    now = timezone.now()
    inviter = make_customer()
    referred = make_customer()
    manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)

    code = LinkSharingCode.objects.create(
        owner_profile=inviter,
        code="SEA01R",
        expires_at=now + timedelta(hours=2),
    )
    referral = ReferralService.redeem_code(referred, "SEA01R")
    assert referral.status == ReferralStatus.PENDING

    ReferralService.approve(referral.pk, manager_user)
    referral.refresh_from_db()
    assert referral.status == ReferralStatus.APPROVED

    seller = make_seller()
    auction = AuctionFactory(
        owner=seller,
        status=AuctionStatus.ACTIVE,
        start_time=now - timedelta(hours=1),
        end_time=now + timedelta(hours=2),
    )
    item = AuctionItemFactory(
        auction=auction,
        bird=BirdFactory(owner=seller),
        status=AuctionItemStatus.ACTIVE,
        starting_price=Decimal("50.00"),
    )
    BiddingService.place_bid(item=item, bidder=referred, amount=Decimal("60.00"))
    assert PointTransaction.objects.filter(
        user=inviter.user,
        source_type__in=[
            RewardSourceType.REFERRAL_APPROVED,
            RewardSourceType.REFERRAL_FIRST_BID,
        ],
    ).count() == 2

    challenge = ReferralChallenge.objects.create(
        name="Challenge Regression",
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=5),
    )
    board = ReferralChallengeService.leaderboard(challenge)
    assert len(board) == 1
    assert board[0]["user_id"] == inviter.user_id


def test_43_be_rew_regression():
    from apps.ratings.services import DealRatingService

    seller = make_seller()
    customer = make_customer()
    asset = SupplyFactory(owner=seller, price=Decimal("100.00"), count=5)
    order = OrderService.create_order(
        customer,
        [{"asset_id": asset.pk, "quantity": 1}],
    )
    item = order.items.get()
    OrderItemService.approve_item(item, seller)
    pr = MarketPaymentRequest.objects.create(
        buyer=customer,
        seller=seller,
        asset=asset,
        amount=item.total_price,
        status=PaymentRequestStatus.PENDING,
        order_item=item,
    )
    PaymentRequestService.approve(pr.pk, seller)
    deal = CompletedDeal.objects.get(
        source_type=CompletedDealSourceType.ORDER_ITEM,
        source_id=item.pk,
    )

    DealRatingService.create(customer, deal.pk, stars=5, comment="Great seller")
    DealRatingService.create(seller, deal.pk, stars=5, comment="Great buyer")

    assert PointTransaction.objects.filter(
        user=seller.user,
        source_type=RewardSourceType.SUCCESSFUL_SALE,
    ).exists()
    assert PointTransaction.objects.filter(
        user=customer.user,
        source_type=RewardSourceType.DEAL_COMPLETED,
    ).exists()
    assert PointTransaction.objects.filter(
        user=seller.user,
        source_type=RewardSourceType.MUTUAL_RATING,
    ).exists()
    assert PointTransaction.objects.filter(
        user=customer.user,
        source_type=RewardSourceType.MUTUAL_RATING,
    ).exists()
