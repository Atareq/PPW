from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.deals.models import (
    CompletedDeal,
    CompletedDealSourceType,
    CompletedDealType,
)
from apps.loyalty.constants import BadgeType, RewardRuleCode, RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import PointService, RewardRuleService
from apps.notifications.models import Notification
from apps.promotions.models import UserPromotionGrant
from apps.ratings.models import DealRating, DealRatingDirection
from apps.referrals.models import ReferralChallenge
from apps.referrals.services import ReferralChallengeService, ReferralService
from apps.seasons.models import Season, SeasonSellerInsight, SeasonStatus
from apps.seasons.services import (
    SEASON_WINNER_POINTS,
    SeasonPayoutService,
    SeasonScoreService,
    SeasonService,
)
from apps.users.constants import ProfileType
from tests.factories import (
    BidFactory,
    CompletedDealFactory,
    CustomerProfileFactory,
    ManagerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def auth_client(user, lang="en"):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE=lang,
    )
    return client


def make_manager(permissions=None):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager_profile = user.active_profile
    if permissions is not None:
        manager_profile.permissions = list(permissions)
        manager_profile.save(update_fields=["permissions", "modified"])
    else:
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    return user, manager_profile


def make_ended_season(days=30, status=SeasonStatus.DRAFT):
    now = timezone.now()
    starts_at = now - timedelta(days=days)
    ends_at = now - timedelta(hours=1)
    return Season.objects.create(
        title="Ended Season",
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
    )


def make_active_season():
    now = timezone.now()
    return Season.objects.create(
        title="Active Season",
        starts_at=now - timedelta(days=10),
        ends_at=now + timedelta(days=5),
        status=SeasonStatus.DRAFT,
    )


def create_completed_deal(
    *,
    seller,
    buyer=None,
    completed_at=None,
    amount=Decimal("100.00"),
):
    if buyer is None:
        buyer = CustomerProfileFactory()
    if completed_at is None:
        completed_at = timezone.now() - timedelta(days=2)
    return CompletedDealFactory(
        deal_type=CompletedDealType.MARKET,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        buyer_user=buyer.user,
        buyer_profile=buyer,
        seller_user=seller.user,
        seller_profile=seller,
        amount=amount,
        currency="USD",
        completed_at=completed_at,
    )


def create_four_ranked_sellers(season):
    """Create 4 qualifying sellers with deterministic distinct scores:
    seller_1 (4 sales) > seller_2 (3 sales) > seller_3 (2 sales) > seller_4 (1 sale).
    """
    sellers = [SellerProfileFactory() for _ in range(4)]
    in_window = season.starts_at + timedelta(days=2)
    for idx, seller in enumerate(sellers):
        sales_count = 4 - idx
        for _ in range(sales_count):
            create_completed_deal(seller=seller, completed_at=in_window)
    return sellers


def season_winner_txs(season=None, seller=None):
    qs = PointTransaction.objects.filter(source_type=RewardSourceType.SEASON_WINNER)
    if season is not None:
        qs = qs.filter(source_id=str(season.pk))
    if seller is not None:
        qs = qs.filter(profile_id=seller.pk)
    return qs.order_by("id")


# ---------------------------------------------------------------------------
# 1. Finalization Eligibility
# ---------------------------------------------------------------------------


def test_01_cannot_finalize_before_ends_at():
    user, manager = make_manager(["seasons.finalize"])
    season = make_active_season()
    seller = SellerProfileFactory()
    create_completed_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=1),
    )

    with pytest.raises(DRFValidationError) as exc_info:
        SeasonService.finalize(season, manager_profile=manager)
    assert any(
        msg in str(exc_info.value.detail)
        for msg in (
            "Season cannot be finalized before it ends",
            "لا يمكن إنهاء الموسم قبل انتهائه",
        )
    )

    response = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert response.status_code == 400
    assert "Season cannot be finalized before it ends" in str(response.data)

    season.refresh_from_db()
    assert season.status == SeasonStatus.DRAFT
    assert season.finalized_by is None
    assert season.finalized_at is None
    assert season_winner_txs(season=season).count() == 0


