from datetime import timedelta
from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.deals.models import (
    CompletedDeal,
    CompletedDealSourceType,
    CompletedDealType,
)
from apps.loyalty.constants import RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.services import PointService
from apps.notifications.models import Notification
from apps.seasons.models import (
    DEFAULT_SEASON_PRIZE_POINTS,
    Season,
    SeasonPrize,
    SeasonSellerInsight,
    SeasonStatus,
)
from apps.seasons.services import (
    SeasonPayoutService,
    SeasonPrizeService,
    SeasonScoreService,
    SeasonService,
)
from apps.users.constants import ProfileType
from tests.factories import (
    CompletedDealFactory,
    CustomerProfileFactory,
    ManagerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

migration_0003 = import_module("apps.seasons.migrations.0003_season_prize")
backfill_non_finalized_season_prizes = (
    migration_0003.backfill_non_finalized_season_prizes
)

pytestmark = pytest.mark.django_db

_DEAL_SEQ = 20000


def next_source_id() -> int:
    global _DEAL_SEQ
    _DEAL_SEQ += 1
    return _DEAL_SEQ


def auth_client(user, lang="en") -> APIClient:
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE=lang,
    )
    return client


def make_manager(permissions=None):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager = user.active_profile
    if permissions is None:
        permissions = [
            "seasons.list",
            "seasons.create",
            "seasons.update",
            "seasons.calculate",
            "seasons.finalize",
            "seasons.insights.list",
            "seasons.prizes.list",
            "seasons.prizes.create",
            "seasons.prizes.update",
            "seasons.prizes.delete",
        ]
    manager.permissions = list(permissions)
    manager.save(update_fields=["permissions", "modified"])
    return user, manager


def make_future_draft_season(*, title="Future Draft Season"):
    now = timezone.now()
    return Season.objects.create(
        title=title,
        starts_at=now + timedelta(days=5),
        ends_at=now + timedelta(days=35),
        status=SeasonStatus.DRAFT,
    )


def make_active_season(*, title="Active Season"):
    now = timezone.now()
    return Season.objects.create(
        title=title,
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=25),
        status=SeasonStatus.DRAFT,
    )


def make_ended_season(*, status=SeasonStatus.DRAFT, title="Ended Season"):
    now = timezone.now()
    return Season.objects.create(
        title=title,
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(hours=1),
        status=status,
    )


def move_season_to_ended(season: Season):
    now = timezone.now()
    Season.objects.filter(pk=season.pk).update(
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(hours=1),
    )
    season.refresh_from_db()
    return season


def create_completed_deal(
    *,
    seller,
    buyer=None,
    completed_at,
    amount=Decimal("100.00"),
):
    if buyer is None:
        buyer = CustomerProfileFactory()
    return CompletedDealFactory(
        deal_type=CompletedDealType.MARKET,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        buyer_user=buyer.user,
        buyer_profile=buyer,
        seller_user=seller.user,
        seller_profile=seller,
        amount=amount,
        currency="EGP",
        completed_at=completed_at,
    )


def create_four_ranked_sellers(season: Season):
    sellers = [SellerProfileFactory() for _ in range(4)]
    sales_counts = [4, 3, 2, 1]
    base_time = season.starts_at + timedelta(days=1)
    for seller, count in zip(sellers, sales_counts):
        for offset in range(count):
            create_completed_deal(
                seller=seller,
                completed_at=base_time + timedelta(minutes=offset),
            )
    return sellers


def season_winner_txs(*, season=None, seller=None):
    qs = PointTransaction.objects.filter(
        deleted__isnull=True,
        source_type=RewardSourceType.SEASON_WINNER,
    )
    if season is not None:
        qs = qs.filter(
            source_id=str(season.pk),
        )
    if seller is not None:
        qs = qs.filter(profile_id=seller.pk)
    return qs.order_by("id")


# ---------------------------------------------------------------------------
# 1 - 5. Default & Model Tests
# ---------------------------------------------------------------------------


def test_01_new_season_automatically_gets_default_prizes():
    season = make_future_draft_season()
    prizes = list(season.prizes.order_by("rank"))
    assert len(prizes) == 3
    assert [(p.rank, p.pp_points, p.symbolic_prize_code, p.title) for p in prizes] == [
        (1, 100000, "", ""),
        (2, 50000, "", ""),
        (3, 25000, "", ""),
    ]

    # Also verify API creation sets created_by / modified_by and returns prizes
    user, manager = make_manager(["seasons.create", "seasons.list"])
    now = timezone.now()
    res = auth_client(user).post(
        "/api/seasons/",
        {
            "title": "API Created Season",
            "starts_at": (now + timedelta(days=10)).isoformat(),
            "ends_at": (now + timedelta(days=40)).isoformat(),
        },
        format="json",
    )
    assert res.status_code == 201
    assert [(p["rank"], p["pp_points"]) for p in res.data["prizes"]] == [
        (1, 100000),
        (2, 50000),
        (3, 25000),
    ]
    api_season = Season.objects.get(pk=res.data["id"])
    for p in api_season.prizes.all():
        assert p.created_by_id == manager.pk
        assert p.modified_by_id == manager.pk


