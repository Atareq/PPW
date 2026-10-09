"""BE-LW-02: Lucky Wheel spin outcome + prize execution hardening tests."""

from datetime import timedelta
from decimal import Decimal
import secrets
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.draws.models import WeeklyDraw, WeeklyDrawEntry, WeeklyDrawStatus
from apps.loyalty.constants import BadgeType, RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.services import BadgeService, PointService
from apps.lucky_wheel.models import (
    LuckyWheelGrant,
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
    LuckyWheelSpin,
    LuckyWheelSpinStatus,
)
from apps.lucky_wheel.services import (
    LuckyWheelGrantService,
    LuckyWheelPrizeSelector,
    LuckyWheelService,
    LuckyWheelSpinService,
)
from apps.notifications.models import Notification
from apps.promotions.models import (
    CashbackTransaction,
    OfferTriggerType,
    PromotionCampaign,
    PromotionCampaignType,
    UserPromotionGrant,
    UserPromotionGrantStatus,
    UserPromotionGrantType,
)
from apps.promotions.services import (
    CashbackOfferService,
    DiscountOfferService,
    UserPromotionGrantService,
)
from apps.referrals.models import LinkSharingCode, Referral
from apps.seasons.models import Season, SeasonStatus
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile
from tests.factories import (
    CashbackOfferFactory,
    CustomerProfileFactory,
    DiscountOfferFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def auth_client(user=None):
    client = APIClient()
    if user is not None:
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def grant_attempts(profile, attempts=1, source_id="lw02"):
    return LuckyWheelGrantService.grant_attempt(
        profile=profile,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id=f"{source_id}:{profile.pk}:{timezone.now().timestamp()}",
        attempts=attempts,
    )


def create_manager_with_wheel_perms():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    mgr_profile = manager.active_profile
    mgr_profile.permissions = [
        "lucky_wheel.prizes.list",
        "lucky_wheel.prizes.create",
        "lucky_wheel.prizes.update",
        "lucky_wheel.spins.list",
    ]
    mgr_profile.save(update_fields=["permissions"])
    return manager


# ===========================================================================
# 1. Candidate Selection & Randomness (Cases 1–10)
# ===========================================================================


def test_01_only_active_non_deleted_non_exhausted_prizes_with_positive_weight_can_be_selected():
    p_valid_1 = LuckyWheelPrize.objects.create(
        label="Valid Unlimited",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=2,
        is_active=True,
    )
    p_valid_2 = LuckyWheelPrize.objects.create(
        label="Valid Finite",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=3,
        is_active=True,
        quantity_total=5,
        quantity_awarded=4,
    )
    # Inactive
    LuckyWheelPrize.objects.create(
        label="Inactive",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=10,
        is_active=False,
    )
    # Soft-deleted
    p_deleted = LuckyWheelPrize.objects.create(
        label="Deleted",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=10,
        is_active=True,
    )
    p_deleted.delete()
    # Exhausted
    LuckyWheelPrize.objects.create(
        label="Exhausted",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=10,
        is_active=True,
        quantity_total=3,
        quantity_awarded=3,
    )

    available_ids = [p.pk for p in LuckyWheelService.available_prizes()]
    assert available_ids == [p_valid_1.pk, p_valid_2.pk]


def test_02_inactive_prize_is_never_selected():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=5)
    LuckyWheelPrize.objects.create(
        label="Inactive Big Prize",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=1000,
        weight=100,
        is_active=False,
    )
    active_prize = LuckyWheelPrize.objects.create(
        label="Active Small Prize",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=1,
        is_active=True,
    )

    for idx in range(5):
        spin, _ = LuckyWheelSpinService.spin(
            customer.user, customer, f"inactive-never-{idx}"
        )
        assert spin.prize_id == active_prize.pk


def test_03_soft_deleted_prize_is_never_selected():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=3)
    deleted_prize = LuckyWheelPrize.objects.create(
        label="Deleted Prize",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=500,
        weight=50,
        is_active=True,
    )
    deleted_prize.delete()
    active_prize = LuckyWheelPrize.objects.create(
        label="Surviving Prize",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
        is_active=True,
    )

    for idx in range(3):
        spin, _ = LuckyWheelSpinService.spin(
            customer.user, customer, f"deleted-never-{idx}"
        )
        assert spin.prize_id == active_prize.pk


def test_04_exhausted_finite_prize_is_never_selected():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=3)
    LuckyWheelPrize.objects.create(
        label="Exhausted Prize",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=500,
        weight=50,
        is_active=True,
        quantity_total=2,
        quantity_awarded=2,
    )
    fallback_prize = LuckyWheelPrize.objects.create(
        label="Available Prize",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=5,
        weight=1,
        is_active=True,
    )

    for idx in range(3):
        spin, _ = LuckyWheelSpinService.spin(
            customer.user, customer, f"exhausted-never-{idx}"
        )
        assert spin.prize_id == fallback_prize.pk


def test_05_spin_fails_with_400_when_zero_valid_prizes_exist_and_does_not_consume_attempt():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="Inactive Only",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
        is_active=False,
    )
    LuckyWheelPrize.objects.create(
        label="Sold Out Only",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=20,
        weight=5,
        is_active=True,
        quantity_total=1,
        quantity_awarded=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "no-valid-prizes-spin"},
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert LuckyWheelSpin.objects.count() == 0
    assert PointTransaction.objects.count() == 0


def test_06_production_selection_uses_secrets_system_random():
    from apps.lucky_wheel import services as lw_services

    assert isinstance(lw_services._SYSTEM_RANDOM, secrets.SystemRandom)
    p1 = LuckyWheelPrize.objects.create(
        label="SysRandom P1",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=3,
    )
    p2 = LuckyWheelPrize.objects.create(
        label="SysRandom P2",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=15,
        weight=7,
    )

    with patch.object(
        lw_services._SYSTEM_RANDOM,
        "randint",
        return_value=4,
    ) as mock_randint:
        chosen = LuckyWheelPrizeSelector.select([p1, p2])
        mock_randint.assert_called_once_with(1, 10)
        assert chosen.pk == p2.pk