# ---------------------------------------------------------------------------
# 2. Final Score Refresh
# ---------------------------------------------------------------------------


def test_02_finalization_refreshes_latest_scores():
    _, manager = make_manager(["seasons.finalize"])
    season = make_ended_season()
    seller_a = SellerProfileFactory()
    seller_b = SellerProfileFactory()
    t1 = season.starts_at + timedelta(days=1)
    t2 = season.starts_at + timedelta(days=2)

    # Initial calculation: seller_a has 2 sales, seller_b has 1 sale.
    create_completed_deal(seller=seller_a, completed_at=t1)
    create_completed_deal(seller=seller_a, completed_at=t1)
    create_completed_deal(seller=seller_b, completed_at=t1)
    SeasonService.calculate_winners(season, manager_profile=manager)

    # Before finalization, seller_b completes 3 more in-window deals (total 4 > 2).
    for _ in range(3):
        create_completed_deal(seller=seller_b, completed_at=t2)

    # Finalize without manually calling calculate_winners again.
    SeasonService.finalize(season, manager_profile=manager)

    insights = list(SeasonService.insights(season))
    assert [i.seller_id for i in insights] == [seller_b.pk, seller_a.pk]
    assert [i.rank for i in insights] == [1, 2]
    assert insights[0].completed_sales_count == 4
    assert insights[0].points_awarded == 100000
    assert insights[1].completed_sales_count == 2
    assert insights[1].points_awarded == 50000
    assert PointService.get_balance(seller_b.user) == 100000
    assert PointService.get_balance(seller_a.user) == 50000


# ---------------------------------------------------------------------------
# 3 - 9. Winner Payout Amounts & Participant Counts
# ---------------------------------------------------------------------------


def test_03_rank_1_gets_confirmed_100000_pp():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    tx = season_winner_txs(season=season, seller=sellers[0]).get()
    assert tx.points == 100000
    assert PointService.get_balance(sellers[0].user) == 100000


def test_04_rank_2_gets_confirmed_50000_pp():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    tx = season_winner_txs(season=season, seller=sellers[1]).get()
    assert tx.points == 50000
    assert PointService.get_balance(sellers[1].user) == 50000


def test_05_rank_3_gets_confirmed_25000_pp():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    tx = season_winner_txs(season=season, seller=sellers[2]).get()
    assert tx.points == 25000
    assert PointService.get_balance(sellers[2].user) == 25000


def test_06_rank_4_gets_0_pp():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    assert not season_winner_txs(season=season, seller=sellers[3]).exists()
    assert PointService.get_balance(sellers[3].user) == 0


def test_07_one_participant_pays_rank_1_only():
    season = make_ended_season()
    seller = SellerProfileFactory()
    create_completed_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )

    SeasonService.finalize(season)

    txs = list(season_winner_txs(season=season))
    assert len(txs) == 1
    assert txs[0].profile_id == seller.pk
    assert txs[0].points == 100000


def test_08_two_participants_pays_rank_1_and_2_only():
    season = make_ended_season()
    seller_1 = SellerProfileFactory()
    seller_2 = SellerProfileFactory()
    t = season.starts_at + timedelta(days=2)
    create_completed_deal(seller=seller_1, completed_at=t)
    create_completed_deal(seller=seller_1, completed_at=t)
    create_completed_deal(seller=seller_2, completed_at=t)

    SeasonService.finalize(season)

    txs = list(season_winner_txs(season=season))
    assert len(txs) == 2
    assert [(tx.profile_id, tx.points) for tx in txs] == [
        (seller_1.pk, 100000),
        (seller_2.pk, 50000),
    ]