def test_02_default_prizes_preserve_be_sea_02_finalization_payout_behavior():
    season = make_ended_season()
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season)

    assert PointService.get_balance(sellers[0].user) == 100000
    assert PointService.get_balance(sellers[1].user) == 50000
    assert PointService.get_balance(sellers[2].user) == 25000
    assert PointService.get_balance(sellers[3].user) == 0


def test_03_unique_season_rank_constraint_enforced():
    user, manager = make_manager()
    season = make_future_draft_season()

    # Model / service validation rejects duplicate rank 1
    with pytest.raises(DRFValidationError) as exc_info:
        SeasonPrizeService.create_prize(
            season,
            rank=1,
            pp_points=200000,
            manager_profile=manager,
        )
    assert "rank" in exc_info.value.detail

    # API rejects duplicate rank 1
    res = auth_client(user).post(
        f"/api/seasons/{season.pk}/prizes/",
        {"rank": 1, "pp_points": 200000},
        format="json",
    )
    assert res.status_code == 400
    assert "rank" in res.data

    # DB constraint season_prize_rank_uniq rejects raw bulk_create bypass
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SeasonPrize.objects.bulk_create(
                [SeasonPrize(season=season, rank=1, pp_points=200000)]
            )


def test_04_negative_pp_points_rejected():
    user, manager = make_manager()
    season = make_future_draft_season()
    prize_1 = season.prizes.get(rank=1)

    res = auth_client(user).patch(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
        {"pp_points": -100},
        format="json",
    )
    assert res.status_code == 400
    assert "pp_points" in res.data

    with pytest.raises(DRFValidationError) as exc_info:
        SeasonPrizeService.update_prize(
            prize_1,
            pp_points=-1,
            manager_profile=manager,
        )
    assert "pp_points" in exc_info.value.detail

    with pytest.raises(DjangoValidationError):
        prize_1.pp_points = -50
        prize_1.full_clean()


def test_05_unsupported_rank_rejected():
    user, manager = make_manager()
    season = make_future_draft_season()
    prize_3 = season.prizes.get(rank=3)
    SeasonPrizeService.delete_prize(prize_3, manager_profile=manager)

    for bad_rank in (0, 4, -1):
        res = auth_client(user).post(
            f"/api/seasons/{season.pk}/prizes/",
            {"rank": bad_rank, "pp_points": 10000},
            format="json",
        )
        assert res.status_code == 400
        assert "rank" in res.data

        with pytest.raises(DRFValidationError) as exc_info:
            SeasonPrizeService.create_prize(
                season,
                rank=bad_rank,
                pp_points=10000,
                manager_profile=manager,
            )
        assert "rank" in exc_info.value.detail

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                SeasonPrize.objects.bulk_create(
                    [SeasonPrize(season=season, rank=bad_rank, pp_points=10000)]
                )


# ---------------------------------------------------------------------------
# 6 - 16. Editability & Freeze Tests
# ---------------------------------------------------------------------------


def test_06_manager_can_list_prizes_for_season():
    user, _ = make_manager(["seasons.prizes.list"])
    season = make_future_draft_season()

    res = auth_client(user).get(f"/api/seasons/{season.pk}/prizes/")
    assert res.status_code == 200
    items = res.data["results"] if isinstance(res.data, dict) else res.data
    assert [(item["rank"], item["pp_points"]) for item in items] == [
        (1, 100000),
        (2, 50000),
        (3, 25000),
    ]


def test_07_manager_can_update_prize_pp_points_in_draft_before_starts_at():
    user, manager = make_manager(["seasons.prizes.update"])
    season = make_future_draft_season()
    prize_1 = season.prizes.get(rank=1)

    res = auth_client(user).patch(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
        {"pp_points": 150000},
        format="json",
    )
    assert res.status_code == 200
    assert res.data["pp_points"] == 150000
    prize_1.refresh_from_db()
    assert prize_1.pp_points == 150000
    assert prize_1.modified_by_id == manager.pk


def test_08_manager_can_update_symbolic_prize_code_and_title_in_draft_before_starts_at():
    user, manager = make_manager(["seasons.prizes.update"])
    season = make_future_draft_season()
    prize_1 = season.prizes.get(rank=1)

    res = auth_client(user).patch(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
        {
            "symbolic_prize_code": "GOLD_TROPHY",
            "title": "Golden Champion Cup",
            "metadata": {"badge_hint": "gold"},
        },
        format="json",
    )
    assert res.status_code == 200
    prize_1.refresh_from_db()
    assert prize_1.symbolic_prize_code == "GOLD_TROPHY"
    assert prize_1.title == "Golden Champion Cup"
    assert prize_1.metadata == {"badge_hint": "gold"}
    assert prize_1.modified_by_id == manager.pk


