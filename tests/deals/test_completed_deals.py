"""BE-REW-04: CompletedDeal is the canonical record of a completed buyer/seller deal."""

from decimal import Decimal

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from rest_framework.exceptions import ValidationError

from apps.deals.models import CompletedDeal, CompletedDealSourceType, CompletedDealType
from apps.deals.services import CompletedDealService
from apps.deals.validators import CompletedDealValidator
from apps.loyalty.constants import BadgeType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.orders.models import OrderItemStatus
from apps.orders.services import OrderItemService
from apps.payments.models import PaymentRequestStatus
from apps.payments.services import PaymentRequestService
from apps.users.constants import ProfileType
from tests.factories import (
    BirdFactory,
    OrderFactory,
    OrderItemFactory,
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


def complete_market_deal(**kwargs):
    item, buyer, seller, asset = create_approved_order_item(**kwargs)
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    request = PaymentRequestService.approve(request.pk, seller.active_profile)
    item.refresh_from_db()
    return item, buyer, seller, asset, request


def complete_auction_deal(**kwargs):
    item, buyer, seller = create_sold_auction_item(**kwargs)
    request = PaymentRequestService.create_for_auction_item(item.pk, buyer.active_profile)
    request = PaymentRequestService.approve(request.pk, seller.active_profile)
    item.refresh_from_db()
    return item, buyer, seller, request


# --- Completion creates exactly one deal -----------------------------------


def test_market_completion_creates_completed_deal():
    item, buyer, seller, asset, request = complete_market_deal()

    deal = CompletedDeal.objects.get()
    assert item.status == OrderItemStatus.COMPLETED
    assert deal.deal_type == CompletedDealType.MARKET
    assert deal.source_type == CompletedDealSourceType.ORDER_ITEM
    assert deal.source_id == item.pk
    assert deal.buyer_user_id == buyer.pk
    assert deal.buyer_profile_id == buyer.active_profile.pk
    assert deal.seller_user_id == seller.pk
    assert deal.seller_profile_id == seller.active_profile.pk
    assert deal.asset_id == asset.pk
    assert deal.amount == item.total_price == request.amount == Decimal("50.00")
    assert deal.currency == active_seller(seller).currency
    assert deal.completed_at == request.approved_at
    assert deal.metadata == {
        "order_id": item.order_id,
        "quantity": 2,
        "payment_request_id": request.pk,
    }


def test_auction_completion_creates_completed_deal():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    paired_bird = BirdFactory(owner=seller.active_profile)
    item, buyer, seller, request = complete_auction_deal(
        seller=seller,
        paired_bird=paired_bird,
    )

    deal = CompletedDeal.objects.get()
    assert deal.deal_type == CompletedDealType.AUCTION
    assert deal.source_type == CompletedDealSourceType.AUCTION_ITEM
    assert deal.source_id == item.pk
    assert deal.buyer_user_id == buyer.pk
    assert deal.buyer_profile_id == buyer.active_profile.pk
    assert deal.seller_user_id == seller.pk
    assert deal.seller_profile_id == seller.active_profile.pk
    assert deal.asset_id == item.bird_id
    assert deal.amount == item.current_price == request.amount == Decimal("150.00")
    assert deal.completed_at == request.approved_at
    assert deal.metadata == {
        "auction_id": item.auction_id,
        "paired_bird_id": paired_bird.pk,
        "payment_request_id": request.pk,
    }


def test_repeated_completion_is_idempotent():
    item, _, seller, _, request = complete_market_deal()
    auction_item, _, auction_seller, auction_request = complete_auction_deal()
    market_deal = CompletedDeal.objects.get(deal_type=CompletedDealType.MARKET)
    auction_deal = CompletedDeal.objects.get(deal_type=CompletedDealType.AUCTION)

    assert CompletedDealService.create_from_market_completion(item).pk == market_deal.pk
    assert (
        CompletedDealService.create_from_auction_completion(auction_item).pk
        == auction_deal.pk
    )
    with pytest.raises(ValidationError):
        PaymentRequestService.approve(request.pk, seller.active_profile)
    with pytest.raises(ValidationError):
        PaymentRequestService.approve(auction_request.pk, auction_seller.active_profile)

    assert CompletedDeal.objects.count() == 2


# --- Non-final states create no deal ---------------------------------------


def test_request_creation_and_rejection_create_no_deal():
    item, buyer, seller, _ = create_approved_order_item()
    auction_item, auction_buyer, auction_seller = create_sold_auction_item()

    market_request = PaymentRequestService.create_for_order_item(
        item.pk, buyer.active_profile
    )
    auction_request = PaymentRequestService.create_for_auction_item(
        auction_item.pk, auction_buyer.active_profile
    )
    assert not CompletedDeal.objects.exists()

    PaymentRequestService.reject(market_request.pk, seller.active_profile)
    PaymentRequestService.reject(auction_request.pk, auction_seller.active_profile)

    assert not CompletedDeal.objects.exists()


def test_resubmitted_request_creates_one_deal_when_finally_approved():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    PaymentRequestService.reject(request.pk, seller.active_profile)
    PaymentRequestService.update_by_buyer(request.pk, buyer.active_profile, "retry")

    PaymentRequestService.approve(request.pk, seller.active_profile)

    assert CompletedDeal.objects.filter(source_id=item.pk).count() == 1


def test_intermediate_order_and_auction_states_create_no_deal():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    order = OrderFactory(owner=active_customer(buyer))
    item = OrderItemFactory(
        order=order,
        asset=SupplyFactory(owner=active_seller(seller), count=5),
        seller=active_seller(seller),
        quantity=1,
        unit_price_snapshot=Decimal("10.00"),
        status=OrderItemStatus.PENDING_SELLER,
    )
    OrderItemService.approve_item(item, seller.active_profile)
    create_sold_auction_item()

    item.refresh_from_db()
    assert item.status == OrderItemStatus.APPROVED
    assert not CompletedDeal.objects.exists()
    with pytest.raises(ValidationError):
        CompletedDealService.create_from_market_completion(item)


def test_service_rejects_sources_without_approved_confirmation():
    auction_item, buyer, _ = create_sold_auction_item()
    PaymentRequestService.create_for_auction_item(auction_item.pk, buyer.active_profile)

    with pytest.raises(ValidationError):
        CompletedDealService.create_from_auction_completion(auction_item)

    assert not CompletedDeal.objects.exists()


# --- Rollback with the business transaction --------------------------------


def test_market_stock_failure_leaves_no_deal():
    item, buyer, seller, asset = create_approved_order_item(quantity=2)
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    asset.count = 1
    asset.save(update_fields=["count", "modified"])

    with pytest.raises(ValidationError):
        PaymentRequestService.approve(request.pk, seller.active_profile)

    assert not CompletedDeal.objects.exists()


def test_failure_after_deal_creation_rolls_the_deal_back(monkeypatch):
    item, buyer, seller, asset = create_approved_order_item(quantity=2)
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)

    def fail(*args, **kwargs):
        raise RuntimeError("event log failed")

    # _log_event runs after the deal (and its rewards) are recorded.
    monkeypatch.setattr(PaymentRequestService, "_log_event", fail)
    with pytest.raises(RuntimeError):
        PaymentRequestService.approve(request.pk, seller.active_profile)

    request.refresh_from_db()
    item.refresh_from_db()
    asset.refresh_from_db()
    assert not CompletedDeal.objects.exists()
    assert not PointTransaction.objects.exists()
    assert not BadgeAward.objects.exists()
    assert request.status == PaymentRequestStatus.PENDING
    assert item.status == OrderItemStatus.APPROVED
    assert asset.count == 5