def test_07_boundary_pick_1_selects_first_candidate_in_id_order():
    p1 = LuckyWheelPrize.objects.create(
        label="First Segment",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=3,
    )
    p2 = LuckyWheelPrize.objects.create(
        label="Second Segment",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=5,
    )
    p3 = LuckyWheelPrize.objects.create(
        label="Third Segment",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=25,
        weight=2,
    )
    candidates = list(LuckyWheelService.available_prizes())
    assert [c.pk for c in candidates] == [p1.pk, p2.pk, p3.pk]

    selected = LuckyWheelPrizeSelector.select(candidates, rng=lambda total: 1)
    assert selected.pk == p1.pk


def test_08_boundary_pick_at_cumulative_threshold_selects_expected_candidate():
    p1 = LuckyWheelPrize.objects.create(
        label="Weight 3 (1..3)",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=3,
    )
    p2 = LuckyWheelPrize.objects.create(
        label="Weight 5 (4..8)",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=5,
    )
    p3 = LuckyWheelPrize.objects.create(
        label="Weight 2 (9..10)",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=20,
        weight=2,
    )
    candidates = list(LuckyWheelService.available_prizes())

    # Exact upper boundary of p1 (pick=3) -> p1
    assert LuckyWheelPrizeSelector.select(candidates, rng=lambda _: 3).pk == p1.pk
    # Exact lower boundary of p2 (pick=4) -> p2
    assert LuckyWheelPrizeSelector.select(candidates, rng=lambda _: 4).pk == p2.pk
    # Exact upper boundary of p2 (pick=8) -> p2
    assert LuckyWheelPrizeSelector.select(candidates, rng=lambda _: 8).pk == p2.pk
    # Exact lower boundary of p3 (pick=9) -> p3
    assert LuckyWheelPrizeSelector.select(candidates, rng=lambda _: 9).pk == p3.pk


def test_09_boundary_pick_at_total_weight_selects_last_candidate():
    p1 = LuckyWheelPrize.objects.create(
        label="First",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=4,
    )
    p2 = LuckyWheelPrize.objects.create(
        label="Middle",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=6,
    )
    p3 = LuckyWheelPrize.objects.create(
        label="Last",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=50,
        weight=5,
    )
    candidates = list(LuckyWheelService.available_prizes())

    selected = LuckyWheelPrizeSelector.select(
        candidates, rng=lambda total: total
    )
    assert selected.pk == p3.pk


def test_10_weighted_selection_across_controlled_draws_respects_configured_weights():
    p1 = LuckyWheelPrize.objects.create(
        label="W2",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=2,
    )
    p2 = LuckyWheelPrize.objects.create(
        label="W5",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=5,
    )
    p3 = LuckyWheelPrize.objects.create(
        label="W3",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=30,
        weight=3,
    )
    candidates = list(LuckyWheelService.available_prizes())
    counts = {p1.pk: 0, p2.pk: 0, p3.pk: 0}

    # Exhaustively sweep picks 1..10 ten times (100 controlled draws)
    for cycle in range(10):
        for pick in range(1, 11):
            chosen = LuckyWheelPrizeSelector.select(
                candidates, rng=lambda _total, p=pick: p
            )
            counts[chosen.pk] += 1

    assert counts == {p1.pk: 20, p2.pk: 50, p3.pk: 30}


# ===========================================================================
# 2. Finite Inventory & Concurrency (Cases 11–16)
# ===========================================================================


def test_11_finite_prize_with_quantity_total_1_is_awarded_at_most_once():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=2)
    finite_prize = LuckyWheelPrize.objects.create(
        label="Single Inventory Prize",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=100,
        weight=10,
        quantity_total=1,
        quantity_awarded=0,
    )

    spin1, _ = LuckyWheelSpinService.spin(customer.user, customer, "finite-1-first")
    assert spin1.prize_id == finite_prize.pk
    finite_prize.refresh_from_db()
    assert finite_prize.quantity_awarded == 1

    # Second spin must fail because the only finite prize is now exhausted
    with pytest.raises(ValidationError):
        LuckyWheelSpinService.spin(customer.user, customer, "finite-1-second")

    finite_prize.refresh_from_db()
    grant.refresh_from_db()
    assert finite_prize.quantity_awarded == 1
    assert grant.remaining_attempts == 1
    assert LuckyWheelSpin.objects.filter(prize=finite_prize).count() == 1


def test_12_quantity_awarded_increments_by_1_on_successful_spin():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=2)
    prize = LuckyWheelPrize.objects.create(
        label="Track Inventory",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=15,
        weight=1,
        quantity_total=10,
        quantity_awarded=3,
    )

    LuckyWheelSpinService.spin(customer.user, customer, "inc-1")
    prize.refresh_from_db()
    assert prize.quantity_awarded == 4

    LuckyWheelSpinService.spin(customer.user, customer, "inc-2")
    prize.refresh_from_db()
    assert prize.quantity_awarded == 5


def test_13_after_reaching_quantity_total_prize_is_excluded_from_available_prizes_and_future_spins():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=2)
    finite = LuckyWheelPrize.objects.create(
        label="One Left",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=50,
        weight=100,
        quantity_total=1,
        quantity_awarded=0,
    )
    unlimited = LuckyWheelPrize.objects.create(
        label="Unlimited Fallback",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
        quantity_total=None,
    )

    # First spin picks finite (pick=1)
    spin1, _ = LuckyWheelSpinService.spin(
        customer.user, customer, "cap-spin-1", rng=lambda _: 1
    )
    assert spin1.prize_id == finite.pk

    # finite is now excluded from available_prizes() and GET /api/lucky-wheel/current/
    available_ids = [p.pk for p in LuckyWheelService.available_prizes()]
    assert available_ids == [unlimited.pk]
    current_res = auth_client(customer.user).get("/api/lucky-wheel/current/")
    assert [s["id"] for s in current_res.data["segments"]] == [unlimited.pk]

    # Second spin automatically selects from remaining valid prizes (unlimited)
    spin2, _ = LuckyWheelSpinService.spin(
        customer.user, customer, "cap-spin-2", rng=lambda _: 1
    )
    assert spin2.prize_id == unlimited.pk
    finite.refresh_from_db()
    assert finite.quantity_awarded == 1