def test_09_manager_can_create_missing_rank_prize_in_draft_before_starts_at():
    user, manager = make_manager(["seasons.prizes.delete", "seasons.prizes.create"])
    season = make_future_draft_season()
    prize_3 = season.prizes.get(rank=3)
    SeasonPrizeService.delete_prize(prize_3, manager_profile=manager)
    assert season.prizes.count() == 2

    res = auth_client(user).post(
        f"/api/seasons/{season.pk}/prizes/",
        {
            "rank": 3,
            "pp_points": 35000,
            "symbolic_prize_code": "BRONZE_TROPHY",
            "title": "Bronze Champion Cup",
        },
        format="json",
    )
    assert res.status_code == 201
    assert res.data["rank"] == 3
    assert res.data["pp_points"] == 35000
    assert res.data["symbolic_prize_code"] == "BRONZE_TROPHY"
    assert res.data["title"] == "Bronze Champion Cup"
    assert res.data["created_by"] == manager.pk
    assert res.data["modified_by"] == manager.pk
    assert season.prizes.count() == 3


def test_10_manager_can_delete_rank_prize_in_draft_before_starts_at():
    user, _ = make_manager(["seasons.prizes.delete"])
    season = make_future_draft_season()
    prize_3 = season.prizes.get(rank=3)

    res = auth_client(user).delete(f"/api/seasons/{season.pk}/prizes/{prize_3.pk}/")
    assert res.status_code == 204
    assert not season.prizes.filter(rank=3).exists()
    assert SeasonPrize.all_objects.get(pk=prize_3.pk).deleted is not None


def test_11_prize_create_rejected_once_now_gte_season_starts_at():
    user, manager = make_manager()
    season = make_future_draft_season()
    prize_3 = season.prizes.get(rank=3)
    SeasonPrizeService.delete_prize(prize_3, manager_profile=manager)

    # Move season into active window (starts_at <= now)
    Season.objects.filter(pk=season.pk).update(
        starts_at=timezone.now() - timedelta(minutes=5)
    )
    season.refresh_from_db()

    res = auth_client(user).post(
        f"/api/seasons/{season.pk}/prizes/",
        {"rank": 3, "pp_points": 25000},
        format="json",
    )
    assert res.status_code == 400
    assert "Season prizes cannot be modified after the season starts" in str(res.data)

    with pytest.raises(DRFValidationError):
        SeasonPrizeService.create_prize(season, rank=3, pp_points=25000)


def test_12_prize_update_rejected_once_now_gte_season_starts_at():
    user, _ = make_manager()
    season = make_active_season()
    prize_1 = season.prizes.get(rank=1)

    for payload in (
        {"pp_points": 200000},
        {"symbolic_prize_code": "GOLD"},
        {"title": "New Title"},
        {"rank": 2},
    ):
        res = auth_client(user).patch(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
            payload,
            format="json",
        )
        assert res.status_code == 400
        assert "Season prizes cannot be modified after the season starts" in str(
            res.data
        )

    prize_1.refresh_from_db()
    assert prize_1.pp_points == 100000


def test_13_prize_delete_rejected_once_now_gte_season_starts_at():
    user, _ = make_manager()
    season = make_active_season()
    prize_3 = season.prizes.get(rank=3)

    res = auth_client(user).delete(f"/api/seasons/{season.pk}/prizes/{prize_3.pk}/")
    assert res.status_code == 400
    assert "Season prizes cannot be modified after the season starts" in str(res.data)

    with pytest.raises(DRFValidationError):
        SeasonPrizeService.delete_prize(prize_3)

    assert season.prizes.filter(rank=3).exists()


def test_14_prize_edits_rejected_when_season_status_is_calculated():
    user, _ = make_manager()
    season = make_future_draft_season()
    Season.objects.filter(pk=season.pk).update(status=SeasonStatus.CALCULATED)
    season.refresh_from_db()
    prize_1 = season.prizes.get(rank=1)

    res_patch = auth_client(user).patch(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
        {"pp_points": 180000},
        format="json",
    )
    assert res_patch.status_code == 400
    assert "Season prizes can only be modified while the season is in draft" in str(
        res_patch.data
    )

    res_del = auth_client(user).delete(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/"
    )
    assert res_del.status_code == 400


def test_15_prize_edits_rejected_when_season_status_is_finalized():
    user, manager = make_manager()
    season = make_ended_season()
    SeasonService.finalize(season, manager_profile=manager)
    prize_1 = season.prizes.get(rank=1)

    res_patch = auth_client(user).patch(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
        {"pp_points": 180000},
        format="json",
    )
    assert res_patch.status_code == 400
    assert "Finalized season prizes cannot be modified" in str(res_patch.data)

    res_del = auth_client(user).delete(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/"
    )
    assert res_del.status_code == 400
    assert "Finalized season prizes cannot be modified" in str(res_del.data)

    res_post = auth_client(user).post(
        f"/api/seasons/{season.pk}/prizes/",
        {"rank": 1, "pp_points": 180000},
        format="json",
    )
    assert res_post.status_code == 400