def test_09_zero_participants_creates_zero_payout_and_finalizes():
    _, manager = make_manager(["seasons.finalize"])
    season = make_ended_season()

    finalized = SeasonService.finalize(season, manager_profile=manager)

    assert finalized.status == SeasonStatus.FINALIZED
    assert finalized.finalized_by_id == manager.pk
    assert finalized.finalized_at is not None
    assert season_winner_txs(season=season).count() == 0
    assert season.seller_insights.count() == 0


# ---------------------------------------------------------------------------
# 10 - 13. Ownership, Source, Metadata, and Idempotency Key Identity
# ---------------------------------------------------------------------------


def test_10_payout_uses_correct_user_seller_user_in_shared_wallet():
    season = make_ended_season()
    dual_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = SellerProfileFactory(user=dual_user, is_active=False, activated=True)
    create_completed_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )
    # Keep user active in Customer mode when season finalizes.
    assert dual_user.active_profile.type == ProfileType.CUSTOMER

    SeasonService.finalize(season)

    tx = season_winner_txs(season=season, seller=seller).get()
    assert tx.user_id == seller.user_id == dual_user.pk
    assert PointService.get_balance(dual_user) == 100000


def test_11_payout_uses_correct_profile_seller():
    season = make_ended_season()
    seller = SellerProfileFactory()
    create_completed_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )

    SeasonService.finalize(season)

    tx = season_winner_txs(season=season).get()
    assert tx.profile_id == seller.pk


def test_12_payout_uses_correct_season_winner_source_and_audit_metadata():
    season = make_ended_season()
    seller = SellerProfileFactory()
    create_completed_deal(
        seller=seller,
        completed_at=season.starts_at + timedelta(days=2),
    )

    SeasonService.finalize(season)

    insight = SeasonSellerInsight.objects.get(season=season, seller=seller)
    tx = season_winner_txs(season=season, seller=seller).get()
    assert tx.source_type == str(RewardSourceType.SEASON_WINNER)
    assert tx.source_id == str(season.pk)
    assert tx.metadata["season_id"] == season.pk
    assert tx.metadata["seller_id"] == seller.pk
    assert tx.metadata["rank"] == 1
    assert tx.metadata["season_score"] == str(insight.season_score)


def test_13_idempotency_key_excludes_rank():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    for rank_idx in range(3):
        seller = sellers[rank_idx]
        tx = season_winner_txs(season=season, seller=seller).get()
        expected_key = f"reward:season_winner:season:{season.pk}:seller:{seller.pk}"
        assert tx.idempotency_key == expected_key
        assert "rank" not in tx.idempotency_key


# ---------------------------------------------------------------------------
# 14 - 16. Idempotent Finalization, Stale/Retry Evaluation, Rank Change Before Finalize
# ---------------------------------------------------------------------------


def test_14_finalize_twice_creates_no_duplicate_payout():
    user, manager = make_manager(["seasons.finalize"])
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    first_res = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert first_res.status_code == 200
    assert season_winner_txs(season=season).count() == 3

    second_res = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert second_res.status_code == 200
    assert season_winner_txs(season=season).count() == 3

    SeasonService.finalize(season, manager_profile=manager)
    assert season_winner_txs(season=season).count() == 3
    assert PointService.get_balance(sellers[0].user) == 100000
    assert PointService.get_balance(sellers[1].user) == 50000
    assert PointService.get_balance(sellers[2].user) == 25000


def test_15_stale_retry_evaluation_creates_no_duplicate(monkeypatch):
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    SeasonScoreService.calculate_season_scores(season)

    # First payout run
    SeasonPayoutService.payout_season_winners(season)
    assert season_winner_txs(season=season).count() == 3

    # Direct second call to SeasonPayoutService
    SeasonPayoutService.payout_season_winners(season)
    assert season_winner_txs(season=season).count() == 3

    # Simulate a stale read where get_historical_winner_transactions returns []
    # so PointService idempotency key is exercised directly (even if rank changed).
    insight_1 = SeasonSellerInsight.objects.get(season=season, seller=sellers[0])
    insight_1.rank = 2
    insight_1.save(update_fields=["rank", "modified"])
    monkeypatch.setattr(
        SeasonPayoutService,
        "get_historical_winner_transactions",
        staticmethod(lambda season, seller: []),
    )
    SeasonPayoutService.payout_season_winners(season)

    assert season_winner_txs(season=season).count() == 3
    assert PointService.get_balance(sellers[0].user) == 100000
    assert PointService.get_balance(sellers[1].user) == 50000
    assert PointService.get_balance(sellers[2].user) == 25000