def test_14_stale_or_exhausted_prize_at_lock_time_does_not_over_award_and_re_resolves_to_remaining_candidate():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    prize_a = LuckyWheelPrize.objects.create(
        label="Contended Finite Prize A",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=100,
        weight=10,
        quantity_total=1,
        quantity_awarded=0,
    )
    prize_b = LuckyWheelPrize.objects.create(
        label="Available Prize B",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=20,
        weight=1,
        quantity_total=None,
    )

    real_select = LuckyWheelPrizeSelector.select
    first_pick = {"exhausted_concurrently": False}

    def select_with_concurrent_exhaustion(candidates, *, rng=None):
        chosen = real_select(candidates, rng=rng)
        if not first_pick["exhausted_concurrently"] and chosen.pk == prize_a.pk:
            first_pick["exhausted_concurrently"] = True
            # Simulate a concurrent worker exhausting prize_a right before select_for_update()
            LuckyWheelPrize.objects.filter(pk=prize_a.pk).update(quantity_awarded=1)
        return chosen

    with patch.object(
        LuckyWheelPrizeSelector,
        "select",
        side_effect=select_with_concurrent_exhaustion,
    ):
        spin, _ = LuckyWheelSpinService.spin(
            customer.user,
            customer,
            "race-reresolve-spin",
            rng=lambda _: 1,
        )

    prize_a.refresh_from_db()
    prize_b.refresh_from_db()
    grant.refresh_from_db()
    assert prize_a.quantity_awarded == 1  # Never over-awarded to 2!
    assert spin.prize_id == prize_b.pk
    assert prize_b.quantity_awarded == 1
    assert grant.remaining_attempts == 0


def test_15_failed_inventory_selection_when_no_candidates_remain_does_not_decrement_remaining_attempts():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    only_prize = LuckyWheelPrize.objects.create(
        label="Last Unit",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=100,
        weight=5,
        quantity_total=1,
        quantity_awarded=0,
    )
    stale_snapshot = LuckyWheelPrize.objects.get(pk=only_prize.pk)
    # Concurrent transaction commits exhaustion before our spin locks the row
    LuckyWheelPrize.objects.filter(pk=only_prize.pk).update(quantity_awarded=1)

    real_available = LuckyWheelService.available_prizes
    first_call = {"done": False}

    class _StaleQuerySet:
        def exclude(self, **kwargs):
            if only_prize.pk in kwargs.get("pk__in", set()):
                return []
            return [stale_snapshot]

    def stale_then_real():
        if not first_call["done"]:
            first_call["done"] = True
            return _StaleQuerySet()
        return real_available()

    with patch.object(
        LuckyWheelService,
        "available_prizes",
        side_effect=stale_then_real,
    ):
        with pytest.raises(ValidationError):
            LuckyWheelSpinService.spin(
                customer.user,
                customer,
                "race-no-fallback-spin",
            )

    only_prize.refresh_from_db()
    grant.refresh_from_db()
    assert only_prize.quantity_awarded == 1
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert LuckyWheelSpin.objects.count() == 0
    assert PointTransaction.objects.count() == 0


def test_16_lowering_quantity_total_below_quantity_awarded_is_rejected_with_400():
    manager = create_manager_with_wheel_perms()
    prize = LuckyWheelPrize.objects.create(
        label="Awarded 5 Times",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
        quantity_total=10,
        quantity_awarded=5,
    )

    res = auth_client(manager).patch(
        f"/api/lucky-wheel/admin/prizes/{prize.pk}/",
        {"quantity_total": 4},
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "quantity_total" in res.data or "quantity_awarded" in res.data
    prize.refresh_from_db()
    assert prize.quantity_total == 10


# ===========================================================================
# 3. Atomicity & Rollback (Cases 17–21)
# ===========================================================================


def test_17_failure_inside_point_service_earn_points_rolls_back_attempt_consumption_and_spin_creation():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="50 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=50,
        weight=1,
        quantity_total=5,
        quantity_awarded=0,
    )

    with patch.object(
        PointService,
        "earn_points",
        side_effect=ValidationError({"points": "Simulated point ledger failure."}),
    ):
        with pytest.raises(ValidationError):
            LuckyWheelSpinService.spin(customer.user, customer, "rb-points-key")

    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert PointTransaction.objects.count() == 0


def test_18_failure_inside_user_promotion_grant_service_grant_discount_rolls_back_attempt_and_spin():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("15.00"),
        is_active=True,
    )
    prize = LuckyWheelPrize.objects.create(
        label="15% Discount",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=1,
        quantity_total=3,
        quantity_awarded=0,
    )

    with patch.object(
        UserPromotionGrantService,
        "grant_discount",
        side_effect=ValidationError({"discount_offer": "Simulated discount failure."}),
    ):
        with pytest.raises(ValidationError):
            LuckyWheelSpinService.spin(customer.user, customer, "rb-discount-key")

    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0


def test_19_failure_inside_user_promotion_grant_service_grant_cashback_rolls_back_attempt_and_spin():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    offer = CashbackOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )
    prize = LuckyWheelPrize.objects.create(
        label="10 Cashback",
        prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
        cashback_offer=offer,
        weight=1,
        quantity_total=3,
        quantity_awarded=0,
    )

    with patch.object(
        UserPromotionGrantService,
        "grant_cashback",
        side_effect=ValidationError({"cashback_offer": "Simulated cashback failure."}),
    ):
        with pytest.raises(ValidationError):
            LuckyWheelSpinService.spin(customer.user, customer, "rb-cashback-key")

    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0


def test_20_failure_inside_badge_service_award_badge_rolls_back_attempt_and_spin():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="Bidder Badge",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.FIRST_BIDDER,
        weight=1,
        quantity_total=2,
        quantity_awarded=0,
    )

    with patch.object(
        BadgeService,
        "award_badge",
        side_effect=ValidationError({"badge_type": "Simulated badge failure."}),
    ):
        with pytest.raises(ValidationError):
            LuckyWheelSpinService.spin(customer.user, customer, "rb-badge-key")

    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert BadgeAward.objects.count() == 0


def test_21_failure_after_prize_inventory_increment_rolls_back_quantity_awarded():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="Inventory Rollback Check",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=30,
        weight=1,
        quantity_total=2,
        quantity_awarded=1,
    )

    with patch.object(
        LuckyWheelSpin.objects,
        "create",
        side_effect=RuntimeError("DB failure during spin creation"),
    ):
        with pytest.raises(RuntimeError):
            LuckyWheelSpinService.spin(customer.user, customer, "rb-spin-create")

    prize.refresh_from_db()
    grant.refresh_from_db()
    assert prize.quantity_awarded == 1
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert LuckyWheelSpin.objects.count() == 0


# ===========================================================================
# 4. Idempotency & Isolation (Cases 22–26)
# ===========================================================================