def test_16_moving_draft_season_starts_at_into_past_immediately_blocks_subsequent_prize_edits():
    user, _ = make_manager()
    season = make_future_draft_season()
    prize_1 = season.prizes.get(rank=1)

    # First edit succeeds while starts_at is in the future
    first_patch = auth_client(user).patch(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
        {"pp_points": 140000},
        format="json",
    )
    assert first_patch.status_code == 200

    # Manager updates season starts_at into the past via PATCH /api/seasons/<id>/
    now = timezone.now()
    season_patch = auth_client(user).patch(
        f"/api/seasons/{season.pk}/",
        {"starts_at": (now - timedelta(hours=1)).isoformat()},
        format="json",
    )
    assert season_patch.status_code == 200

    # Subsequent prize edit is immediately blocked
    second_patch = auth_client(user).patch(
        f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
        {"pp_points": 160000},
        format="json",
    )
    assert second_patch.status_code == 400
    assert "Season prizes cannot be modified after the season starts" in str(
        second_patch.data
    )
    prize_1.refresh_from_db()
    assert prize_1.pp_points == 140000


# ---------------------------------------------------------------------------
# 17 - 25. Finalization & Payout Tests
# ---------------------------------------------------------------------------


def test_17_customized_prizes_paid_on_finalization_instead_of_defaults():
    _, manager = make_manager()
    season = make_future_draft_season()

    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=150000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=2), pp_points=75000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=3), pp_points=30000, manager_profile=manager
    )

    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season, manager_profile=manager)

    assert PointService.get_balance(sellers[0].user) == 150000
    assert PointService.get_balance(sellers[1].user) == 75000
    assert PointService.get_balance(sellers[2].user) == 30000
    assert PointService.get_balance(sellers[3].user) == 0

    insights = list(SeasonService.insights(season))
    assert [i.points_awarded for i in insights] == [150000, 75000, 30000, 0]


def test_18_rank_4_plus_still_receives_0_pp_with_custom_prizes():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=250000, manager_profile=manager
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season, manager_profile=manager)

    rank_4_insight = SeasonSellerInsight.objects.get(season=season, seller=sellers[3])
    assert rank_4_insight.rank == 4
    assert rank_4_insight.points_awarded == 0
    assert "season_prize" not in (rank_4_insight.metadata or {})
    assert not season_winner_txs(season=season, seller=sellers[3]).exists()
    assert PointService.get_balance(sellers[3].user) == 0


def test_19_only_2_qualifying_sellers_pays_ranks_1_and_2_using_configured_prizes():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=180000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=2), pp_points=90000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=3), pp_points=45000, manager_profile=manager
    )
    move_season_to_ended(season)

    s1 = SellerProfileFactory()
    s2 = SellerProfileFactory()
    t = season.starts_at + timedelta(days=2)
    create_completed_deal(seller=s1, completed_at=t)
    create_completed_deal(seller=s1, completed_at=t)
    create_completed_deal(seller=s2, completed_at=t)

    SeasonService.finalize(season, manager_profile=manager)

    txs = list(season_winner_txs(season=season))
    assert len(txs) == 2
    assert [(tx.profile_id, tx.points) for tx in txs] == [
        (s1.pk, 180000),
        (s2.pk, 90000),
    ]


def test_20_only_1_qualifying_seller_pays_rank_1_using_configured_prize():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=180000, manager_profile=manager
    )
    move_season_to_ended(season)

    s1 = SellerProfileFactory()
    create_completed_deal(
        seller=s1, completed_at=season.starts_at + timedelta(days=2)
    )

    SeasonService.finalize(season, manager_profile=manager)

    txs = list(season_winner_txs(season=season))
    assert len(txs) == 1
    assert txs[0].profile_id == s1.pk
    assert txs[0].points == 180000


def test_21_zero_qualifying_sellers_creates_0_payouts_and_finalizes_cleanly():
    _, manager = make_manager()
    season = make_future_draft_season()
    # Even if all prizes were deleted before start, 0 qualifying sellers needs 0 prizes
    for p in list(season.prizes.all()):
        SeasonPrizeService.delete_prize(p, manager_profile=manager)
    move_season_to_ended(season)

    finalized = SeasonService.finalize(season, manager_profile=manager)
    assert finalized.status == SeasonStatus.FINALIZED
    assert season_winner_txs(season=season).count() == 0