def test_16_rank_change_before_finalize_pays_final_rank_once():
    season = make_ended_season()
    seller_a = SellerProfileFactory()
    seller_b = SellerProfileFactory()
    t1 = season.starts_at + timedelta(days=1)
    t2 = season.starts_at + timedelta(days=2)

    # Initially seller_a is #1 (2 deals) and seller_b is #2 (1 deal).
    create_completed_deal(seller=seller_a, completed_at=t1)
    create_completed_deal(seller=seller_a, completed_at=t1)
    create_completed_deal(seller=seller_b, completed_at=t1)
    SeasonService.calculate_winners(season)
    assert season_winner_txs(season=season).count() == 0

    # Seller_b overtakes seller_a (now 3 deals vs 2 deals), recalculated before finalize.
    create_completed_deal(seller=seller_b, completed_at=t2)
    create_completed_deal(seller=seller_b, completed_at=t2)
    SeasonService.recalculate(season)
    assert season_winner_txs(season=season).count() == 0

    # Finalize pays final rank once: seller_b gets 100k, seller_a gets 50k.
    SeasonService.finalize(season)
    assert season_winner_txs(season=season, seller=seller_b).count() == 1
    assert season_winner_txs(season=season, seller=seller_a).count() == 1
    assert PointService.get_balance(seller_b.user) == 100000
    assert PointService.get_balance(seller_a.user) == 50000


# ---------------------------------------------------------------------------
# 17 - 19. Finalized Immutability
# ---------------------------------------------------------------------------


def test_17_recalculation_after_finalized_is_rejected():
    user, manager = make_manager(["seasons.calculate", "seasons.finalize"])
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    SeasonService.finalize(season, manager_profile=manager)

    for fn in (
        SeasonService.calculate_winners,
        SeasonService.calculate_scores,
        SeasonService.recalculate,
        SeasonScoreService.calculate_season_scores,
    ):
        with pytest.raises(DRFValidationError) as exc_info:
            fn(season, manager_profile=manager)
        assert any(
            msg in str(exc_info.value.detail)
            for msg in (
                "Finalized seasons cannot be recalculated",
                "لا يمكن إعادة احتساب المواسم المنتهية",
            )
        )

    response = auth_client(user).post(f"/api/seasons/{season.pk}/calculate-winners/")
    assert response.status_code == 400
    assert "Finalized seasons cannot be recalculated" in str(response.data)

    assert [i.seller_id for i in SeasonService.insights(season)] == [
        s.pk for s in sellers
    ]


def test_18_finalized_get_insights_does_not_recalculate(monkeypatch):
    user, manager = make_manager(["seasons.finalize", "seasons.insights.list"])
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    SeasonService.finalize(season, manager_profile=manager)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("calculate_season_scores must not run on GET insights")

    monkeypatch.setattr(
        SeasonScoreService,
        "calculate_season_scores",
        staticmethod(fail_if_called),
    )

    response = auth_client(user).get(f"/api/seasons/{season.pk}/seller-insights/")
    assert response.status_code == 200
    assert [row["seller_id"] for row in response.data] == [s.pk for s in sellers]
    assert [row["points_awarded"] for row in response.data] == [
        100000,
        50000,
        25000,
        0,
    ]