# --- Parties, snapshot, immutability ---------------------------------------


def test_deal_profiles_belong_to_deal_accounts():
    complete_market_deal()
    deal = CompletedDeal.objects.select_related("buyer_profile", "seller_profile").get()

    assert deal.buyer_profile.user_id == deal.buyer_user_id
    assert deal.seller_profile.user_id == deal.seller_user_id


def test_mismatched_accounts_are_rejected():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    stranger = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    with pytest.raises(ValidationError):
        CompletedDealValidator.validate_parties(
            buyer_user=stranger,
            buyer_profile=buyer.active_profile,
            seller_user=seller,
            seller_profile=seller.active_profile,
        )
    with pytest.raises(ValidationError):
        CompletedDealValidator.validate_parties(
            buyer_user=buyer,
            buyer_profile=buyer.active_profile,
            seller_user=stranger,
            seller_profile=seller.active_profile,
        )


def test_amount_is_a_snapshot():
    item, _, _, asset, _ = complete_market_deal()
    auction_item, _, _, _ = complete_auction_deal()

    asset.price = Decimal("999.00")
    asset.save(update_fields=["price", "modified"])
    auction_item.current_price = Decimal("999.00")
    auction_item.save(update_fields=["current_price", "modified"])

    market_deal = CompletedDeal.objects.get(deal_type=CompletedDealType.MARKET)
    auction_deal = CompletedDeal.objects.get(deal_type=CompletedDealType.AUCTION)
    assert market_deal.source_id == item.pk
    assert market_deal.amount == Decimal("50.00")
    assert auction_deal.source_id == auction_item.pk
    assert auction_deal.amount == Decimal("150.00")