def test_22_configured_pp_points_zero_for_rank_3_creates_no_transaction_and_succeeds():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1),
        pp_points=120000,
        symbolic_prize_code="GOLD_MEDAL",
        title="Gold Medal",
        manager_profile=manager,
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=2),
        pp_points=60000,
        symbolic_prize_code="SILVER_MEDAL",
        title="Silver Medal",
        manager_profile=manager,
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=3),
        pp_points=0,
        symbolic_prize_code="HONORARY_BRONZE",
        title="Honorary Bronze Certificate",
        manager_profile=manager,
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    finalized = SeasonService.finalize(season, manager_profile=manager)

    assert finalized.status == SeasonStatus.FINALIZED
    txs = list(season_winner_txs(season=season))
    assert len(txs) == 2
    assert [(tx.profile_id, tx.points) for tx in txs] == [
        (sellers[0].pk, 120000),
        (sellers[1].pk, 60000),
    ]
    assert not season_winner_txs(season=season, seller=sellers[2]).exists()
    assert PointService.get_balance(sellers[2].user) == 0

    rank_3_insight = SeasonSellerInsight.objects.get(season=season, seller=sellers[2])
    assert rank_3_insight.points_awarded == 0
    assert rank_3_insight.metadata["season_prize"] == {
        "rank": 3,
        "pp_points": 0,
        "symbolic_prize_code": "HONORARY_BRONZE",
        "title": "Honorary Bronze Certificate",
    }


def test_23_missing_required_prize_config_for_qualifying_top_3_rank_fails_before_any_payout():
    user, manager = make_manager()
    season = make_future_draft_season()
    # Delete rank 3 prize while in DRAFT before starts_at
    SeasonPrizeService.delete_prize(
        season.prizes.get(rank=3),
        manager_profile=manager,
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    res = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert res.status_code == 400
    assert "Season prize configuration is incomplete" in str(res.data)

    season.refresh_from_db()
    assert season.status != SeasonStatus.FINALIZED
    assert season.finalized_at is None
    assert season.finalized_by is None
    assert season_winner_txs(season=season).count() == 0
    for seller in sellers:
        assert PointService.get_balance(seller.user) == 0


def test_24_missing_rank_3_prize_when_only_2_sellers_qualify_succeeds():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=140000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=2), pp_points=70000, manager_profile=manager
    )
    SeasonPrizeService.delete_prize(
        season.prizes.get(rank=3), manager_profile=manager
    )
    move_season_to_ended(season)

    s1 = SellerProfileFactory()
    s2 = SellerProfileFactory()
    t = season.starts_at + timedelta(days=2)
    create_completed_deal(seller=s1, completed_at=t)
    create_completed_deal(seller=s1, completed_at=t)
    create_completed_deal(seller=s2, completed_at=t)

    finalized = SeasonService.finalize(season, manager_profile=manager)
    assert finalized.status == SeasonStatus.FINALIZED
    assert PointService.get_balance(s1.user) == 140000
    assert PointService.get_balance(s2.user) == 70000


def test_25_winner_insight_metadata_stores_frozen_season_prize_snapshot_on_finalization():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1),
        pp_points=200000,
        symbolic_prize_code="GOLD_CUP",
        title="Gold Cup",
        manager_profile=manager,
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=2),
        pp_points=100000,
        symbolic_prize_code="SILVER_CUP",
        title="Silver Cup",
        manager_profile=manager,
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=3),
        pp_points=50000,
        symbolic_prize_code="BRONZE_CUP",
        title="Bronze Cup",
        manager_profile=manager,
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    badges_before = BadgeAward.objects.count()
    notifs_before = Notification.objects.count()

    SeasonService.finalize(season, manager_profile=manager)

    insights = list(SeasonService.insights(season))
    assert insights[0].metadata["season_prize"] == {
        "rank": 1,
        "pp_points": 200000,
        "symbolic_prize_code": "GOLD_CUP",
        "title": "Gold Cup",
    }
    assert insights[1].metadata["season_prize"] == {
        "rank": 2,
        "pp_points": 100000,
        "symbolic_prize_code": "SILVER_CUP",
        "title": "Silver Cup",
    }
    assert insights[2].metadata["season_prize"] == {
        "rank": 3,
        "pp_points": 50000,
        "symbolic_prize_code": "BRONZE_CUP",
        "title": "Bronze Cup",
    }
    assert "season_prize" not in (insights[3].metadata or {})

    # Symbolic prize codes do not trigger badge awards or notifications
    assert BadgeAward.objects.count() == badges_before
    assert Notification.objects.count() == notifs_before


# ---------------------------------------------------------------------------
# 26 - 31. Idempotency & Legacy Compatibility Tests
# ---------------------------------------------------------------------------