def test_19_new_business_data_after_finalization_does_not_alter_ranking():
    user, manager = make_manager(["seasons.finalize", "seasons.insights.list"])
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    SeasonService.finalize(season, manager_profile=manager)

    # Even if in-window deals are inserted after finalization for rank 4 seller:
    in_window = season.starts_at + timedelta(days=3)
    for _ in range(10):
        create_completed_deal(seller=sellers[3], completed_at=in_window)

    response = auth_client(user).get(f"/api/seasons/{season.pk}/seller-insights/")
    assert response.status_code == 200
    assert [row["seller_id"] for row in response.data] == [s.pk for s in sellers]
    assert [row["rank"] for row in response.data] == [1, 2, 3, 4]
    assert [row["completed_sales_count"] for row in response.data] == [4, 3, 2, 1]


# ---------------------------------------------------------------------------
# 20 - 26. Legacy Safety & points_awarded Behavior
# ---------------------------------------------------------------------------


def test_20_legacy_old_rank_1_reward_prevents_new_payout():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    seller_1 = sellers[0]

    legacy_tx = PointService.earn_points(
        user=seller_1.user,
        profile=seller_1,
        points=100000,
        reason="Season winner rank 1",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{seller_1.pk}:rank:1",
        metadata={"season_id": season.pk, "rank": 1},
    )

    SeasonService.finalize(season)

    seller_1_txs = list(season_winner_txs(season=season, seller=seller_1))
    assert len(seller_1_txs) == 1
    assert seller_1_txs[0].pk == legacy_tx.pk
    assert PointService.get_balance(seller_1.user) == 100000
    insight_1 = SeasonSellerInsight.objects.get(season=season, seller=seller_1)
    assert insight_1.points_awarded == 100000


def test_21_legacy_old_rank_2_reward_prevents_new_payout():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    seller_2 = sellers[1]

    legacy_tx = PointService.earn_points(
        user=seller_2.user,
        profile=seller_2,
        points=50000,
        reason="Season winner rank 2",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{seller_2.pk}:rank:2",
        metadata={"season_id": season.pk, "rank": 2},
    )

    SeasonService.finalize(season)

    seller_2_txs = list(season_winner_txs(season=season, seller=seller_2))
    assert len(seller_2_txs) == 1
    assert seller_2_txs[0].pk == legacy_tx.pk
    assert PointService.get_balance(seller_2.user) == 50000
    insight_2 = SeasonSellerInsight.objects.get(season=season, seller=seller_2)
    assert insight_2.points_awarded == 50000


def test_22_legacy_old_payout_at_different_rank_prevents_top_up_or_clawback():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    seller_1 = sellers[0]  # Final rank is 1 (would normally get 100,000 PP)

    # Historically paid rank 2 = 50,000 PP under old rank-based key
    legacy_tx = PointService.earn_points(
        user=seller_1.user,
        profile=seller_1,
        points=50000,
        reason="Season winner rank 2",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{seller_1.pk}:rank:2",
        metadata={"season_id": season.pk, "rank": 2},
    )

    SeasonService.finalize(season)

    # Must NOT top up to 100k, must NOT add another 100k, must NOT claw back 50k.
    seller_1_txs = list(season_winner_txs(season=season, seller=seller_1))
    assert len(seller_1_txs) == 1
    assert seller_1_txs[0].pk == legacy_tx.pk
    assert seller_1_txs[0].points == 50000
    assert PointService.get_balance(seller_1.user) == 50000

    insight_1 = SeasonSellerInsight.objects.get(season=season, seller=seller_1)
    assert insight_1.rank == 1
    assert insight_1.points_awarded == 50000


