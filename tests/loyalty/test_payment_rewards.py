from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError

from apps.assets.models import AssetStatus
from apps.auctions.models import AuctionItemStatus
from apps.loyalty.constants import BadgeType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.orders.models import OrderItemStatus, OrderStatus
from apps.orders.services import OrderItemService
from apps.payments.models import (
    MarketPaymentRequest,
)
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BirdFactory,
    OrderFactory,
    OrderItemFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def active_customer(user):
    return CustomerProfile.objects.get(pk=user.active_profile.pk)


def active_seller(user):
    return SellerProfile.objects.get(pk=user.active_profile.pk)


def create_sold_auction_item():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_profile = active_seller(seller)
    buyer_profile = active_customer(buyer)
    bird = BirdFactory(owner=seller_profile)
    auction = AuctionFactory(owner=seller_profile)
    item = AuctionItemFactory(
        auction=auction,
        bird=bird,
        status=AuctionItemStatus.SOLD,
        winner=buyer_profile,
        current_price=Decimal("150.00"),
    )
    return item, buyer, seller


def create_approved_order_item(*, asset=None, quantity=2):
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer_profile = active_customer(buyer)
    seller_profile = active_seller(seller)
    asset = asset or SupplyFactory(owner=seller_profile, count=5)
    order = OrderFactory(owner=buyer_profile, status=OrderStatus.PROCESSING)
    item = OrderItemFactory(
        order=order,
        asset=asset,
        seller=seller_profile,
        quantity=quantity,
        unit_price_snapshot=Decimal("25.00"),
        status=OrderItemStatus.APPROVED,
    )
    return item, buyer, seller, asset


def test_auction_payment_approval_grants_buyer_and_seller_rewards():
    item, buyer, seller = create_sold_auction_item()
    request = PaymentRequestService.create_for_auction_item(
        item.pk,
        buyer.active_profile,
    )

    PaymentRequestService.approve(request.pk, seller.active_profile)

    assert BadgeAward.objects.filter(
        profile=buyer.active_profile,
        badge_type=BadgeType.AUCTION_PURCHASE_CONFIRMED,
    ).exists()
    assert BadgeAward.objects.filter(
        profile=seller.active_profile,
        badge_type=BadgeType.FIRST_AUCTION_SALE_CONFIRMED,
    ).exists()


def test_auction_request_creation_and_rejection_grant_no_sale_rewards():
    item, buyer, seller = create_sold_auction_item()

    request = PaymentRequestService.create_for_auction_item(
        item.pk,
        buyer.active_profile,
    )
    PaymentRequestService.reject(request.pk, seller.active_profile)

    assert not BadgeAward.objects.filter(
        badge_type__in=[
            BadgeType.AUCTION_PURCHASE_CONFIRMED,
            BadgeType.FIRST_AUCTION_SALE_CONFIRMED,
        ]
    ).exists()
    assert PointTransaction.objects.count() == 0


def test_market_payment_approval_grants_buyer_and_seller_rewards():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(
        item.pk,
        buyer.active_profile,
    )

    PaymentRequestService.approve(request.pk, seller.active_profile)

    item.refresh_from_db()
    assert item.status == OrderItemStatus.COMPLETED
    assert BadgeAward.objects.filter(
        profile=buyer.active_profile,
        badge_type=BadgeType.MARKET_PURCHASE_CONFIRMED,
    ).exists()
    assert BadgeAward.objects.filter(
        profile=seller.active_profile,
        badge_type=BadgeType.FIRST_MARKET_SALE_CONFIRMED,
    ).exists()


def test_market_request_creation_and_rejection_grant_no_sale_rewards():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(
        item.pk,
        buyer.active_profile,
    )
    PaymentRequestService.reject(request.pk, seller.active_profile)

    assert not BadgeAward.objects.filter(
        badge_type__in=[
            BadgeType.MARKET_PURCHASE_CONFIRMED,
            BadgeType.FIRST_MARKET_SALE_CONFIRMED,
        ]
    ).exists()
    assert PointTransaction.objects.count() == 0