def test_26_repeated_finalize_with_custom_prizes_pays_only_once():
    user, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=150000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=2), pp_points=75000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=3), pp_points=30000, manager_profile=manager
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    res_1 = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert res_1.status_code == 200

    res_2 = auth_client(user).post(f"/api/seasons/{season.pk}/finalize/")
    assert res_2.status_code == 200

    SeasonService.finalize(season, manager_profile=manager)

    assert season_winner_txs(season=season).count() == 3
    assert PointService.get_balance(sellers[0].user) == 150000
    assert PointService.get_balance(sellers[1].user) == 75000
    assert PointService.get_balance(sellers[2].user) == 30000


def test_27_idempotency_key_remains_rank_and_amount_independent():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=333000, manager_profile=manager
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    SeasonService.finalize(season, manager_profile=manager)

    for rank_idx in range(3):
        seller = sellers[rank_idx]
        tx = season_winner_txs(season=season, seller=seller).get()
        assert (
            tx.idempotency_key
            == f"reward:season_winner:season:{season.pk}:seller:{seller.pk}"
        )


def test_28_historical_legacy_season_winner_tx_blocks_new_payout_when_custom_prize_differs():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=180000, manager_profile=manager
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    # Seed historical legacy rank-based transaction for seller 0 at 100,000 PP
    PointService.earn_points(
        user=sellers[0].user,
        profile=sellers[0],
        points=100000,
        reason="Legacy season winner rank 1",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{sellers[0].pk}:rank:1",
        metadata={"season_id": season.pk, "rank": 1},
    )

    SeasonService.finalize(season, manager_profile=manager)

    assert season_winner_txs(season=season, seller=sellers[0]).count() == 1
    assert PointService.get_balance(sellers[0].user) == 100000


def test_29_historical_legacy_payout_not_topped_up_to_higher_custom_pp_points():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=200000, manager_profile=manager
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    # Seller 0 was paid 50,000 PP at rank 2 under legacy code, now finishes rank 1 (200,000 PP)
    PointService.earn_points(
        user=sellers[0].user,
        profile=sellers[0],
        points=50000,
        reason="Legacy season winner rank 2",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{sellers[0].pk}:rank:2",
        metadata={"season_id": season.pk, "rank": 2},
    )

    SeasonService.finalize(season, manager_profile=manager)

    assert season_winner_txs(season=season, seller=sellers[0]).count() == 1
    assert PointService.get_balance(sellers[0].user) == 50000
    insight_1 = SeasonSellerInsight.objects.get(season=season, seller=sellers[0])
    assert insight_1.points_awarded == 50000


def test_30_historical_legacy_payout_not_clawed_back_when_custom_pp_points_is_lower():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=30000, manager_profile=manager
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    # Seller 0 was paid 100,000 PP under legacy code, configured rank 1 is 30,000 PP
    PointService.earn_points(
        user=sellers[0].user,
        profile=sellers[0],
        points=100000,
        reason="Legacy season winner rank 1",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{sellers[0].pk}:rank:1",
        metadata={"season_id": season.pk, "rank": 1},
    )

    SeasonService.finalize(season, manager_profile=manager)

    assert season_winner_txs(season=season, seller=sellers[0]).count() == 1
    assert PointService.get_balance(sellers[0].user) == 100000
    insight_1 = SeasonSellerInsight.objects.get(season=season, seller=sellers[0])
    assert insight_1.points_awarded == 100000