def test_23_multiple_historical_payouts_create_no_new_payout_and_no_invented_reconciliation():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    seller_1 = sellers[0]

    # Simulate old buggy rank-change payouts: paid at rank 2 (50k) and rank 1 (100k).
    tx_rank_2 = PointService.earn_points(
        user=seller_1.user,
        profile=seller_1,
        points=50000,
        reason="Season winner rank 2",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{seller_1.pk}:rank:2",
        metadata={"season_id": season.pk, "rank": 2},
    )
    tx_rank_1 = PointService.earn_points(
        user=seller_1.user,
        profile=seller_1,
        points=100000,
        reason="Season winner rank 1",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{seller_1.pk}:rank:1",
        metadata={"season_id": season.pk, "rank": 1},
    )

    SeasonService.finalize(season)

    seller_1_txs = list(season_winner_txs(season=season, seller=seller_1))
    assert [t.pk for t in seller_1_txs] == [tx_rank_2.pk, tx_rank_1.pk]
    assert PointService.get_balance(seller_1.user) == 150000

    # Do not silently invent a reconciliation value on points_awarded.
    insight_1 = SeasonSellerInsight.objects.get(season=season, seller=seller_1)
    assert insight_1.points_awarded == 0


def test_24_historical_ledger_rows_unchanged():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    seller_1 = sellers[0]

    legacy_tx = PointService.earn_points(
        user=seller_1.user,
        profile=seller_1,
        points=25000,
        reason="Season winner rank 3",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{seller_1.pk}:rank:3",
        metadata={"season_id": season.pk, "rank": 3},
    )
    snapshot_before = (
        legacy_tx.pk,
        legacy_tx.points,
        legacy_tx.balance_after,
        legacy_tx.reason,
        legacy_tx.idempotency_key,
        dict(legacy_tx.metadata),
    )

    SeasonService.finalize(season)

    legacy_tx.refresh_from_db()
    snapshot_after = (
        legacy_tx.pk,
        legacy_tx.points,
        legacy_tx.balance_after,
        legacy_tx.reason,
        legacy_tx.idempotency_key,
        dict(legacy_tx.metadata),
    )
    assert snapshot_after == snapshot_before


def test_25_new_winner_points_awarded_correct():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    insights = {
        i.seller_id: i for i in SeasonSellerInsight.objects.filter(season=season)
    }
    assert insights[sellers[0].pk].points_awarded == 100000
    assert insights[sellers[1].pk].points_awarded == 50000
    assert insights[sellers[2].pk].points_awarded == 25000


def test_26_non_winner_points_awarded_is_zero():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    insight_4 = SeasonSellerInsight.objects.get(season=season, seller=sellers[3])
    assert insight_4.rank == 4
    assert insight_4.points_awarded == 0


# ---------------------------------------------------------------------------
# 27 - 28. Scoring Remains Payout-Free Before Finalization
# ---------------------------------------------------------------------------


def test_27_calculate_scores_still_creates_0_pp():
    season = make_ended_season()
    create_four_ranked_sellers(season)

    SeasonService.calculate_scores(season)

    assert season_winner_txs(season=season).count() == 0
    assert all(
        i.points_awarded == 0
        for i in SeasonSellerInsight.objects.filter(season=season)
    )


def test_28_recalculate_still_creates_0_pp():
    season = make_ended_season()
    create_four_ranked_sellers(season)

    SeasonService.calculate_winners(season)
    SeasonService.recalculate(season)

    assert season_winner_txs(season=season).count() == 0
    assert all(
        i.points_awarded == 0
        for i in SeasonSellerInsight.objects.filter(season=season)
    )


# ---------------------------------------------------------------------------
# 29 - 31. Atomicity, Rollback, and Retry After Failure
# ---------------------------------------------------------------------------