def test_22_same_user_same_idempotency_key_returns_same_spin_without_second_attempt_or_reward():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=2)
    prize = LuckyWheelPrize.objects.create(
        label="40 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=40,
        weight=1,
        quantity_total=10,
        quantity_awarded=0,
    )
    client = auth_client(customer.user)

    res1 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "idem-same-key"},
        format="json",
    )
    res2 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "idem-same-key"},
        format="json",
    )

    assert res1.status_code == status.HTTP_200_OK
    assert res2.status_code == status.HTTP_200_OK
    assert res1.data == res2.data
    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert prize.quantity_awarded == 1
    assert LuckyWheelSpin.objects.count() == 1
    assert (
        PointTransaction.objects.filter(
            user=customer.user,
            source_type=RewardSourceType.LUCKY_WHEEL,
        ).count()
        == 1
    )


def test_23_same_user_different_idempotency_key_consumes_next_available_attempt():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=2)
    prize = LuckyWheelPrize.objects.create(
        label="15 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=15,
        weight=1,
    )
    client = auth_client(customer.user)

    res1 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "idem-diff-1"},
        format="json",
    )
    res2 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "idem-diff-2"},
        format="json",
    )

    assert res1.status_code == status.HTTP_200_OK
    assert res2.status_code == status.HTTP_200_OK
    assert res1.data["spin_id"] != res2.data["spin_id"]
    assert res1.data["remaining_attempts"] == 1
    assert res2.data["remaining_attempts"] == 0
    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 0
    assert prize.quantity_awarded == 2
    assert PointService.get_balance(customer.user) == 30


def test_24_user_b_reusing_user_a_idempotency_key_is_rejected_with_400():
    user_a = CustomerProfileFactory(is_active=True)
    user_b = CustomerProfileFactory(is_active=True)
    grant_attempts(user_a, attempts=1)
    grant_b = grant_attempts(user_b, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=1,
    )

    res_a = auth_client(user_a.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "shared-spin-key"},
        format="json",
    )
    assert res_a.status_code == status.HTTP_200_OK

    res_b = auth_client(user_b.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "shared-spin-key"},
        format="json",
    )
    assert res_b.status_code == status.HTTP_400_BAD_REQUEST
    assert "idempotency_key" in res_b.data
    grant_b.refresh_from_db()
    assert grant_b.remaining_attempts == 1


@pytest.mark.parametrize("bad_key", ["", "   "])
def test_25_blank_or_whitespace_idempotency_key_is_rejected_with_400(bad_key):
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": bad_key},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "idempotency_key" in res.data

    with pytest.raises(ValidationError) as exc_info:
        LuckyWheelSpinService.spin(customer.user, customer, bad_key)
    assert "idempotency_key" in exc_info.value.detail
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1


def test_26_profile_not_belonging_to_user_is_rejected_with_400():
    c1 = CustomerProfileFactory(is_active=True)
    c2 = CustomerProfileFactory(is_active=True)
    grant_attempts(c1, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=1,
    )

    with pytest.raises(ValidationError) as exc_info:
        LuckyWheelSpinService.spin(c1.user, c2, "wrong-profile-key")
    assert "profile" in exc_info.value.detail
    assert LuckyWheelSpin.objects.count() == 0


# ===========================================================================
# 5. Prize Types (Cases 27–46)
# ===========================================================================


# --- NO_PRIZE (27–28) ---


def test_27_no_prize_spin_records_no_prize_status_and_awarded_false_with_zero_rewards():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="Try Again",
        description="Better luck next time",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "no-prize-spin-27"},
        format="json",
    )

    assert res.status_code == status.HTTP_200_OK
    assert res.data["awarded"] is False
    assert res.data["user_promotion_grant"] is None
    spin = LuckyWheelSpin.objects.get(idempotency_key="no-prize-spin-27")
    assert spin.status == LuckyWheelSpinStatus.NO_PRIZE
    assert PointTransaction.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0
    assert BadgeAward.objects.count() == 0


def test_28_no_prize_still_consumes_one_attempt_and_increments_prize_quantity_awarded():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=2)
    no_prize = LuckyWheelPrize.objects.create(
        label="Limited No Prize",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
        quantity_total=5,
        quantity_awarded=1,
    )

    spin, extra = LuckyWheelSpinService.spin(
        customer.user, customer, "no-prize-spin-28"
    )

    assert extra is None
    assert spin.status == LuckyWheelSpinStatus.NO_PRIZE
    grant.refresh_from_db()
    no_prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert no_prize.quantity_awarded == 2


# --- PP_COINS (29–32) ---


def test_29_pp_coins_spin_credits_exact_points_amount_to_shared_user_account_wallet():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="75 PP Coins",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=75,
        weight=1,
    )

    assert PointService.get_balance(customer.user) == 0
    spin, tx = LuckyWheelSpinService.spin(customer.user, customer, "pp-spin-29")

    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert tx.points == 75
    assert PointService.get_balance(customer.user) == 75


def test_30_pp_coins_spin_creates_point_transaction_with_expected_ledger_fields():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="120 PP Jackpot",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=120,
        weight=1,
    )

    spin, tx = LuckyWheelSpinService.spin(customer.user, customer, "pp-spin-30")

    assert tx.user_id == customer.user_id
    assert tx.profile_id == customer.pk
    assert tx.points == 120
    assert tx.balance_after == 120
    assert tx.source_type == RewardSourceType.LUCKY_WHEEL
    assert tx.source_id == str(spin.pk)
    assert tx.idempotency_key == f"lucky_wheel:spin:{spin.pk}:points"
    assert tx.metadata == {"spin_id": spin.pk, "prize_id": prize.pk}


def test_31_pp_coins_awarded_in_customer_mode_are_visible_in_seller_mode_on_same_account():
    customer = CustomerProfileFactory(is_active=True)
    user = customer.user
    seller = SellerProfile.objects.create(
        user=user,
        nickname=user.username,
        country="EG",
        currency="EGP",
        is_active=False,
        is_valid=True,
        activated=True,
    )
    grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="60 Shared PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=60,
        weight=1,
    )

    res = auth_client(user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "pp-shared-31"},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data["balances"]["points"] == 60

    switched, _ = switch_user_profile(user, new_profile_type=ProfileType.SELLER)
    assert switched is True
    assert user.active_profile.pk == seller.pk
    assert PointService.get_balance(user) == 60