def test_order_item_seller_approval_alone_grants_no_sale_completion_reward():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer_profile = active_customer(buyer)
    seller_profile = active_seller(seller)
    asset = SupplyFactory(owner=seller_profile, count=5)
    order = OrderFactory(owner=buyer_profile)
    item = OrderItemFactory(
        order=order,
        asset=asset,
        seller=seller_profile,
        status=OrderItemStatus.PENDING_SELLER,
    )

    OrderItemService.approve_item(item, seller_profile)

    assert not BadgeAward.objects.filter(
        badge_type__in=[
            BadgeType.MARKET_PURCHASE_CONFIRMED,
            BadgeType.FIRST_MARKET_SALE_CONFIRMED,
        ]
    ).exists()
    assert PointTransaction.objects.count() == 0


def test_market_stock_failure_does_not_grant_market_sale_rewards():
    item, buyer, seller, asset = create_approved_order_item(quantity=2)
    request = PaymentRequestService.create_for_order_item(
        item.pk,
        buyer.active_profile,
    )
    asset.count = 1
    asset.save(update_fields=["count", "modified"])

    with pytest.raises(ValidationError):
        PaymentRequestService.approve(request.pk, seller.active_profile)

    assert not BadgeAward.objects.filter(
        badge_type__in=[
            BadgeType.MARKET_PURCHASE_CONFIRMED,
            BadgeType.FIRST_MARKET_SALE_CONFIRMED,
        ]
    ).exists()
    assert PointTransaction.objects.count() == 0


def test_repeated_payment_approval_attempt_does_not_duplicate_rewards():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(
        item.pk,
        buyer.active_profile,
    )

    PaymentRequestService.approve(request.pk, seller.active_profile)
    with pytest.raises(ValidationError):
        PaymentRequestService.approve(request.pk, seller.active_profile)

    assert BadgeAward.objects.filter(
        badge_type=BadgeType.MARKET_PURCHASE_CONFIRMED,
    ).count() == 1
    assert PointTransaction.objects.filter(
        source_type="PaymentRequest",
        source_id=str(request.pk),
    ).count() == 2


def test_market_bird_payment_approval_transfers_owner_and_awards_market_reward():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer_profile = active_customer(buyer)
    seller_profile = active_seller(seller)
    bird = BirdFactory(owner=seller_profile, is_market_listed=True)
    order = OrderFactory(owner=buyer_profile, status=OrderStatus.PROCESSING)
    item = OrderItemFactory(
        order=order,
        asset=bird,
        seller=seller_profile,
        quantity=1,
        unit_price_snapshot=Decimal("25.00"),
        status=OrderItemStatus.APPROVED,
    )
    request = MarketPaymentRequest.objects.create(
        buyer=buyer_profile,
        seller=seller_profile,
        asset=bird,
        amount=item.total_price,
        order_item=item,
    )

    PaymentRequestService.approve(request.pk, seller_profile)

    bird.refresh_from_db()
    assert bird.owner_id == buyer_profile.pk
    assert bird.status == AssetStatus.SOLD
    assert BadgeAward.objects.filter(
        profile=buyer_profile,
        badge_type=BadgeType.MARKET_PURCHASE_CONFIRMED,
    ).exists()


def test_market_supply_sale_does_not_create_bird_specific_badge():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(
        item.pk,
        buyer.active_profile,
    )

    PaymentRequestService.approve(request.pk, seller.active_profile)

    assert not BadgeAward.objects.filter(badge_type="BIRD_OWNER_CONFIRMED").exists()


def test_direct_cashback_like_fields_do_not_create_loyalty_transactions():
    item, _, _, _ = create_approved_order_item()
    item.cashback_earned_amount = Decimal("5.00")
    item.cashback_redeemed_amount = Decimal("2.00")
    item.save(
        update_fields=[
            "cashback_earned_amount",
            "cashback_redeemed_amount",
            "modified",
        ]
    )

    assert PointTransaction.objects.count() == 0