def test_29_partial_payout_failure_rolls_back_everything(monkeypatch):
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    real_earn_points = PointService.earn_points
    call_count = {"n": 0}

    def fail_on_second_winner(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("Simulated mid-finalization rank 2 payout failure")
        return real_earn_points(*args, **kwargs)

    monkeypatch.setattr(
        PointService,
        "earn_points",
        staticmethod(fail_on_second_winner),
    )

    with pytest.raises(RuntimeError, match="Simulated mid-finalization"):
        SeasonService.finalize(season)

    # Rank 1 ledger row must be rolled back and rank 1 balance restored to 0.
    assert season_winner_txs(season=season).count() == 0
    assert PointService.get_balance(sellers[0].user) == 0
    assert PointService.get_balance(sellers[1].user) == 0
    assert PointService.get_balance(sellers[2].user) == 0


def test_30_payout_failure_leaves_season_unfinalized(monkeypatch):
    _, manager = make_manager(["seasons.finalize"])
    season = make_ended_season(status=SeasonStatus.CALCULATED)
    create_four_ranked_sellers(season)

    def fail_earn_points(*args, **kwargs):
        raise RuntimeError("Simulated payout error")

    monkeypatch.setattr(PointService, "earn_points", staticmethod(fail_earn_points))

    with pytest.raises(RuntimeError, match="Simulated payout error"):
        SeasonService.finalize(season, manager_profile=manager)

    season.refresh_from_db()
    assert season.status == SeasonStatus.CALCULATED
    assert season.finalized_by is None
    assert season.finalized_at is None


def test_31_retry_after_rollback_succeeds_once(monkeypatch):
    _, manager = make_manager(["seasons.finalize"])
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)
    real_earn_points = PointService.earn_points
    should_fail = {"active": True}
    call_count = {"n": 0}

    def flaky_earn_points(*args, **kwargs):
        call_count["n"] += 1
        if should_fail["active"] and call_count["n"] == 2:
            raise RuntimeError("Transient failure on rank 2")
        return real_earn_points(*args, **kwargs)

    monkeypatch.setattr(PointService, "earn_points", staticmethod(flaky_earn_points))

    with pytest.raises(RuntimeError, match="Transient failure"):
        SeasonService.finalize(season, manager_profile=manager)

    assert season_winner_txs(season=season).count() == 0
    season.refresh_from_db()
    assert season.status != SeasonStatus.FINALIZED

    # Now clear transient failure and retry finalization.
    should_fail["active"] = False
    finalized = SeasonService.finalize(season, manager_profile=manager)

    assert finalized.status == SeasonStatus.FINALIZED
    assert season_winner_txs(season=season).count() == 3
    assert PointService.get_balance(sellers[0].user) == 100000
    assert PointService.get_balance(sellers[1].user) == 50000
    assert PointService.get_balance(sellers[2].user) == 25000


# ---------------------------------------------------------------------------
# 32 - 34. Audit Metadata (finalized_by, finalized_at) & Date Immutability
# ---------------------------------------------------------------------------


def test_32_finalized_by_recorded_and_not_replaced_on_retry():
    _, manager_1 = make_manager(["seasons.finalize"])
    _, manager_2 = make_manager(["seasons.finalize"])
    season = make_ended_season()
    create_four_ranked_sellers(season)

    SeasonService.finalize(season, manager_profile=manager_1)
    season.refresh_from_db()
    assert season.finalized_by_id == manager_1.pk

    SeasonService.finalize(season, manager_profile=manager_2)
    season.refresh_from_db()
    assert season.finalized_by_id == manager_1.pk


def test_33_finalized_at_set_once_and_preserved_on_retry():
    user, manager = make_manager(["seasons.finalize"])
    season = make_ended_season()
    create_four_ranked_sellers(season)

    res_1 = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert res_1.status_code == 200
    season.refresh_from_db()
    first_finalized_at = season.finalized_at
    assert first_finalized_at is not None
    assert res_1.data["finalized_at"] is not None

    res_2 = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert res_2.status_code == 200
    season.refresh_from_db()
    assert season.finalized_at == first_finalized_at
    assert res_2.data["finalized_at"] == res_1.data["finalized_at"]