def test_31_failure_mid_payout_rolls_back_all_custom_payouts_and_leaves_season_unfinalized(
    monkeypatch,
):
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=160000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=2), pp_points=80000, manager_profile=manager
    )
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=3), pp_points=40000, manager_profile=manager
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    real_earn_points = PointService.earn_points
    call_count = {"n": 0}

    def failing_on_second_winner(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("Simulated mid-payout failure on rank 2")
        return real_earn_points(*args, **kwargs)

    monkeypatch.setattr(PointService, "earn_points", failing_on_second_winner)

    with pytest.raises(RuntimeError, match="Simulated mid-payout failure"):
        SeasonService.finalize(season, manager_profile=manager)

    season.refresh_from_db()
    assert season.status == SeasonStatus.DRAFT
    assert season.finalized_at is None
    assert season.finalized_by is None
    assert season_winner_txs(season=season).count() == 0
    for seller in sellers:
        assert PointService.get_balance(seller.user) == 0


# ---------------------------------------------------------------------------
# 32 - 33. Migration / Existing Season Tests
# ---------------------------------------------------------------------------


def test_32_non_finalized_existing_season_without_prizes_gets_default_prizes_in_backfill():
    now = timezone.now()
    draft_season = Season(
        title="Legacy Draft Season",
        starts_at=now - timedelta(days=20),
        ends_at=now + timedelta(days=10),
        status=SeasonStatus.DRAFT,
    )
    draft_season._skip_default_prizes = True
    draft_season.save()

    calculated_season = Season(
        title="Legacy Calculated Season",
        starts_at=now - timedelta(days=40),
        ends_at=now - timedelta(days=10),
        status=SeasonStatus.CALCULATED,
    )
    calculated_season._skip_default_prizes = True
    calculated_season.save()

    assert draft_season.prizes.count() == 0
    assert calculated_season.prizes.count() == 0

    backfill_non_finalized_season_prizes(django_apps, None)
    # Run twice to verify idempotency
    backfill_non_finalized_season_prizes(django_apps, None)

    for s in (draft_season, calculated_season):
        prizes = list(s.prizes.order_by("rank"))
        assert [(p.rank, p.pp_points) for p in prizes] == [
            (1, 100000),
            (2, 50000),
            (3, 25000),
        ]
    assert PointTransaction.objects.count() == 0


def test_33_finalized_historical_season_is_not_recalculated_or_repaid_by_backfill():
    now = timezone.now()
    finalized_season = Season(
        title="Legacy Finalized Season",
        starts_at=now - timedelta(days=60),
        ends_at=now - timedelta(days=30),
        status=SeasonStatus.FINALIZED,
    )
    finalized_season._skip_default_prizes = True
    finalized_season.save()

    seller = SellerProfileFactory()
    insight = SeasonSellerInsight.objects.create(
        season=finalized_season,
        seller=seller,
        rank=1,
        season_score=Decimal("88.50"),
        points_awarded=100000,
    )
    PointService.earn_points(
        user=seller.user,
        profile=seller,
        points=100000,
        reason="Legacy finalized season winner",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(finalized_season.pk),
        idempotency_key=f"season:{finalized_season.pk}:seller:{seller.pk}:rank:1",
    )

    backfill_non_finalized_season_prizes(django_apps, None)

    assert finalized_season.prizes.count() == 0
    assert season_winner_txs(season=finalized_season).count() == 1
    assert PointService.get_balance(seller.user) == 100000
    insight.refresh_from_db()
    assert insight.season_score == Decimal("88.50")
    assert insight.points_awarded == 100000


# ---------------------------------------------------------------------------
# 34 - 37. Permission Tests
# ---------------------------------------------------------------------------


def test_34_customer_forbidden_from_season_prize_endpoints():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    season = make_future_draft_season()
    prize_1 = season.prizes.get(rank=1)
    client = auth_client(customer)

    assert client.get(f"/api/seasons/{season.pk}/prizes/").status_code == 403
    assert (
        client.post(
            f"/api/seasons/{season.pk}/prizes/",
            {"rank": 1, "pp_points": 100000},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
            {"pp_points": 120000},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/"
        ).status_code
        == 403
    )


def test_35_seller_forbidden_from_season_prize_endpoints():
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    season = make_future_draft_season()
    prize_1 = season.prizes.get(rank=1)
    client = auth_client(seller_user)

    assert client.get(f"/api/seasons/{season.pk}/prizes/").status_code == 403
    assert (
        client.post(
            f"/api/seasons/{season.pk}/prizes/",
            {"rank": 1, "pp_points": 100000},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
            {"pp_points": 120000},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/"
        ).status_code
        == 403
    )


def test_36_manager_without_season_prizes_permissions_forbidden():
    user, _ = make_manager(
        [
            "seasons.list",
            "seasons.create",
            "seasons.update",
            "seasons.calculate",
            "seasons.finalize",
            "seasons.insights.list",
        ]
    )
    season = make_future_draft_season()
    prize_1 = season.prizes.get(rank=1)
    client = auth_client(user)

    assert client.get(f"/api/seasons/{season.pk}/prizes/").status_code == 403
    assert (
        client.post(
            f"/api/seasons/{season.pk}/prizes/",
            {"rank": 1, "pp_points": 100000},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
            {"pp_points": 120000},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/"
        ).status_code
        == 403
    )


def test_37_manager_with_exact_season_prizes_permissions_allowed():
    season = make_future_draft_season()
    prize_3 = season.prizes.get(rank=3)

    # 1. List-only manager
    u_list, _ = make_manager(["seasons.prizes.list"])
    assert auth_client(u_list).get(f"/api/seasons/{season.pk}/prizes/").status_code == 200
    assert (
        auth_client(u_list)
        .patch(
            f"/api/seasons/{season.pk}/prizes/{prize_3.pk}/",
            {"pp_points": 40000},
            format="json",
        )
        .status_code
        == 403
    )

    # 2. Update-only manager
    u_update, _ = make_manager(["seasons.prizes.update"])
    assert (
        auth_client(u_update)
        .patch(
            f"/api/seasons/{season.pk}/prizes/{prize_3.pk}/",
            {"pp_points": 40000},
            format="json",
        )
        .status_code
        == 200
    )
    assert auth_client(u_update).get(f"/api/seasons/{season.pk}/prizes/").status_code == 403

    # 3. Delete-only manager
    u_delete, _ = make_manager(["seasons.prizes.delete"])
    assert (
        auth_client(u_delete)
        .delete(f"/api/seasons/{season.pk}/prizes/{prize_3.pk}/")
        .status_code
        == 204
    )

    # 4. Create-only manager
    u_create, _ = make_manager(["seasons.prizes.create"])
    assert (
        auth_client(u_create)
        .post(
            f"/api/seasons/{season.pk}/prizes/",
            {"rank": 3, "pp_points": 30000},
            format="json",
        )
        .status_code
        == 201
    )


# ---------------------------------------------------------------------------
# 38 - 41. Additional Safety & Regression Tests
# ---------------------------------------------------------------------------


def test_38_score_recalculation_creates_0_pp_and_does_not_mutate_custom_prizes():
    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1),
        pp_points=220000,
        symbolic_prize_code="GOLD_CROWN",
        title="Gold Crown",
        manager_profile=manager,
    )
    move_season_to_ended(season)
    sellers = create_four_ranked_sellers(season)

    SeasonService.calculate_winners(season, manager_profile=manager)
    SeasonScoreService.recalculate(season, manager_profile=manager)

    assert season_winner_txs(season=season).count() == 0
    for seller in sellers:
        assert PointService.get_balance(seller.user) == 0

    prize_1 = season.prizes.get(rank=1)
    assert prize_1.pp_points == 220000
    assert prize_1.symbolic_prize_code == "GOLD_CROWN"
    assert prize_1.title == "Gold Crown"