def test_completed_deal_cannot_be_changed_or_deleted():
    complete_market_deal()
    deal = CompletedDeal.objects.get()

    deal.amount = Decimal("1.00")
    with pytest.raises(DjangoValidationError):
        deal.save()
    with pytest.raises(DjangoValidationError):
        deal.delete()

    assert CompletedDeal.objects.get().amount == Decimal("50.00")


def test_database_rejects_duplicate_source_and_mismatched_type():
    complete_market_deal()
    deal = CompletedDeal.objects.get()
    fields = {
        "buyer_user": deal.buyer_user,
        "buyer_profile": deal.buyer_profile,
        "seller_user": deal.seller_user,
        "seller_profile": deal.seller_profile,
        "asset": deal.asset,
        "amount": deal.amount,
        "completed_at": deal.completed_at,
    }

    with pytest.raises(IntegrityError), transaction.atomic():
        CompletedDeal.objects.create(
            deal_type=CompletedDealType.MARKET,
            source_type=CompletedDealSourceType.ORDER_ITEM,
            source_id=deal.source_id,
            **fields,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        CompletedDeal.objects.create(
            deal_type=CompletedDealType.AUCTION,
            source_type=CompletedDealSourceType.ORDER_ITEM,
            source_id=deal.source_id + 1000,
            **fields,
        )


def test_admin_is_read_only():
    admin = site._registry[CompletedDeal]
    request = RequestFactory().get("/")
    request.user = UserAccountFactory(with_profile=False)

    assert not admin.has_add_permission(request)
    assert not admin.has_change_permission(request)
    assert not admin.has_delete_permission(request)


# --- Query helpers and reward regression -----------------------------------


def test_query_helpers_filter_by_profile_or_account():
    _, buyer, seller, _, _ = complete_market_deal()
    complete_auction_deal(buyer=buyer)

    assert CompletedDealService.completed_for_buyer(buyer).count() == 2
    assert CompletedDealService.completed_for_buyer(buyer.active_profile).count() == 2
    assert CompletedDealService.completed_for_seller(seller).count() == 1
    assert CompletedDealService.completed_for_seller(seller.active_profile).count() == 1
    assert not CompletedDealService.completed_for_seller(buyer).exists()


def test_market_rewards_are_unchanged_by_deal_recording():
    item, buyer, seller, _, _ = complete_market_deal()

    keys = set(
        PointTransaction.objects.filter(
            idempotency_key__startswith="reward:completed_deal"
        ).values_list("idempotency_key", flat=True)
    )
    assert keys == {
        f"reward:completed_deal:order_item:{item.pk}:buyer",
        f"reward:completed_deal:order_item:{item.pk}:seller",
        f"reward:completed_deal_bonus:order_item:{item.pk}:buyer",
        f"reward:completed_deal_bonus:order_item:{item.pk}:seller",
    }
    assert BadgeAward.objects.filter(
        profile=buyer.active_profile,
        badge_type=BadgeType.MARKET_PURCHASE_CONFIRMED,
    ).count() == 1
    assert BadgeAward.objects.filter(
        profile=seller.active_profile,
        badge_type=BadgeType.FIRST_MARKET_SALE_CONFIRMED,
    ).count() == 1
    assert CompletedDeal.objects.count() == 1