def test_32_pp_coins_spin_response_returns_updated_balances_points():
    customer = CustomerProfileFactory(is_active=True)
    PointService.earn_points(
        user=customer.user,
        profile=customer,
        points=100,
        reason="Initial balance",
        source_type="seed",
        source_id="1",
        idempotency_key="seed:32",
    )
    grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="35 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=35,
        weight=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "pp-balance-32"},
        format="json",
    )

    assert res.status_code == status.HTTP_200_OK
    assert res.data["awarded"] is True
    assert res.data["balances"]["points"] == 135


# --- DISCOUNT_OFFER (33–37) ---


def test_33_discount_offer_spin_creates_one_active_user_promotion_grant():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    offer = DiscountOfferFactory(
        title="Wheel 15% Off",
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("15.00"),
        is_active=True,
    )
    prize = LuckyWheelPrize.objects.create(
        label="15% Discount Coupon",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=1,
    )

    spin, promo_grant = LuckyWheelSpinService.spin(
        customer.user, customer, "disc-spin-33"
    )

    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert promo_grant.user_id == customer.user_id
    assert promo_grant.profile_id == customer.pk
    assert promo_grant.grant_type == UserPromotionGrantType.DISCOUNT
    assert promo_grant.discount_offer_id == offer.pk
    assert promo_grant.cashback_offer_id is None
    assert promo_grant.status == UserPromotionGrantStatus.ACTIVE
    assert promo_grant.source_type == RewardSourceType.LUCKY_WHEEL
    assert promo_grant.source_id == str(spin.pk)
    assert promo_grant.idempotency_key == f"lucky_wheel:spin:{spin.pk}:discount"
    assert promo_grant.metadata == {"spin_id": spin.pk, "prize_id": prize.pk}


def test_34_discount_offer_spin_response_includes_serialized_user_promotion_grant():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    offer = DiscountOfferFactory(
        title="Wheel 25% Off",
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("25.00"),
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="25% Off",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "disc-spin-34"},
        format="json",
    )

    assert res.status_code == status.HTTP_200_OK
    assert res.data["awarded"] is True
    assert res.data["user_promotion_grant"] is not None
    assert res.data["user_promotion_grant"]["grant_type"] == "discount"
    assert res.data["user_promotion_grant"]["discount_offer"] == offer.pk
    assert res.data["user_promotion_grant"]["discount_offer_title"] == "Wheel 25% Off"
    assert res.data["prize"]["discount_offer"]["id"] == offer.pk


def test_35_idempotent_retry_of_discount_spin_returns_same_user_promotion_grant_without_duplicate():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=2)
    offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="10% Off",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=1,
    )
    client = auth_client(customer.user)

    r1 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "disc-retry-35"},
        format="json",
    )
    r2 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "disc-retry-35"},
        format="json",
    )

    assert r1.status_code == status.HTTP_200_OK
    assert r2.status_code == status.HTTP_200_OK
    assert (
        r1.data["user_promotion_grant"]["id"]
        == r2.data["user_promotion_grant"]["id"]
    )
    assert (
        UserPromotionGrant.objects.filter(
            user=customer.user,
            source_type=RewardSourceType.LUCKY_WHEEL,
        ).count()
        == 1
    )


def test_36_non_lucky_wheel_discount_offer_trigger_type_is_rejected():
    manager = create_manager_with_wheel_perms()
    global_offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.GLOBAL,
        value=Decimal("10.00"),
        is_active=True,
    )

    res = auth_client(manager).post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Wrong Trigger Discount",
            "prize_type": LuckyWheelPrizeType.DISCOUNT_OFFER,
            "discount_offer": global_offer.pk,
            "weight": 1,
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "discount_offer" in res.data


def test_37_inactive_or_soft_deleted_discount_offer_cannot_be_granted_by_spin():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("15.00"),
        is_active=True,
    )
    prize = LuckyWheelPrize.objects.create(
        label="Deactivated Offer Prize",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=1,
        quantity_total=5,
        quantity_awarded=0,
    )

    # Manager deactivates the underlying DiscountOffer after the wheel prize was created
    DiscountOfferService.deactivate(offer)

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "disc-inactive-spin-37"},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0

    # Re-activate then soft-delete the offer: spin must still reject and roll back
    DiscountOfferService.activate(offer)
    offer.delete()

    res_deleted = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "disc-deleted-spin-37"},
        format="json",
    )
    assert res_deleted.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0


# --- CASHBACK_OFFER (38–42) ---


def test_38_cashback_offer_spin_creates_one_active_user_promotion_grant():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    offer = CashbackOfferFactory(
        title="Wheel Cashback 20",
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("20.00"),
        is_active=True,
    )
    prize = LuckyWheelPrize.objects.create(
        label="20 Cashback Offer",
        prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
        cashback_offer=offer,
        weight=1,
    )

    spin, promo_grant = LuckyWheelSpinService.spin(
        customer.user, customer, "cb-spin-38"
    )

    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert promo_grant.user_id == customer.user_id
    assert promo_grant.profile_id == customer.pk
    assert promo_grant.grant_type == UserPromotionGrantType.CASHBACK
    assert promo_grant.cashback_offer_id == offer.pk
    assert promo_grant.discount_offer_id is None
    assert promo_grant.status == UserPromotionGrantStatus.ACTIVE
    assert promo_grant.source_type == RewardSourceType.LUCKY_WHEEL
    assert promo_grant.source_id == str(spin.pk)
    assert promo_grant.idempotency_key == f"lucky_wheel:spin:{spin.pk}:cashback"
    assert promo_grant.metadata == {"spin_id": spin.pk, "prize_id": prize.pk}


def test_39_cashback_offer_spin_does_not_directly_mutate_customer_cashback_balance():
    customer = CustomerProfileFactory(
        is_active=True, cashback_balance=Decimal("12.50")
    )
    grant_attempts(customer, attempts=1)
    offer = CashbackOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("25.00"),
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="Cashback Grant Only",
        prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
        cashback_offer=offer,
        weight=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "cb-balance-unchanged-39"},
        format="json",
    )

    assert res.status_code == status.HTTP_200_OK
    customer.refresh_from_db()
    assert customer.cashback_balance == Decimal("12.50")
    assert res.data["balances"]["cashback"] == "12.50"
    assert CashbackTransaction.objects.count() == 0
    assert res.data["user_promotion_grant"]["grant_type"] == "cashback"