def test_39_post_finalization_season_and_prize_immutability():
    user, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1),
        pp_points=175000,
        symbolic_prize_code="PLATINUM_1",
        title="Platinum Rank 1",
        manager_profile=manager,
    )
    move_season_to_ended(season)
    create_four_ranked_sellers(season)

    SeasonService.finalize(season, manager_profile=manager)

    client = auth_client(user)
    assert client.post(f"/api/seasons/{season.pk}/calculate-winners/").status_code == 400
    assert (
        client.patch(
            f"/api/seasons/{season.pk}/",
            {"ends_at": (timezone.now() + timedelta(days=5)).isoformat()},
            format="json",
        ).status_code
        == 400
    )
    prize_1 = season.prizes.get(rank=1)
    assert (
        client.patch(
            f"/api/seasons/{season.pk}/prizes/{prize_1.pk}/",
            {"pp_points": 999999},
            format="json",
        ).status_code
        == 400
    )

    insights_res = client.get(f"/api/seasons/{season.pk}/seller-insights/")
    assert insights_res.status_code == 200
    items = (
        insights_res.data["results"]
        if isinstance(insights_res.data, dict)
        else insights_res.data
    )
    assert items[0]["points_awarded"] == 175000
    assert items[0]["metadata"]["season_prize"]["pp_points"] == 175000
    assert items[0]["metadata"]["season_prize"]["symbolic_prize_code"] == "PLATINUM_1"


def test_40_prize_endpoint_returns_404_for_unknown_season_or_mismatched_season_prize():
    user, _ = make_manager()
    s1 = make_future_draft_season(title="S1")
    s2 = make_future_draft_season(title="S2")
    s2_prize_1 = s2.prizes.get(rank=1)
    client = auth_client(user)

    assert client.get("/api/seasons/999999/prizes/").status_code == 404
    assert (
        client.patch(
            f"/api/seasons/{s1.pk}/prizes/{s2_prize_1.pk}/",
            {"pp_points": 150000},
            format="json",
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/seasons/{s1.pk}/prizes/{s2_prize_1.pk}/"
        ).status_code
        == 404
    )


def test_41_reward_and_referral_regressions_unaffected_by_season_prizes():
    from apps.loyalty.reward_engine import RewardEngine
    from apps.referrals.services import ReferralService

    _, manager = make_manager()
    season = make_future_draft_season()
    SeasonPrizeService.update_prize(
        season.prizes.get(rank=1), pp_points=145000, manager_profile=manager
    )
    move_season_to_ended(season)

    seller = SellerProfileFactory()
    buyer = CustomerProfileFactory()

    # 1. Onboarding reward (BE-REW-02: 100 PP once per UserAccount)
    RewardEngine.grant_account_onboarding_reward(seller.user, seller)
    assert PointService.get_balance(seller.user) == 100

    # 2. Referral approval (BE-REF-01: 30 PP)
    inviter = CustomerProfileFactory()
    new_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    code = ReferralService.create_code(inviter)
    ref = ReferralService.redeem_code(new_user.active_profile, code.code)
    ReferralService.approve(ref.pk, manager.user)
    assert PointService.get_balance(inviter.user) == 30

    # 3. Season deal + finalize (BE-SEA-03: 145,000 PP on top of 100 PP onboarding)
    create_completed_deal(
        seller=seller,
        buyer=buyer,
        completed_at=season.starts_at + timedelta(days=2),
    )
    SeasonService.finalize(season, manager_profile=manager)

    assert PointService.get_balance(seller.user) == 145100
    assert PointService.get_balance(inviter.user) == 30