def test_34_finalized_dates_immutable():
    user, manager = make_manager(["seasons.finalize", "seasons.update"])
    season = make_ended_season()
    SeasonService.finalize(season, manager_profile=manager)
    original_starts_at = season.starts_at
    original_ends_at = season.ends_at

    # API PATCH attempt on starts_at and ends_at
    res_start = auth_client(user).patch(
        f"/api/seasons/{season.pk}/",
        {"starts_at": (original_starts_at - timedelta(days=5)).isoformat()},
        format="json",
    )
    assert res_start.status_code == 400
    assert "starts_at" in res_start.data

    res_end = auth_client(user).patch(
        f"/api/seasons/{season.pk}/",
        {"ends_at": (original_ends_at + timedelta(days=5)).isoformat()},
        format="json",
    )
    assert res_end.status_code == 400
    assert "ends_at" in res_end.data

    # Model-level save attempt
    season.refresh_from_db()
    season.starts_at = original_starts_at - timedelta(days=2)
    with pytest.raises(DjangoValidationError):
        season.save()

    season.refresh_from_db()
    assert season.starts_at == original_starts_at
    assert season.ends_at == original_ends_at


# ---------------------------------------------------------------------------
# 35 - 36. Zero Badges, Trophies, Prizes, or Notifications
# ---------------------------------------------------------------------------


def test_35_no_badge_trophy_or_prize_side_effect():
    season = make_ended_season()
    create_four_ranked_sellers(season)
    badges_before = BadgeAward.objects.count()
    grants_before = UserPromotionGrant.objects.count()

    SeasonService.finalize(season)

    assert BadgeAward.objects.count() == badges_before
    assert UserPromotionGrant.objects.count() == grants_before


def test_36_no_notification_side_effect():
    season = make_ended_season()
    create_four_ranked_sellers(season)
    notifications_before = Notification.objects.count()

    SeasonService.finalize(season)

    assert Notification.objects.count() == notifications_before


# ---------------------------------------------------------------------------
# 37 - 39. Cross-Ticket Regressions (BE-SEA-01, BE-REF-01..03, BE-REW-02..10)
# ---------------------------------------------------------------------------


def test_37_be_sea_01_weighted_scoring_preserved_during_finalization():
    season = make_ended_season()
    seller = SellerProfileFactory()
    buyer = CustomerProfileFactory()
    deal = create_completed_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=2),
    )
    rating = DealRating.objects.create(
        deal=deal,
        rater_profile=buyer,
        rated_profile=seller,
        direction=DealRatingDirection.BUYER_TO_SELLER,
        stars=5,
    )
    DealRating.objects.filter(pk=rating.pk).update(
        created=season.starts_at + timedelta(days=3)
    )

    SeasonService.finalize(season)

    insight = SeasonSellerInsight.objects.get(season=season, seller=seller)
    # 100 * 0.40 + 100 * 0.30 + 100 * 0.20 + 0 * 0.10 = 90.00
    assert insight.season_score == Decimal("90.00")
    assert insight.rank == 1
    assert insight.points_awarded == 100000


def test_38_be_ref_01_to_03_regression_remains_intact():
    inviter = CustomerProfileFactory()
    referred = CustomerProfileFactory()
    manager_user, _ = make_manager(["referrals.approve"])

    code = ReferralService.create_code(inviter)
    referral = ReferralService.redeem_code(referred, code.code)
    ReferralService.approve(referral.pk, manager_user)
    assert PointService.get_balance(inviter.user) == 30

    now = timezone.now()
    challenge = ReferralChallenge.objects.create(
        name="Regression Challenge",
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=5),
    )
    leaderboard = ReferralChallengeService.leaderboard(challenge)
    assert len(leaderboard) == 1
    assert leaderboard[0]["user_id"] == inviter.user_id


def test_39_be_rew_02_to_10_regression_remains_intact():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer = user.active_profile

    tx = RewardEngine.grant_account_onboarding_reward(user, customer)
    assert tx is not None
    assert PointService.get_balance(user) == 100

    rule = RewardRuleService.get_rule(RewardRuleCode.SUCCESSFUL_SALE)
    assert rule.enabled is True
    assert rule.points == 100