def test_40_idempotent_retry_of_cashback_spin_returns_same_user_promotion_grant_without_duplicate():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=2)
    offer = CashbackOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="Cashback Retry",
        prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
        cashback_offer=offer,
        weight=1,
    )
    client = auth_client(customer.user)

    r1 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "cb-retry-40"},
        format="json",
    )
    r2 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "cb-retry-40"},
        format="json",
    )

    assert r1.status_code == status.HTTP_200_OK
    assert r2.status_code == status.HTTP_200_OK
    assert (
        r1.data["user_promotion_grant"]["id"]
        == r2.data["user_promotion_grant"]["id"]
    )
    assert (
        UserPromotionGrant.objects.filter(
            user=customer.user,
            grant_type=UserPromotionGrantType.CASHBACK,
            source_type=RewardSourceType.LUCKY_WHEEL,
        ).count()
        == 1
    )


def test_41_non_lucky_wheel_cashback_offer_trigger_type_is_rejected():
    manager = create_manager_with_wheel_perms()
    global_cb = CashbackOfferFactory(
        trigger_type=OfferTriggerType.GLOBAL,
        value=Decimal("10.00"),
        is_active=True,
    )

    res = auth_client(manager).post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Wrong Trigger Cashback",
            "prize_type": LuckyWheelPrizeType.CASHBACK_OFFER,
            "cashback_offer": global_cb.pk,
            "weight": 1,
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "cashback_offer" in res.data


def test_42_inactive_or_soft_deleted_cashback_offer_cannot_be_granted_by_spin():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    offer = CashbackOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("15.00"),
        is_active=True,
    )
    prize = LuckyWheelPrize.objects.create(
        label="Deactivated Cashback Prize",
        prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
        cashback_offer=offer,
        weight=1,
        quantity_total=5,
        quantity_awarded=0,
    )

    CashbackOfferService.deactivate(offer)

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "cb-inactive-spin-42"},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0

    CashbackOfferService.activate(offer)
    offer.delete()

    res_deleted = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "cb-deleted-spin-42"},
        format="json",
    )
    assert res_deleted.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0


# --- BADGE (43–46) ---


def test_43_badge_spin_creates_one_badge_award_for_eligible_profile():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="Loyal Follower Badge",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.LOYAL_FOLLOWER,
        weight=1,
    )

    spin, award = LuckyWheelSpinService.spin(
        customer.user, customer, "badge-spin-43"
    )

    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert award.profile_id == customer.pk
    assert award.badge_type == BadgeType.LOYAL_FOLLOWER
    assert award.source_type == RewardSourceType.LUCKY_WHEEL
    assert award.source_id == str(spin.pk)
    assert award.idempotency_key == f"lucky_wheel:spin:{spin.pk}:badge"
    assert award.metadata == {"spin_id": spin.pk, "prize_id": prize.pk}


def test_44_badge_spin_creates_zero_point_transactions():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="First Bidder Badge Only",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.FIRST_BIDDER,
        weight=1,
    )
    PointTransaction.objects.all().delete()

    spin, award = LuckyWheelSpinService.spin(
        customer.user, customer, "badge-no-points-44"
    )

    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert award.badge_type == BadgeType.FIRST_BIDDER
    assert PointTransaction.objects.count() == 0
    assert PointService.get_balance(customer.user) == 0


def test_45_badge_spin_for_ineligible_profile_type_fails_cleanly_and_rolls_back_attempt_and_spin():
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    # WELCOME_SELLER is only eligible for Seller profiles, not Customer profiles
    prize = LuckyWheelPrize.objects.create(
        label="Seller-Only Badge",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.WELCOME_SELLER,
        weight=1,
        quantity_total=4,
        quantity_awarded=0,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "badge-ineligible-45"},
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert BadgeAward.objects.filter(
        profile=customer, badge_type=BadgeType.WELCOME_SELLER
    ).count() == 0


def test_46_repeated_badge_award_for_one_per_profile_badge_remains_idempotent_without_duplicate_badge_award():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=2)
    LuckyWheelPrize.objects.create(
        label="Reviewer Buyer Badge",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.REVIEWER_BUYER,
        weight=1,
    )

    spin1, award1 = LuckyWheelSpinService.spin(
        customer.user, customer, "badge-repeat-1"
    )
    spin2, award2 = LuckyWheelSpinService.spin(
        customer.user, customer, "badge-repeat-2"
    )

    assert spin1.pk != spin2.pk
    assert award1.pk == award2.pk
    assert (
        BadgeAward.objects.filter(
            profile=customer,
            badge_type=BadgeType.REVIEWER_BUYER,
        ).count()
        == 1
    )


# ===========================================================================
# 6. Snapshot Immutability (Cases 47–50)
# ===========================================================================


def test_47_spin_stores_immutable_prize_snapshot():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    offer = DiscountOfferFactory(
        title="Snapshot 20% Off",
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("20.00"),
        is_active=True,
    )
    prize = LuckyWheelPrize.objects.create(
        label="Snapshot Discount Label",
        description="Snapshot Discount Description",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=1,
    )

    spin, _ = LuckyWheelSpinService.spin(
        customer.user, customer, "snapshot-check-47"
    )

    snap = spin.prize_snapshot
    assert snap["id"] == prize.pk
    assert snap["label"] == "Snapshot Discount Label"
    assert snap["description"] == "Snapshot Discount Description"
    assert snap["prize_type"] == LuckyWheelPrizeType.DISCOUNT_OFFER
    assert snap["points_amount"] is None
    assert snap["discount_offer_id"] == offer.pk
    assert snap["discount_offer"] == {
        "id": offer.pk,
        "title": "Snapshot 20% Off",
        "value_type": offer.value_type,
        "value": "20.00",
    }
    assert snap["cashback_offer_id"] is None
    assert snap["cashback_offer"] is None
    assert snap["badge_type"] == ""


