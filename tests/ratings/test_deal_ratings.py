"""BE-REW-10: deal-scoped buyer <-> seller ratings and the mutual rating reward."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.deals.models import CompletedDeal
from apps.loyalty.constants import (
    BadgeType,
    RewardEventType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine, RewardResult
from apps.loyalty.services import PointService, RewardRuleService
from apps.orders.models import OrderItemStatus
from apps.payments.services import PaymentRequestService
from apps.ratings.models import DealRating, DealRatingDirection
from apps.ratings.services import DealRatingService, create_seller_rating
from apps.users.constants import ProfileType
from tests.factories import (
    CompletedDealFactory,
    CustomerProfileFactory,
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

RATE_URL = "/api/deal-ratings/"
MUTUAL = RewardSourceType.MUTUAL_RATING


def status_url(deal):
    return f"/api/deal-ratings/deals/{deal.pk}/"


def client_for(user):
    user.refresh_from_db()
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {build_token_for_user(user).access_token}"
    )
    return client


def rate(user, deal, stars=5, comment="", **extra):
    return client_for(user).post(
        RATE_URL,
        {"deal_id": deal.pk, "stars": stars, "comment": comment, **extra},
        format="json",
    )


def parties():
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    return buyer, seller


def make_deal(buyer=None, seller=None, **kwargs):
    if buyer is None or seller is None:
        buyer, seller = parties()
    deal = CompletedDealFactory(
        buyer_profile=active_customer(buyer),
        seller_profile=active_seller(seller),
        **kwargs,
    )
    return deal, buyer, seller


def mutual_rows(user=None):
    queryset = PointTransaction.objects.filter(source_type=MUTUAL)
    return queryset.filter(user=user) if user is not None else queryset


def set_rule(**changes):
    RewardRuleService.update_rule(RewardRuleCode.MUTUAL_RATING, updated_by=None, **changes)


def fire_mutual(deal):
    return RewardEngine.handle_event(
        RewardEventType.MUTUAL_RATING_COMPLETED,
        actor=deal.seller_profile,
        context={"deal": deal},
    )


# --- Completion order --------------------------------------------------------------


def test_first_rating_saves_without_reward():
    deal, buyer, seller = make_deal()

    response = rate(buyer, deal, stars=4, comment="Smooth deal")

    assert response.status_code == status.HTTP_201_CREATED
    rating = DealRating.objects.get()
    assert rating.direction == DealRatingDirection.BUYER_TO_SELLER
    assert rating.rater_profile_id == deal.buyer_profile_id
    assert rating.rated_profile_id == deal.seller_profile_id
    assert rating.stars == 4
    assert rating.comment == "Smooth deal"
    assert not mutual_rows().exists()


def test_second_rating_rewards_both_parties():
    deal, buyer, seller = make_deal()
    rate(buyer, deal)

    response = rate(seller, deal, stars=3)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["direction"] == DealRatingDirection.SELLER_TO_BUYER
    assert list(mutual_rows(buyer).values_list("points", flat=True)) == [20]
    assert list(mutual_rows(seller).values_list("points", flat=True)) == [20]


def test_reverse_order_gives_the_same_result():
    deal, buyer, seller = make_deal()

    rate(seller, deal)
    assert not mutual_rows().exists()
    rate(buyer, deal)

    assert mutual_rows(buyer).get().points == 20
    assert mutual_rows(seller).get().points == 20


def test_one_sided_rating_never_pays():
    deal, buyer, _ = make_deal()
    rate(buyer, deal)

    assert DealRatingService.is_mutual(deal) is False
    assert not mutual_rows().exists()


# --- Market and auction deals from the real completion flow --------------------------


def test_market_deal_mutual_rating():
    item, buyer, seller, _ = create_approved_order_item()
    request = PaymentRequestService.create_for_order_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    deal = CompletedDeal.objects.get(source_id=item.pk, deal_type="market")

    rate(buyer, deal)
    rate(seller, deal)

    assert mutual_rows(buyer).count() == 1
    assert mutual_rows(seller).count() == 1


def test_auction_deal_mutual_rating():
    item, buyer, seller = create_sold_auction_item()
    request = PaymentRequestService.create_for_auction_item(item.pk, buyer.active_profile)
    PaymentRequestService.approve(request.pk, seller.active_profile)
    deal = CompletedDeal.objects.get(source_id=item.pk, deal_type="auction")

    rate(seller, deal)
    rate(buyer, deal)

    assert mutual_rows(buyer).count() == 1
    assert mutual_rows(seller).count() == 1


# --- Exactly once, per deal --------------------------------------------------------


def test_replayed_mutual_event_pays_once():
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)

    for _ in range(3):
        fire_mutual(deal)

    assert mutual_rows(buyer).count() == 1
    assert mutual_rows(seller).count() == 1


def test_each_deal_pays_separately():
    deal_a, buyer, seller = make_deal()
    deal_b, _, _ = make_deal(buyer=buyer, seller=seller)
    for deal in (deal_a, deal_b):
        rate(buyer, deal)
        rate(seller, deal)

    assert sorted(mutual_rows(buyer).values_list("points", flat=True)) == [20, 20]
    assert sorted(mutual_rows(seller).values_list("points", flat=True)) == [20, 20]
    assert set(mutual_rows(buyer).values_list("source_id", flat=True)) == {
        str(deal_a.pk),
        str(deal_b.pk),
    }


# --- Eligibility and direction -------------------------------------------------------


def test_unrelated_profile_cannot_rate_the_deal():
    deal, _, _ = make_deal()
    stranger = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    stranger_seller = UserAccountFactory(with_profile=ProfileType.SELLER)

    assert rate(stranger, deal).status_code == status.HTTP_404_NOT_FOUND
    assert rate(stranger_seller, deal).status_code == status.HTTP_404_NOT_FOUND
    assert not DealRating.objects.exists()


def test_rated_profile_is_always_the_counterparty():
    deal, buyer, seller = make_deal()
    other_seller = SellerProfileFactory(is_active=True)
    other_customer = CustomerProfileFactory(is_active=True)

    rate(buyer, deal, rated_profile_id=other_seller.pk)
    rate(seller, deal, rated_profile_id=other_customer.pk)

    by_direction = {r.direction: r for r in DealRating.objects.all()}
    assert by_direction[DealRatingDirection.BUYER_TO_SELLER].rated_profile_id == (
        deal.seller_profile_id
    )
    assert by_direction[DealRatingDirection.SELLER_TO_BUYER].rated_profile_id == (
        deal.buyer_profile_id
    )


def test_party_of_another_deal_cannot_rate_this_deal():
    deal, _, _ = make_deal()
    _, other_buyer, other_seller = make_deal()

    assert rate(other_buyer, deal).status_code == status.HTTP_404_NOT_FOUND
    assert rate(other_seller, deal).status_code == status.HTTP_404_NOT_FOUND


def test_incomplete_transaction_has_no_deal_to_rate():
    buyer, seller = parties()
    OrderItemFactory(
        order=OrderFactory(owner=active_customer(buyer)),
        asset=SupplyFactory(owner=active_seller(seller)),
        seller=active_seller(seller),
        unit_price_snapshot=Decimal("10.00"),
        status=OrderItemStatus.APPROVED,
    )

    response = client_for(buyer).post(
        RATE_URL, {"deal_id": 999999, "stars": 5}, format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert not DealRating.objects.exists()


@pytest.mark.parametrize("stars", [0, 6])
def test_stars_must_be_one_to_five(stars):
    deal, buyer, _ = make_deal()

    assert rate(buyer, deal, stars=stars).status_code == status.HTTP_400_BAD_REQUEST


def test_manager_cannot_rate():
    deal, _, _ = make_deal()
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

    assert rate(manager, deal).status_code == status.HTTP_403_FORBIDDEN


# --- Duplicates, updates, deletes ----------------------------------------------------


def test_duplicate_rating_is_rejected_and_backed_by_the_database():
    deal, buyer, _ = make_deal()
    rate(buyer, deal)

    assert rate(buyer, deal, stars=1).status_code == status.HTTP_400_BAD_REQUEST
    with pytest.raises(IntegrityError), transaction.atomic():
        DealRating.objects.create(
            deal=deal,
            direction=DealRatingDirection.BUYER_TO_SELLER,
            rater_profile_id=deal.buyer_profile_id,
            rated_profile_id=deal.seller_profile_id,
            stars=1,
        )
    assert DealRating.objects.count() == 1


def test_editing_a_rating_does_not_pay_again():
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)

    client = client_for(buyer)
    patch = client.patch(RATE_URL, {"stars": 1}, format="json")
    rating = DealRating.objects.get(direction=DealRatingDirection.BUYER_TO_SELLER)
    rating.stars = 1
    rating.save(update_fields=["stars", "modified"])

    assert patch.status_code in {
        status.HTTP_403_FORBIDDEN,
        status.HTTP_405_METHOD_NOT_ALLOWED,
    }
    assert mutual_rows().count() == 2


def test_deleted_rating_cannot_be_recreated_to_farm():
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)

    DealRating.objects.get(direction=DealRatingDirection.BUYER_TO_SELLER).delete()
    response = rate(buyer, deal)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert DealRating.all_objects.count() == 2
    assert mutual_rows().count() == 2


# --- Rule configuration --------------------------------------------------------------


def test_configured_points_are_used():
    set_rule(points=35)
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)

    assert mutual_rows(buyer).get().points == 35
    assert mutual_rows(seller).get().points == 35


def test_disabled_rule_still_saves_ratings_and_is_not_topped_up():
    set_rule(enabled=False)
    deal, buyer, seller = make_deal()

    assert rate(buyer, deal).status_code == status.HTTP_201_CREATED
    assert rate(seller, deal).status_code == status.HTTP_201_CREATED
    assert DealRatingService.is_mutual(deal) is True
    assert not mutual_rows().exists()

    set_rule(enabled=True)
    # The pair can only be completed once, so no rating flow re-emits the event.
    assert rate(buyer, deal).status_code == status.HTTP_400_BAD_REQUEST
    assert not mutual_rows().exists()


# --- Ledger -------------------------------------------------------------------------


def test_points_only_and_ledger_ownership():
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)

    assert not BadgeAward.objects.exists()
    buyer_row = mutual_rows(buyer).get()
    seller_row = mutual_rows(seller).get()
    assert (buyer_row.user_id, buyer_row.profile_id) == (
        deal.buyer_user_id,
        deal.buyer_profile_id,
    )
    assert (seller_row.user_id, seller_row.profile_id) == (
        deal.seller_user_id,
        deal.seller_profile_id,
    )


def test_reward_source_is_the_deal_not_a_rating_row():
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)

    for row, role in ((mutual_rows(buyer).get(), "buyer"), (mutual_rows(seller).get(), "seller")):
        assert row.idempotency_key == f"reward:mutual_rating:deal:{deal.pk}:{role}"
        assert row.source_id == str(deal.pk)
        assert row.reason == "Mutual deal rating reward"
        assert row.metadata == {
            "reward_code": "mutual_rating",
            "deal_id": deal.pk,
            "deal_type": deal.deal_type,
        }


def test_reward_lands_in_the_account_wallet():
    deal, buyer, seller = make_deal()
    CustomerProfileFactory(user=seller, is_active=False)
    rate(buyer, deal)
    rate(seller, deal)

    switch_user_profile(seller, ProfileType.CUSTOMER)

    assert PointService.get_balance(seller) == 20
    assert PointService.get_balance(buyer) == 20


# --- Existing public ratings are unchanged ------------------------------------------


def test_public_seller_rating_rewards_are_unchanged():
    deal, buyer, seller = make_deal()
    OrderItemFactory(
        order=OrderFactory(owner=active_customer(buyer)),
        asset=SupplyFactory(owner=active_seller(seller)),
        seller=active_seller(seller),
        unit_price_snapshot=Decimal("10.00"),
        status=OrderItemStatus.COMPLETED,
    )

    create_seller_rating(buyer.active_profile, active_seller(seller).pk, 5)

    assert PointTransaction.objects.filter(
        user=seller, source_type=RewardSourceType.RATING_RECEIVED, points=30
    ).count() == 1
    assert BadgeAward.objects.filter(badge_type=BadgeType.REVIEWER_BUYER).count() == 1
    assert not DealRating.objects.exists()


def test_deal_ratings_do_not_trigger_public_rating_rewards():
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)

    assert not PointTransaction.objects.filter(
        source_type=RewardSourceType.RATING_RECEIVED
    ).exists()
    assert not BadgeAward.objects.filter(badge_type=BadgeType.REVIEWER_BUYER).exists()
    assert not active_seller(seller).sellerrating_set.exists()


# --- Concurrency-oriented -----------------------------------------------------------


def test_double_emission_and_stale_reads_keep_one_row_per_party(monkeypatch):
    deal, buyer, seller = make_deal()
    rate(buyer, deal)
    rate(seller, deal)
    balances = (PointService.get_balance(buyer), PointService.get_balance(seller))

    # Both concurrent requests decide the pair is complete and emit, and the
    # second cannot yet see the first's rows: inserts hit the unique key.
    monkeypatch.setattr(RewardEngine, "_paid_keys", staticmethod(lambda keys: set()))
    monkeypatch.setattr(
        PointService,
        "_get_idempotent_transaction",
        staticmethod(lambda key: None),
    )
    result = RewardResult()
    for _ in range(2):
        RewardEngine._handle_mutual_rating_completed({"deal": deal}, result)

    assert result.points_awarded == []
    assert mutual_rows(buyer).count() == 1
    assert mutual_rows(seller).count() == 1
    assert (PointService.get_balance(buyer), PointService.get_balance(seller)) == balances


def test_stray_event_without_both_ratings_pays_nothing():
    deal, buyer, _ = make_deal()
    rate(buyer, deal)

    fire_mutual(deal)

    assert not mutual_rows().exists()


def test_completing_rating_always_sees_the_first_one():
    deal, buyer, seller = make_deal()
    DealRatingService.create(active_customer(buyer), deal.pk, 5)

    rating = DealRatingService.create(active_seller(seller), deal.pk, 4)

    assert rating.direction == DealRatingDirection.SELLER_TO_BUYER
    assert mutual_rows().count() == 2


# --- Same account in both roles -----------------------------------------------------


def test_same_account_deal_can_complete_mutual_rating():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_profile = SellerProfileFactory(user=user, is_active=False)
    deal = CompletedDealFactory(
        buyer_profile=active_customer(user),
        seller_profile=seller_profile,
    )

    assert rate(user, deal).status_code == status.HTTP_201_CREATED
    switch_user_profile(user, ProfileType.SELLER)
    assert rate(user, deal).status_code == status.HTTP_201_CREATED

    # Current behavior (not blocked): both party rewards land in one wallet.
    assert sorted(mutual_rows(user).values_list("points", flat=True)) == [20, 20]


# --- Status read API ----------------------------------------------------------------


def test_status_hides_the_counterparty_rating_until_mutual():
    deal, buyer, seller = make_deal()
    rate(buyer, deal, stars=4, comment="Good")

    seller_view = client_for(seller).get(status_url(deal)).data
    assert seller_view["role"] == "seller"
    assert seller_view["has_rated"] is False
    assert seller_view["counterparty_has_rated"] is True
    assert seller_view["is_mutual"] is False
    assert seller_view["counterparty_rating"] is None

    rate(seller, deal, stars=5)
    buyer_view = client_for(buyer).get(status_url(deal)).data

    assert buyer_view["role"] == "buyer"
    assert buyer_view["is_mutual"] is True
    assert buyer_view["my_rating"]["stars"] == 4
    assert buyer_view["counterparty_rating"]["stars"] == 5


def test_status_is_private_to_deal_participants():
    deal, _, _ = make_deal()
    stranger = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    response = client_for(stranger).get(status_url(deal))

    assert response.status_code == status.HTTP_404_NOT_FOUND