def test_48_later_manager_edit_to_prize_label_description_or_points_does_not_mutate_existing_snapshot():
    customer = CustomerProfileFactory(is_active=True)
    manager = create_manager_with_wheel_perms()
    grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="Original 25 PP Label",
        description="Original 25 PP Description",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=25,
        weight=1,
    )

    spin, _ = LuckyWheelSpinService.spin(
        customer.user, customer, "snapshot-immutability-48"
    )

    patch_res = auth_client(manager).patch(
        f"/api/lucky-wheel/admin/prizes/{prize.pk}/",
        {
            "label": "Updated 999 PP Label",
            "description": "Updated Description",
            "points_amount": 999,
            "weight": 10,
        },
        format="json",
    )
    assert patch_res.status_code == status.HTTP_200_OK

    spin.refresh_from_db()
    assert spin.prize_snapshot["label"] == "Original 25 PP Label"
    assert spin.prize_snapshot["description"] == "Original 25 PP Description"
    assert spin.prize_snapshot["points_amount"] == 25


def test_49_later_prize_deactivation_does_not_mutate_historical_spin_rows():
    customer = CustomerProfileFactory(is_active=True)
    manager = create_manager_with_wheel_perms()
    grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="Soon Inactive Prize",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=15,
        weight=1,
        is_active=True,
    )

    spin, _ = LuckyWheelSpinService.spin(
        customer.user, customer, "snapshot-deactivate-49"
    )

    res = auth_client(manager).patch(
        f"/api/lucky-wheel/admin/prizes/{prize.pk}/",
        {"is_active": False},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK

    spin.refresh_from_db()
    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert spin.prize_snapshot["label"] == "Soon Inactive Prize"
    assert spin.prize_snapshot["points_amount"] == 15


def test_50_get_my_spins_and_admin_spins_return_preserved_prize_snapshot():
    customer = CustomerProfileFactory(is_active=True)
    manager = create_manager_with_wheel_perms()
    grant_attempts(customer, attempts=1)
    prize = LuckyWheelPrize.objects.create(
        label="Before Edit Label",
        description="Before Edit Desc",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=30,
        weight=1,
    )

    spin, _ = LuckyWheelSpinService.spin(
        customer.user, customer, "snapshot-api-50"
    )
    prize.label = "After Edit Label"
    prize.points_amount = 300
    prize.save()

    my_res = auth_client(customer.user).get("/api/lucky-wheel/spins/my/")
    admin_res = auth_client(manager).get("/api/lucky-wheel/admin/spins/")

    assert my_res.status_code == status.HTTP_200_OK
    assert admin_res.status_code == status.HTTP_200_OK
    my_spin = next(item for item in my_res.data["results"] if item["id"] == spin.pk)
    admin_spin = next(
        item for item in admin_res.data["results"] if item["id"] == spin.pk
    )
    assert my_spin["prize_snapshot"]["label"] == "Before Edit Label"
    assert my_spin["prize_snapshot"]["points_amount"] == 30
    assert admin_spin["prize_snapshot"]["label"] == "Before Edit Label"
    assert admin_spin["prize_snapshot"]["points_amount"] == 30


# ===========================================================================
# 7. Client Manipulation Protection (Cases 51–56)
# ===========================================================================


@pytest.mark.parametrize("field_name,val", [("prize_id", 1), ("prize", 1)])
def test_51_client_cannot_pass_prize_id_or_prize(field_name, val):
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10, weight=1
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": f"forbid-51-{field_name}", field_name: val},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in res.data
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1


@pytest.mark.parametrize(
    "field_name,val", [("outcome", "awarded"), ("status", "awarded")]
)
def test_52_client_cannot_pass_outcome_or_status(field_name, val):
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10, weight=1
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": f"forbid-52-{field_name}", field_name: val},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in res.data
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1


@pytest.mark.parametrize("field_name,val", [("slot", 0), ("segment", 1)])
def test_53_client_cannot_pass_slot_or_segment(field_name, val):
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10, weight=1
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": f"forbid-53-{field_name}", field_name: val},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in res.data
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1


@pytest.mark.parametrize(
    "field_name,val",
    [("weight", 100), ("seed", 42), ("random_value", 1)],
)
def test_54_client_cannot_pass_weight_seed_or_random_value(field_name, val):
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10, weight=1
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": f"forbid-54-{field_name}", field_name: val},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in res.data
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1


@pytest.mark.parametrize(
    "field_name,val",
    [
        ("points_amount", 9999),
        ("discount_offer", 1),
        ("cashback_offer", 1),
        ("badge_type", BadgeType.FIRST_BIDDER),
    ],
)
def test_55_client_cannot_pass_points_amount_discount_offer_cashback_offer_or_badge_type(
    field_name, val
):
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10, weight=1
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": f"forbid-55-{field_name}", field_name: val},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in res.data
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1


@pytest.mark.parametrize(
    "field_name,val",
    [("grant_id", 1), ("remaining_attempts", 99)],
)
def test_56_client_cannot_pass_grant_id_or_remaining_attempts(field_name, val):
    customer = CustomerProfileFactory(is_active=True)
    grant = grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10, weight=1
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": f"forbid-56-{field_name}", field_name: val},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in res.data
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1


# ===========================================================================
# 8. Side-Effect Isolation & Regressions (Cases 57–60) + Admin One-Of Validation
# ===========================================================================


def test_57_spin_creates_zero_notification_rows():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=3)
    offer_disc = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )
    offer_cb = CashbackOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )
    p1 = LuckyWheelPrize.objects.create(
        label="PP Spin",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=20,
        weight=1,
    )
    p2 = LuckyWheelPrize.objects.create(
        label="Disc Spin",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer_disc,
        weight=1,
    )
    p3 = LuckyWheelPrize.objects.create(
        label="CB Spin",
        prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
        cashback_offer=offer_cb,
        weight=1,
    )
    Notification.objects.all().delete()

    LuckyWheelSpinService.spin(customer.user, customer, "no-notif-1", rng=lambda _: 1)
    LuckyWheelSpinService.spin(customer.user, customer, "no-notif-2", rng=lambda _: 2)
    LuckyWheelSpinService.spin(customer.user, customer, "no-notif-3", rng=lambda _: 3)

    assert Notification.objects.count() == 1
    notif = Notification.objects.get()
    assert notif.object_type == "point_transaction"
    assert notif.metadata["points"] == 20
    assert p1.pk and p2.pk and p3.pk


def test_58_spin_creates_zero_weekly_draw_or_weekly_draw_entry_mutations():
    customer = CustomerProfileFactory(is_active=True)
    now = timezone.now()
    draw = WeeklyDraw.objects.create(
        title="LW02 Draw Isolation",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
        status=WeeklyDrawStatus.OPEN,
    )
    draw_count_before = WeeklyDraw.objects.count()
    entry_count_before = WeeklyDrawEntry.objects.count()

    grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="10 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=1,
    )

    LuckyWheelSpinService.spin(customer.user, customer, "draw-isolation-58")

    draw.refresh_from_db()
    assert draw.status == WeeklyDrawStatus.OPEN
    assert WeeklyDraw.objects.count() == draw_count_before
    assert WeeklyDrawEntry.objects.count() == entry_count_before


def test_59_spin_creates_zero_onboarding_referrals_seasons_or_campaign_mutations():
    customer = CustomerProfileFactory(is_active=True)
    now = timezone.now()
    season = Season.objects.create(
        title="LW02 Season Check",
        starts_at=now - timedelta(days=3),
        ends_at=now + timedelta(days=4),
        status=SeasonStatus.DRAFT,
    )
    campaign = PromotionCampaign.objects.create(
        name="LW02 Campaign Check",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
        is_enabled=True,
    )
    code = LinkSharingCode.objects.create(
        owner_profile=customer,
        code="LW0259",
        expires_at=now + timedelta(days=1),
    )

    grant_attempts(customer, attempts=1)
    LuckyWheelPrize.objects.create(
        label="25 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=25,
        weight=1,
    )

    LuckyWheelSpinService.spin(customer.user, customer, "cross-domain-59")

    season.refresh_from_db()
    campaign.refresh_from_db()
    code.refresh_from_db()
    assert season.status == SeasonStatus.DRAFT
    assert campaign.is_enabled is True
    assert Referral.objects.count() == 0
    assert (
        PointTransaction.objects.filter(
            source_type=RewardSourceType.ONBOARDING,
            user=customer.user,
        ).count()
        == 0
    )


def test_60_openapi_schema_and_i18n_ar_en_for_spin_and_prize_validation():
    import yaml

    customer = CustomerProfileFactory(is_active=True)
    manager = create_manager_with_wheel_perms()

    schema_res = auth_client(customer.user).get("/api/schema/")
    assert schema_res.status_code == status.HTTP_200_OK
    paths = yaml.safe_load(schema_res.content.decode())["paths"]
    assert "/api/lucky-wheel/spin/" in paths
    assert "/api/lucky-wheel/admin/prizes/" in paths

    # Arabic vs English validation when no attempts are available
    LuckyWheelPrize.objects.create(
        label="10 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=1,
    )
    res_ar = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "i18n-ar-60"},
        format="json",
        HTTP_ACCEPT_LANGUAGE="ar",
    )
    res_en = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "i18n-en-60"},
        format="json",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    assert res_ar.status_code == status.HTTP_400_BAD_REQUEST
    assert res_en.status_code == status.HTTP_400_BAD_REQUEST
    assert str(res_en.data["detail"]) == "No lucky wheel attempts are available."
    assert str(res_ar.data["detail"]) != str(res_en.data["detail"])

    # Admin strict one-of validation in Arabic and English
    disc_offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )
    admin_ar = auth_client(manager).post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Mixed Prize",
            "prize_type": LuckyWheelPrizeType.PP_COINS,
            "points_amount": 20,
            "discount_offer": disc_offer.pk,
            "weight": 1,
        },
        format="json",
        HTTP_ACCEPT_LANGUAGE="ar",
    )
    admin_en = auth_client(manager).post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Mixed Prize",
            "prize_type": LuckyWheelPrizeType.PP_COINS,
            "points_amount": 20,
            "discount_offer": disc_offer.pk,
            "weight": 1,
        },
        format="json",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    assert admin_ar.status_code == status.HTTP_400_BAD_REQUEST
    assert admin_en.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        str(admin_en.data["discount_offer"][0])
        == "PP coin prizes cannot define a discount offer."
    )
    assert (
        str(admin_ar.data["discount_offer"][0])
        == "لا يمكن لجوائز عملات PP تحديد عرض خصم."
    )


def test_admin_prize_strict_one_of_reward_payload_validation_across_all_types():
    disc_offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )
    cb_offer = CashbackOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("10.00"),
        is_active=True,
    )

    # DISCOUNT_OFFER cannot also set points_amount, cashback_offer, or badge_type
    with pytest.raises(DjangoValidationError) as exc1:
        LuckyWheelPrize(
            label="Invalid Discount",
            prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
            discount_offer=disc_offer,
            points_amount=10,
            cashback_offer=cb_offer,
            badge_type=BadgeType.FIRST_BIDDER,
            weight=1,
        ).full_clean()
    assert "points_amount" in exc1.value.message_dict
    assert "cashback_offer" in exc1.value.message_dict
    assert "badge_type" in exc1.value.message_dict

    # CASHBACK_OFFER cannot also set points_amount, discount_offer, or badge_type
    with pytest.raises(DjangoValidationError) as exc2:
        LuckyWheelPrize(
            label="Invalid Cashback",
            prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
            cashback_offer=cb_offer,
            points_amount=10,
            discount_offer=disc_offer,
            badge_type=BadgeType.FIRST_BIDDER,
            weight=1,
        ).full_clean()
    assert "points_amount" in exc2.value.message_dict
    assert "discount_offer" in exc2.value.message_dict
    assert "badge_type" in exc2.value.message_dict

    # BADGE cannot also set points_amount, discount_offer, or cashback_offer
    with pytest.raises(DjangoValidationError) as exc3:
        LuckyWheelPrize(
            label="Invalid Badge",
            prize_type=LuckyWheelPrizeType.BADGE,
            badge_type=BadgeType.FIRST_BIDDER,
            points_amount=10,
            discount_offer=disc_offer,
            cashback_offer=cb_offer,
            weight=1,
        ).full_clean()
    assert "points_amount" in exc3.value.message_dict
    assert "discount_offer" in exc3.value.message_dict
    assert "cashback_offer" in exc3.value.message_dict


def test_optional_prize_metadata_validity_days_sets_user_promotion_grant_expires_at():
    customer = CustomerProfileFactory(is_active=True)
    grant_attempts(customer, attempts=1)
    disc_offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("15.00"),
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="7-Day Discount",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=disc_offer,
        weight=1,
        metadata={"validity_days": 7},
    )

    spin, promo_grant = LuckyWheelSpinService.spin(
        customer.user, customer, "validity-7-days-spin"
    )

    assert promo_grant.expires_at == spin.awarded_at + timedelta(days=7)
    assert promo_grant.is_available is True
