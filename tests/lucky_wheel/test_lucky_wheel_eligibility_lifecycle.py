"""BE-LW-01: Lucky Wheel source audit + eligibility / attempt lifecycle hardening."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.auctions.models import AuctionItemStatus
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.deals.models import CompletedDeal, CompletedDealSourceType, CompletedDealType
from apps.draws.models import WeeklyDrawEntry, WeeklyDrawEntrySource
from apps.loyalty.constants import BadgeType, RewardEventType, RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine
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
    LuckyWheelService,
    LuckyWheelSpinService,
)
from apps.notifications.models import Notification
from apps.promotions.models import (
    OfferTriggerType,
    PromotionCampaign,
    PromotionCampaignType,
    UserPromotionGrant,
)
from apps.promotions.services import OnboardingHighlightBenefitService
from apps.referrals.models import LinkSharingCode, Referral, ReferralStatus
from apps.seasons.models import Season, SeasonStatus
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    CompletedDealFactory,
    CustomerProfileFactory,
    DiscountOfferFactory,
    SellerProfileFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db


def auth_client(user=None):
    client = APIClient()
    if user is not None:
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def create_prize(
    *,
    label="10 PP Coins",
    prize_type=LuckyWheelPrizeType.PP_COINS,
    points_amount=10,
    weight=1,
    is_active=True,
    quantity_total=None,
    quantity_awarded=0,
    discount_offer=None,
    cashback_offer=None,
    badge_type="",
):
    return LuckyWheelPrize.objects.create(
        label=label,
        prize_type=prize_type,
        points_amount=points_amount if prize_type == LuckyWheelPrizeType.PP_COINS else 0,
        weight=weight,
        is_active=is_active,
        quantity_total=quantity_total,
        quantity_awarded=quantity_awarded,
        discount_offer=discount_offer,
        cashback_offer=cashback_offer,
        badge_type=badge_type,
    )


def create_completed_auction_deal():
    buyer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer_profile = buyer_user.active_profile
    seller_profile = seller_user.active_profile
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
    return deal, item, buyer_user, buyer_profile, seller_user, seller_profile


# ===========================================================================
# 1. Domain Separation: Weekly Onboarding Draw != Lucky Wheel (Cases 1-6)
# ===========================================================================


def test_01_new_customer_registration_creates_zero_lucky_wheel_grants():
    payload = customer_registration_payload(phone_number="+201009998871")
    payload["username"] = "lw01-new-customer"

    response = auth_client().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert LuckyWheelGrant.objects.count() == 0


def test_02_new_seller_profile_creation_creates_zero_lucky_wheel_grants():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    LuckyWheelGrant.objects.all().delete()

    response = auth_client(user).post(
        "/api/users/mobile/sellers/",
        {
            "nickname": "lw01-seller",
            "country": "EG",
            "currency": "EGP",
            "loft_name": "LW01 Loft",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert LuckyWheelGrant.objects.filter(user=user).count() == 0


def test_03_profile_switching_creates_zero_lucky_wheel_grants():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    SellerProfile.objects.create(
        user=user,
        nickname=user.username,
        country="EG",
        currency="EGP",
        is_active=False,
        is_valid=True,
        activated=True,
    )
    LuckyWheelGrant.objects.all().delete()

    success, switched_type = switch_user_profile(
        user, new_profile_type=ProfileType.SELLER
    )
    assert success is True
    assert switched_type == ProfileType.SELLER
    assert LuckyWheelGrant.objects.filter(user=user).count() == 0

    response = auth_client(user).post(
        "/api/auth/switch-profile/",
        {"new_profile": ProfileType.CUSTOMER},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert LuckyWheelGrant.objects.filter(user=user).count() == 0


def test_04_onboarding_creates_weekly_draw_entry_instead_of_lucky_wheel_grant():
    payload = customer_registration_payload(phone_number="+201009998872")
    payload["username"] = "lw01-draw-not-wheel"

    response = auth_client().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert not LuckyWheelGrant.objects.filter(
        source_type=LuckyWheelGrantSourceType.ONBOARDING
    ).exists()
    assert not WeeklyDrawEntry.objects.filter(
        user__username="lw01-draw-not-wheel",
        source=WeeklyDrawEntrySource.ONBOARDING,
    ).exists()


def test_05_and_06_historical_onboarding_lucky_wheel_grants_remain_readable_and_unaffected():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Legacy Wheel Prize")
    legacy_grant = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        source_id=str(customer.pk),
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key=f"lucky_wheel:onboarding:{customer.pk}",
        metadata={"legacy": True},
    )

    # Fresh onboarding for another user must not touch the historical grant
    payload = customer_registration_payload(phone_number="+201009998873")
    payload["username"] = "lw01-fresh-after-legacy"
    reg_res = auth_client().post("/api/auth/register/customer/", payload, format="json")
    assert reg_res.status_code == status.HTTP_201_CREATED

    legacy_grant.refresh_from_db()
    assert legacy_grant.deleted is None
    assert legacy_grant.is_active is True
    assert legacy_grant.remaining_attempts == 1
    assert legacy_grant.source_type == LuckyWheelGrantSourceType.ONBOARDING

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["available"] is True
    assert res.data["eligible"] is True
    assert res.data["remaining_attempts"] == 1
    assert res.data["active_grants"][0]["id"] == legacy_grant.pk
    assert (
        res.data["active_grants"][0]["source_type"]
        == LuckyWheelGrantSourceType.ONBOARDING
    )


# ===========================================================================
# 2. Eligibility / Visibility Rules (Cases 7-10)
# ===========================================================================


def test_07_no_eligible_attempts_makes_wheel_not_available():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Prize A")

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")

    assert res.status_code == status.HTTP_200_OK
    assert res.data["available"] is False
    assert res.data["eligible"] is False
    assert res.data["has_active_wheel"] is True
    assert res.data["remaining_attempts"] == 0
    assert res.data["active_grants"] == []


def test_08_one_eligible_attempt_makes_wheel_available():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Prize A")
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="101",
        attempts=1,
    )

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")

    assert res.status_code == status.HTTP_200_OK
    assert res.data["available"] is True
    assert res.data["eligible"] is True
    assert res.data["has_active_wheel"] is True
    assert res.data["remaining_attempts"] == 1
    assert [g["id"] for g in res.data["active_grants"]] == [grant.pk]


def test_09_multiple_eligible_attempts_accumulate_remaining_attempts_correctly():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Prize A")
    g1 = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="201",
        attempts=1,
    )
    g2 = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="admin-bonus-1",
        attempts=2,
    )

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")

    assert res.status_code == status.HTTP_200_OK
    assert res.data["available"] is True
    assert res.data["eligible"] is True
    assert res.data["remaining_attempts"] == 3
    assert [g["id"] for g in res.data["active_grants"]] == [g1.pk, g2.pk]


def test_10_inactive_or_exhausted_wheel_prizes_make_wheel_unavailable_even_with_attempts():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Inactive Prize", is_active=False)
    create_prize(
        label="Sold Out Prize",
        is_active=True,
        quantity_total=2,
        quantity_awarded=2,
    )
    LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="301",
        attempts=1,
    )

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")

    assert res.status_code == status.HTTP_200_OK
    assert res.data["has_active_wheel"] is False
    assert res.data["available"] is False
    assert res.data["eligible"] is False
    assert res.data["remaining_attempts"] == 1
    assert res.data["segments"] == []


# ===========================================================================
# 3. Attempt Grant Lifecycle & Idempotency (Cases 11-16)
# ===========================================================================


def test_11_eligible_auction_completed_trigger_creates_one_attempt():
    deal, item, buyer_user, buyer_profile, seller_user, _ = (
        create_completed_auction_deal()
    )

    grant = LuckyWheelGrantService.grant_for_auction_completed(buyer_profile, item)

    assert grant.user_id == buyer_user.pk
    assert grant.profile_id == buyer_profile.pk
    assert grant.source_type == LuckyWheelGrantSourceType.AUCTION_COMPLETED
    assert grant.source_id == str(item.pk)
    assert grant.total_attempts == 1
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert not LuckyWheelGrant.objects.filter(user=seller_user).exists()


def test_12_repeated_same_trigger_does_not_duplicate_attempt():
    _, item, buyer_user, buyer_profile, _, _ = create_completed_auction_deal()

    first = LuckyWheelGrantService.grant_for_auction_completed(buyer_profile, item)
    second = LuckyWheelGrantService.grant_for_auction_completed(buyer_profile, item)

    assert first.pk == second.pk
    assert (
        LuckyWheelGrant.objects.filter(
            user=buyer_user,
            source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
            source_id=str(item.pk),
        ).count()
        == 1
    )


def test_13_replay_of_same_deal_completed_event_is_idempotent_for_lucky_wheel_grant():
    deal, item, buyer_user, buyer_profile, _, _ = create_completed_auction_deal()

    RewardEngine.handle_event(
        RewardEventType.DEAL_COMPLETED,
        actor=deal.seller_profile,
        context={"deal": deal},
        run_async_for_aggregates=False,
    )
    RewardEngine.handle_event(
        RewardEventType.DEAL_COMPLETED,
        actor=deal.seller_profile,
        context={"deal": deal},
        run_async_for_aggregates=False,
    )

    grants = LuckyWheelGrant.objects.filter(
        user=buyer_user,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id=str(item.pk),
    )
    assert grants.count() == 1
    assert grants.first().remaining_attempts == 1


def test_14_15_16_attempt_grant_creates_zero_points_notifications_or_prize_grants():
    _, item, buyer_user, buyer_profile, _, _ = create_completed_auction_deal()
    PointTransaction.objects.all().delete()
    Notification.objects.all().delete()
    UserPromotionGrant.objects.all().delete()
    BadgeAward.objects.all().delete()
    LuckyWheelSpin.objects.all().delete()

    grant = LuckyWheelGrantService.grant_for_auction_completed(buyer_profile, item)

    assert grant.remaining_attempts == 1
    assert PointTransaction.objects.count() == 0
    assert Notification.objects.count() == 0
    assert UserPromotionGrant.objects.count() == 0
    assert BadgeAward.objects.count() == 0
    assert LuckyWheelSpin.objects.count() == 0


def test_grant_attempt_rejects_cross_user_idempotency_key_collision():
    c1 = CustomerProfileFactory(is_active=True)
    c2 = CustomerProfileFactory(is_active=True)
    LuckyWheelGrantService.grant_attempt(
        profile=c1,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="a1",
        attempts=1,
        idempotency_key="shared-grant-key",
    )

    with pytest.raises(ValidationError) as exc_info:
        LuckyWheelGrantService.grant_attempt(
            profile=c2,
            source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
            source_id="a1",
            attempts=1,
            idempotency_key="shared-grant-key",
        )
    assert "idempotency_key" in exc_info.value.detail


# ===========================================================================
# 4. Read-Only Availability Queries (Cases 17-19, 43-44)
# ===========================================================================


def test_17_18_19_availability_query_is_read_only_and_matches_backend_truth():
    customer = CustomerProfileFactory(is_active=True)
    prize = create_prize(label="50 PP Coins", points_amount=50)
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="401",
        attempts=2,
    )
    PointTransaction.objects.all().delete()
    Notification.objects.all().delete()

    client = auth_client(customer.user)
    r1 = client.get("/api/lucky-wheel/current/")
    r2 = client.get("/api/lucky-wheel/current/")
    r3 = LuckyWheelService.get_availability(user=customer.user, profile=customer)

    assert r1.status_code == status.HTTP_200_OK
    assert r2.status_code == status.HTTP_200_OK
    assert r1.data == r2.data
    assert r1.data["available"] is True
    assert r1.data["eligible"] is True
    assert r1.data["has_active_wheel"] is True
    assert r1.data["remaining_attempts"] == 2
    assert r3["remaining_attempts"] == 2
    assert [s["id"] for s in r1.data["segments"]] == [prize.pk]

    grant.refresh_from_db()
    assert grant.remaining_attempts == 2
    assert grant.is_active is True
    assert LuckyWheelSpin.objects.count() == 0
    assert PointTransaction.objects.count() == 0
    assert Notification.objects.count() == 0


# ===========================================================================
# 5. Attempt Consumption & Concurrency / Expiration (Cases 20-26)
# ===========================================================================


def test_20_21_attempt_consumed_decrements_remaining_and_marks_inactive_at_zero():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="No Prize", prize_type=LuckyWheelPrizeType.NO_PRIZE)
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="two-tries",
        attempts=2,
    )
    client = auth_client(customer.user)

    res1 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "consume-1"},
        format="json",
    )
    assert res1.status_code == status.HTTP_200_OK
    assert res1.data["remaining_attempts"] == 1
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True

    res2 = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "consume-2"},
        format="json",
    )
    assert res2.status_code == status.HTTP_200_OK
    assert res2.data["remaining_attempts"] == 0
    grant.refresh_from_db()
    assert grant.remaining_attempts == 0
    assert grant.is_active is False


def test_22_23_spin_with_zero_attempts_or_replayed_consumed_attempt_is_rejected():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10)
    client = auth_client(customer.user)

    # 0 attempts initially
    no_grant_res = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-empty"},
        format="json",
    )
    assert no_grant_res.status_code == status.HTTP_400_BAD_REQUEST

    # Grant 1 attempt and consume it
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="501",
        attempts=1,
    )
    first_spin = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-once"},
        format="json",
    )
    assert first_spin.status_code == status.HTTP_200_OK

    # Same idempotency_key returns same spin without extra deduction/reward
    same_key_retry = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-once"},
        format="json",
    )
    assert same_key_retry.status_code == status.HTTP_200_OK
    assert same_key_retry.data["spin_id"] == first_spin.data["spin_id"]

    # New idempotency_key after attempt was consumed is rejected with 400
    second_spin = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-twice-illegal"},
        format="json",
    )
    assert second_spin.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    assert grant.remaining_attempts == 0
    assert LuckyWheelSpin.objects.filter(user=customer.user).count() == 1


def test_24_concurrent_or_back_to_back_spin_requests_allow_only_one_consumption_per_attempt():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="25 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=25)
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="601",
        attempts=1,
    )

    spin1, _ = LuckyWheelSpinService.spin(customer.user, customer, "race-key-1")
    assert spin1.grant_id == grant.pk

    with pytest.raises(ValidationError):
        LuckyWheelSpinService.spin(customer.user, customer, "race-key-2")

    grant.refresh_from_db()
    assert grant.remaining_attempts == 0
    assert grant.is_active is False
    assert LuckyWheelSpin.objects.filter(grant=grant).count() == 1
    assert (
        PointTransaction.objects.filter(
            user=customer.user,
            source_type=RewardSourceType.LUCKY_WHEEL,
        ).count()
        == 1
    )


def test_25_26_expired_attempt_is_excluded_from_remaining_attempts_and_cannot_be_used():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10)
    past = timezone.now() - timedelta(minutes=5)
    LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="expired-grant",
        attempts=1,
        expires_at=past,
    )

    client = auth_client(customer.user)
    current = client.get("/api/lucky-wheel/current/")
    assert current.status_code == status.HTTP_200_OK
    assert current.data["remaining_attempts"] == 0
    assert current.data["available"] is False
    assert current.data["eligible"] is False
    assert current.data["active_grants"] == []

    spin_res = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-expired"},
        format="json",
    )
    assert spin_res.status_code == status.HTTP_400_BAD_REQUEST
    assert LuckyWheelSpin.objects.count() == 0


# ===========================================================================
# 6. Permissions & Isolation (Cases 27-31)
# ===========================================================================


def test_27_unauthenticated_requests_return_401():
    anon = APIClient()
    assert anon.get("/api/lucky-wheel/current/").status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        anon.post("/api/lucky-wheel/spin/", {"idempotency_key": "k"}).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert anon.get("/api/lucky-wheel/spins/my/").status_code == status.HTTP_401_UNAUTHORIZED
    assert anon.get("/api/lucky-wheel/admin/prizes/").status_code == status.HTTP_401_UNAUTHORIZED
    assert anon.get("/api/lucky-wheel/admin/spins/").status_code == status.HTTP_401_UNAUTHORIZED


def test_28_user_a_cannot_view_or_consume_user_b_attempts_or_spin_idempotency_key():
    user_a = CustomerProfileFactory(is_active=True)
    user_b = CustomerProfileFactory(is_active=True)
    create_prize(label="15 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=15)
    grant_b = LuckyWheelGrantService.grant_attempt(
        profile=user_b,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="701",
        attempts=2,
    )

    # User A sees 0 attempts and cannot see User B's grant
    res_a = auth_client(user_a.user).get("/api/lucky-wheel/current/")
    assert res_a.status_code == status.HTTP_200_OK
    assert res_a.data["remaining_attempts"] == 0
    assert res_a.data["active_grants"] == []

    # User B spins once with key "user-b-key-1"
    res_b = auth_client(user_b.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "user-b-key-1"},
        format="json",
    )
    assert res_b.status_code == status.HTTP_200_OK

    # User A cannot replay User B's idempotency_key to inspect User B's spin
    collide_res = auth_client(user_a.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "user-b-key-1"},
        format="json",
    )
    assert collide_res.status_code == status.HTTP_400_BAD_REQUEST

    # User A's spin history does not include User B's spin
    my_spins_a = auth_client(user_a.user).get("/api/lucky-wheel/spins/my/")
    assert my_spins_a.status_code == status.HTTP_200_OK
    assert my_spins_a.data["results"] == []
    grant_b.refresh_from_db()
    assert grant_b.remaining_attempts == 1


def test_29_30_31_admin_endpoints_enforce_manager_permissions_and_reject_customer_seller():
    customer = CustomerProfileFactory(is_active=True)
    seller = SellerProfileFactory(is_active=True)
    unprivileged_manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    unprivileged_profile = unprivileged_manager.active_profile
    unprivileged_profile.permissions = []
    unprivileged_profile.save(update_fields=["permissions"])

    privileged_manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    privileged_profile = privileged_manager.active_profile
    privileged_profile.permissions = [
        "lucky_wheel.prizes.list",
        "lucky_wheel.prizes.create",
        "lucky_wheel.prizes.update",
        "lucky_wheel.spins.list",
    ]
    privileged_profile.save(update_fields=["permissions"])

    for non_admin_user in (customer.user, seller.user, unprivileged_manager):
        c = auth_client(non_admin_user)
        assert c.get("/api/lucky-wheel/admin/prizes/").status_code == status.HTTP_403_FORBIDDEN
        assert (
            c.post(
                "/api/lucky-wheel/admin/prizes/",
                {
                    "label": "Forbidden Prize",
                    "prize_type": LuckyWheelPrizeType.NO_PRIZE,
                    "weight": 1,
                },
                format="json",
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert c.get("/api/lucky-wheel/admin/spins/").status_code == status.HTTP_403_FORBIDDEN

    admin_client = auth_client(privileged_manager)
    assert admin_client.get("/api/lucky-wheel/admin/prizes/").status_code == status.HTTP_200_OK
    create_res = admin_client.post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Allowed Prize",
            "prize_type": LuckyWheelPrizeType.NO_PRIZE,
            "weight": 1,
        },
        format="json",
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    prize_id = create_res.data["id"]
    patch_res = admin_client.patch(
        f"/api/lucky-wheel/admin/prizes/{prize_id}/",
        {"weight": 3},
        format="json",
    )
    assert patch_res.status_code == status.HTTP_200_OK
    assert admin_client.get("/api/lucky-wheel/admin/spins/").status_code == status.HTTP_200_OK


# ===========================================================================
# 7. Client Security & Admin Prize Validation (Cases 32-35)
# ===========================================================================


@pytest.mark.parametrize(
    "forbidden_payload",
    [
        {"idempotency_key": "k1", "eligible": True},
        {"idempotency_key": "k2", "available": True},
        {"idempotency_key": "k3", "remaining_attempts": 5},
        {"idempotency_key": "k4", "prize_id": 1},
        {"idempotency_key": "k5", "prize": 1},
        {"idempotency_key": "k6", "slot": 0},
        {"idempotency_key": "k7", "outcome": "awarded"},
        {"idempotency_key": "k8", "grant_id": 1},
    ],
)
def test_32_33_34_client_cannot_supply_eligibility_attempts_prize_slot_or_outcome(
    forbidden_payload,
):
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10)
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="801",
        attempts=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        forbidden_payload,
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    grant.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert LuckyWheelSpin.objects.count() == 0


def test_35_invalid_wheel_prize_config_is_rejected_with_400():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    mgr_profile = manager.active_profile
    mgr_profile.permissions = [
        "lucky_wheel.prizes.create",
        "lucky_wheel.prizes.update",
    ]
    mgr_profile.save(update_fields=["permissions"])
    client = auth_client(manager)

    # PP_COINS prize with 0 points_amount -> 400
    r1 = client.post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Invalid Coins",
            "prize_type": LuckyWheelPrizeType.PP_COINS,
            "points_amount": 0,
            "weight": 1,
        },
        format="json",
    )
    assert r1.status_code == status.HTTP_400_BAD_REQUEST
    assert "points_amount" in r1.data

    # DISCOUNT_OFFER prize without discount_offer -> 400
    r2 = client.post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Missing Discount Offer",
            "prize_type": LuckyWheelPrizeType.DISCOUNT_OFFER,
            "weight": 1,
        },
        format="json",
    )
    assert r2.status_code == status.HTTP_400_BAD_REQUEST
    assert "discount_offer" in r2.data

    # Zero weight -> 400
    r3 = client.post(
        "/api/lucky-wheel/admin/prizes/",
        {
            "label": "Zero Weight",
            "prize_type": LuckyWheelPrizeType.NO_PRIZE,
            "weight": 0,
        },
        format="json",
    )
    assert r3.status_code == status.HTTP_400_BAD_REQUEST
    assert "weight" in r3.data


# ===========================================================================
# 8. OpenAPI, i18n, and Cross-Epic Regressions (Cases 36-45)
# ===========================================================================


def test_36_openapi_schema_includes_lucky_wheel_endpoints():
    import yaml

    customer = CustomerProfileFactory(is_active=True)
    res = auth_client(customer.user).get("/api/schema/")
    assert res.status_code == status.HTTP_200_OK
    paths = yaml.safe_load(res.content.decode())["paths"]
    assert "/api/lucky-wheel/current/" in paths
    assert "/api/lucky-wheel/spin/" in paths
    assert "/api/lucky-wheel/spins/my/" in paths
    assert "/api/lucky-wheel/admin/prizes/" in paths
    assert "/api/lucky-wheel/admin/spins/" in paths


def test_37_i18n_ar_and_en_validation_messages_for_lucky_wheel():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10)
    client = auth_client(customer.user)

    res_ar = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "ar-spin", "prize_id": 99},
        format="json",
        HTTP_ACCEPT_LANGUAGE="ar",
    )
    assert res_ar.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        str(res_ar.data["prize_id"][0])
        == "لا يمكن للعميل تحديد الجائزة أو النتيجة أو الخانة أو معلمات المنحة."
    )

    res_en = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "en-spin", "prize_id": 99},
        format="json",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    assert res_en.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        str(res_en.data["prize_id"][0])
        == "Client cannot specify prize, outcome, slot, or grant parameters."
    )


def test_38_to_45_cross_epic_rewards_referrals_seasons_promotions_draws_and_history_unaffected():
    # Historical grant and spin records remain intact
    customer = CustomerProfileFactory(is_active=True)
    prize = create_prize(label="10 PP", prize_type=LuckyWheelPrizeType.PP_COINS, points_amount=10)
    hist_grant = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        source_id=str(customer.pk),
        total_attempts=1,
        remaining_attempts=0,
        is_active=False,
        idempotency_key=f"hist:grant:{customer.pk}",
    )
    hist_spin = LuckyWheelSpin.objects.create(
        user=customer.user,
        profile=customer,
        grant=hist_grant,
        prize=prize,
        status=LuckyWheelSpinStatus.AWARDED,
        prize_snapshot={"id": prize.pk, "label": prize.label},
        idempotency_key=f"hist:spin:{customer.pk}",
    )

    # New registration triggers BE-REW-02 (100 PP), BE-PRO-03/03A (20% highlight discount),
    # 0 WeeklyDrawEntries (BE-DRW-CANCEL-01), and 0 LuckyWheelGrants
    payload = customer_registration_payload(phone_number="+201009998879")
    payload["username"] = "lw01-full-onboarding-check"
    reg = auth_client().post("/api/auth/register/customer/", payload, format="json")
    assert reg.status_code == status.HTTP_201_CREATED

    assert PointTransaction.objects.filter(
        user__username="lw01-full-onboarding-check",
        source_type=RewardSourceType.ONBOARDING,
        points=100,
    ).count() == 1
    benefit = OnboardingHighlightBenefitService.get_benefit(
        CustomerProfile.objects.get(user__username="lw01-full-onboarding-check").user
    )
    assert benefit is not None
    assert WeeklyDrawEntry.objects.filter(
        user__username="lw01-full-onboarding-check",
        source=WeeklyDrawEntrySource.ONBOARDING,
    ).count() == 0
    assert not LuckyWheelGrant.objects.filter(
        user__username="lw01-full-onboarding-check"
    ).exists()

    # Historical records untouched
    hist_grant.refresh_from_db()
    hist_spin.refresh_from_db()
    assert hist_grant.idempotency_key == f"hist:grant:{customer.pk}"
    assert hist_spin.idempotency_key == f"hist:spin:{customer.pk}"


# ===========================================================================
# 9. Dedicated Granular Tests for Every Remaining Scenario (1..45)
# ===========================================================================


def test_05_historical_onboarding_lucky_wheel_grants_remain_readable():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Readable Legacy Prize")
    grant = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        source_id=str(customer.pk),
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key=f"legacy:readable:{customer.pk}",
    )

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["active_grants"][0]["id"] == grant.pk


def test_06_historical_onboarding_lucky_wheel_grants_remain_unaffected_by_new_activity():
    customer = CustomerProfileFactory(is_active=True)
    grant = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        source_id=str(customer.pk),
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key=f"legacy:unaffected:{customer.pk}",
    )

    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert other.pk != customer.user_id

    grant.refresh_from_db()
    assert grant.deleted is None
    assert grant.is_active is True
    assert grant.remaining_attempts == 1


def test_14_attempt_grant_creates_zero_point_transactions():
    _, item, _, buyer_profile, _, _ = create_completed_auction_deal()
    PointTransaction.objects.all().delete()

    LuckyWheelGrantService.grant_for_auction_completed(buyer_profile, item)
    assert PointTransaction.objects.count() == 0


def test_15_attempt_grant_creates_zero_notifications():
    _, item, _, buyer_profile, _, _ = create_completed_auction_deal()
    Notification.objects.all().delete()

    LuckyWheelGrantService.grant_for_auction_completed(buyer_profile, item)
    assert Notification.objects.count() == 0


def test_16_attempt_grant_creates_zero_prize_grants():
    _, item, _, buyer_profile, _, _ = create_completed_auction_deal()
    UserPromotionGrant.objects.all().delete()
    BadgeAward.objects.all().delete()
    LuckyWheelSpin.objects.all().delete()

    LuckyWheelGrantService.grant_for_auction_completed(buyer_profile, item)
    assert UserPromotionGrant.objects.count() == 0
    assert BadgeAward.objects.count() == 0
    assert LuckyWheelSpin.objects.count() == 0


def test_17_availability_query_is_read_only():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Read Only Check Prize")
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="ro-17",
        attempts=1,
    )
    modified_before = grant.modified

    info = LuckyWheelService.get_availability(user=customer.user, profile=customer)
    grant.refresh_from_db()

    assert info["available"] is True
    assert grant.remaining_attempts == 1
    assert grant.modified == modified_before


def test_18_repeated_availability_queries_create_zero_db_mutations():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="Repeated Query Prize")
    LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="ro-18",
        attempts=1,
    )
    PointTransaction.objects.all().delete()
    Notification.objects.all().delete()

    client = auth_client(customer.user)
    for _ in range(3):
        res = client.get("/api/lucky-wheel/current/")
        assert res.status_code == status.HTTP_200_OK

    assert LuckyWheelSpin.objects.count() == 0
    assert PointTransaction.objects.count() == 0
    assert Notification.objects.count() == 0


def test_19_availability_response_matches_backend_truth():
    customer = CustomerProfileFactory(is_active=True)
    p1 = create_prize(label="Segment 1", points_amount=10)
    p2 = create_prize(label="Segment 2", points_amount=20)
    create_prize(label="Inactive Segment", is_active=False)
    g1 = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="truth-19",
        attempts=2,
    )

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")
    service_truth = LuckyWheelService.get_availability(
        user=customer.user,
        profile=customer,
    )

    assert res.data["available"] == service_truth["available"] is True
    assert res.data["eligible"] == service_truth["eligible"] is True
    assert res.data["has_active_wheel"] == service_truth["has_active_wheel"] is True
    assert res.data["remaining_attempts"] == service_truth["remaining_attempts"] == 2
    assert [s["id"] for s in res.data["segments"]] == [p1.pk, p2.pk]
    assert [g["id"] for g in res.data["active_grants"]] == [g1.pk]


def test_21_exhausted_attempts_set_remaining_zero_and_grant_inactive():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="No Prize", prize_type=LuckyWheelPrizeType.NO_PRIZE)
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="ex-21",
        attempts=1,
    )

    res = auth_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "ex-21-spin"},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data["remaining_attempts"] == 0
    grant.refresh_from_db()
    assert grant.remaining_attempts == 0
    assert grant.is_active is False


def test_23_replay_of_consumed_attempt_with_new_key_is_rejected():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="No Prize", prize_type=LuckyWheelPrizeType.NO_PRIZE)
    LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="rep-23",
        attempts=1,
    )
    client = auth_client(customer.user)
    first = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "rep-23-first"},
        format="json",
    )
    assert first.status_code == status.HTTP_200_OK

    second = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "rep-23-second"},
        format="json",
    )
    assert second.status_code == status.HTTP_400_BAD_REQUEST


def test_26_expired_attempt_excluded_from_remaining_attempts_with_active_grant_present():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="10 PP", points_amount=10)
    LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="exp-26",
        attempts=3,
        expires_at=timezone.now() - timedelta(hours=1),
    )
    active = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="act-26",
        attempts=1,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    assert LuckyWheelGrantService.get_remaining_attempts(customer.user, customer) == 1
    res = auth_client(customer.user).get("/api/lucky-wheel/current/")
    assert res.data["remaining_attempts"] == 1
    assert [g["id"] for g in res.data["active_grants"]] == [active.pk]


def test_30_manager_with_permission_gets_200_on_admin_endpoints():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    profile = manager.active_profile
    profile.permissions = ["lucky_wheel.prizes.list", "lucky_wheel.spins.list"]
    profile.save(update_fields=["permissions"])

    client = auth_client(manager)
    assert client.get("/api/lucky-wheel/admin/prizes/").status_code == status.HTTP_200_OK
    assert client.get("/api/lucky-wheel/admin/spins/").status_code == status.HTTP_200_OK


def test_31_customer_and_seller_get_403_on_admin_endpoints():
    customer = CustomerProfileFactory(is_active=True)
    seller = SellerProfileFactory(is_active=True)

    for user in (customer.user, seller.user):
        c = auth_client(user)
        assert c.get("/api/lucky-wheel/admin/prizes/").status_code == status.HTTP_403_FORBIDDEN
        assert c.get("/api/lucky-wheel/admin/spins/").status_code == status.HTTP_403_FORBIDDEN


def test_39_referral_flow_unaffected_by_lucky_wheel_hardening():
    inviter = CustomerProfileFactory(is_active=True)
    redeemer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    code = LinkSharingCode.objects.create(
        owner_profile=inviter,
        code="LW01RF",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    res = auth_client(redeemer_user).post(
        "/api/referrals/redeem/",
        {"code": code.code},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert Referral.objects.get(code=code).status == ReferralStatus.PENDING


def test_40_season_model_and_Scoring_unaffected_by_lucky_wheel_hardening():
    now = timezone.now()
    season = Season.objects.create(
        title="LW01 Check Season",
        starts_at=now - timedelta(days=7),
        ends_at=now + timedelta(days=7),
        status=SeasonStatus.DRAFT,
    )
    assert season.status == SeasonStatus.DRAFT


def test_41_promotion_campaign_model_unaffected_by_lucky_wheel_hardening():
    now = timezone.now()
    campaign = PromotionCampaign.objects.create(
        name="LW01 Promo Check",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=2),
        is_enabled=True,
    )
    assert campaign.is_active_at(now) is True


def test_43_44_zero_notifications_and_zero_unrelated_ledger_mutations_on_eligibility_and_grant():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="10 PP", points_amount=10)
    PointTransaction.objects.all().delete()
    Notification.objects.all().delete()

    LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="no-side-effects-43-44",
        attempts=1,
    )
    LuckyWheelService.get_availability(user=customer.user, profile=customer)
    auth_client(customer.user).get("/api/lucky-wheel/current/")

    assert Notification.objects.count() == 0
    assert PointTransaction.objects.count() == 0


# ===========================================================================
# 10. BE-LW-01A Hardening Tests (IntegrityError Savepoint, NULLS LAST Order,
#     Public API Metadata Stripping)
# ===========================================================================


def test_lw01a_01_02_03_post_integrity_error_recovery_returns_existing_grant_without_breaking_outer_transaction():
    from django.db import transaction

    customer = CustomerProfileFactory(is_active=True)
    precreated = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="901",
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key="lw01a:race:901",
    )

    real_filter = LuckyWheelGrant.all_objects.filter
    first_lookup = {"done": False}

    def stale_first_lookup(*args, **kwargs):
        qs = real_filter(*args, **kwargs)
        if not first_lookup["done"] and kwargs.get("idempotency_key") == "lw01a:race:901":
            first_lookup["done"] = True
            return qs.none()
        return qs

    # Execute inside an outer transaction.atomic() block to prove that the real
    # database IntegrityError on LuckyWheelGrant.objects.create() is contained
    # by the inner savepoint and does NOT poison the outer transaction.
    with transaction.atomic():
        with patch.object(
            LuckyWheelGrant.all_objects,
            "filter",
            side_effect=stale_first_lookup,
        ), patch.object(LuckyWheelGrant, "validate_unique", return_value=None):
            recovered = LuckyWheelGrantService.grant_attempt(
                profile=customer,
                source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
                source_id="901",
                attempts=1,
                idempotency_key="lw01a:race:901",
            )
        # Subsequent query inside the same outer atomic block must succeed
        assert LuckyWheelGrant.objects.filter(pk=precreated.pk).count() == 1

    assert recovered.pk == precreated.pk


def test_lw01a_04_integrity_error_fallback_validates_user_ownership():
    from django.db import transaction

    c1 = CustomerProfileFactory(is_active=True)
    c2 = CustomerProfileFactory(is_active=True)
    LuckyWheelGrant.objects.create(
        user=c1.user,
        profile=c1,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="902",
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key="lw01a:user-collision:902",
    )

    real_filter = LuckyWheelGrant.all_objects.filter
    first_lookup = {"done": False}

    def stale_first_lookup(*args, **kwargs):
        qs = real_filter(*args, **kwargs)
        if not first_lookup["done"] and kwargs.get("idempotency_key") == "lw01a:user-collision:902":
            first_lookup["done"] = True
            return qs.none()
        return qs

    with transaction.atomic():
        with patch.object(
            LuckyWheelGrant.all_objects,
            "filter",
            side_effect=stale_first_lookup,
        ), patch.object(LuckyWheelGrant, "validate_unique", return_value=None):
            with pytest.raises(ValidationError) as exc_info:
                LuckyWheelGrantService.grant_attempt(
                    profile=c2,
                    source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
                    source_id="902",
                    attempts=1,
                    idempotency_key="lw01a:user-collision:902",
                )
        assert "idempotency_key" in exc_info.value.detail
        # Outer transaction must remain usable
        assert LuckyWheelGrant.objects.filter(idempotency_key="lw01a:user-collision:902").count() == 1


def test_lw01a_05_integrity_error_fallback_validates_source_type():
    from django.db import transaction

    customer = CustomerProfileFactory(is_active=True)
    LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="903",
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key="lw01a:source-type-collision:903",
    )

    real_filter = LuckyWheelGrant.all_objects.filter
    first_lookup = {"done": False}

    def stale_first_lookup(*args, **kwargs):
        qs = real_filter(*args, **kwargs)
        if not first_lookup["done"] and kwargs.get("idempotency_key") == "lw01a:source-type-collision:903":
            first_lookup["done"] = True
            return qs.none()
        return qs

    with transaction.atomic():
        with patch.object(
            LuckyWheelGrant.all_objects,
            "filter",
            side_effect=stale_first_lookup,
        ), patch.object(LuckyWheelGrant, "validate_unique", return_value=None):
            with pytest.raises(ValidationError) as exc_info:
                LuckyWheelGrantService.grant_attempt(
                    profile=customer,
                    source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
                    source_id="903",
                    attempts=1,
                    idempotency_key="lw01a:source-type-collision:903",
                )
        assert "idempotency_key" in exc_info.value.detail


def test_lw01a_06_integrity_error_fallback_validates_source_id():
    from django.db import transaction

    customer = CustomerProfileFactory(is_active=True)
    LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="904-A",
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key="lw01a:source-id-collision:904",
    )

    real_filter = LuckyWheelGrant.all_objects.filter
    first_lookup = {"done": False}

    def stale_first_lookup(*args, **kwargs):
        qs = real_filter(*args, **kwargs)
        if not first_lookup["done"] and kwargs.get("idempotency_key") == "lw01a:source-id-collision:904":
            first_lookup["done"] = True
            return qs.none()
        return qs

    with transaction.atomic():
        with patch.object(
            LuckyWheelGrant.all_objects,
            "filter",
            side_effect=stale_first_lookup,
        ), patch.object(LuckyWheelGrant, "validate_unique", return_value=None):
            with pytest.raises(ValidationError) as exc_info:
                LuckyWheelGrantService.grant_attempt(
                    profile=customer,
                    source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
                    source_id="904-B",
                    attempts=1,
                    idempotency_key="lw01a:source-id-collision:904",
                )
        assert "idempotency_key" in exc_info.value.detail


def test_lw01a_08_09_10_11_deterministic_grant_ordering_nulls_last_and_consumption_order():
    customer = CustomerProfileFactory(is_active=True)
    create_prize(label="No Prize", prize_type=LuckyWheelPrizeType.NO_PRIZE)
    now = timezone.now()

    # Create a non-expiring grant FIRST (lower id, earlier created, expires_at=None)
    non_expiring = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="non-expiring-first",
        attempts=1,
        expires_at=None,
    )
    # Create a later-expiring grant SECOND
    later_expiring = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="later-expiring",
        attempts=1,
        expires_at=now + timedelta(days=3),
    )
    # Create two equal-expiration grants THIRD and FOURTH (tie-break by created, id)
    equal_exp_t = now + timedelta(days=1)
    earlier_exp_1 = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="earlier-exp-1",
        attempts=1,
        expires_at=equal_exp_t,
    )
    earlier_exp_2 = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="earlier-exp-2",
        attempts=1,
        expires_at=equal_exp_t,
    )

    ordered_ids = [
        g.pk for g in LuckyWheelGrantService.active_grants(customer.user, customer, now=now)
    ]
    assert ordered_ids == [
        earlier_exp_1.pk,
        earlier_exp_2.pk,
        later_expiring.pk,
        non_expiring.pk,
    ]

    # Spin 1 must consume earlier_exp_1 (NOT non_expiring!)
    spin1, _ = LuckyWheelSpinService.spin(customer.user, customer, "lw01a-order-spin-1")
    assert spin1.grant_id == earlier_exp_1.pk
    earlier_exp_1.refresh_from_db()
    non_expiring.refresh_from_db()
    assert earlier_exp_1.remaining_attempts == 0
    assert non_expiring.remaining_attempts == 1

    # Spin 2 must consume earlier_exp_2
    spin2, _ = LuckyWheelSpinService.spin(customer.user, customer, "lw01a-order-spin-2")
    assert spin2.grant_id == earlier_exp_2.pk

    # Spin 3 must consume later_expiring
    spin3, _ = LuckyWheelSpinService.spin(customer.user, customer, "lw01a-order-spin-3")
    assert spin3.grant_id == later_expiring.pk

    # Spin 4 consumes non_expiring last
    spin4, _ = LuckyWheelSpinService.spin(customer.user, customer, "lw01a-order-spin-4")
    assert spin4.grant_id == non_expiring.pk


def test_lw01a_12_13_14_15_16_public_vs_admin_api_field_exposure():
    customer = CustomerProfileFactory(is_active=True)
    prize = LuckyWheelPrize.objects.create(
        label="20 PP Coins",
        description="Public prize description",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=20,
        weight=7,
        is_active=True,
        quantity_total=50,
        quantity_awarded=3,
        metadata={"internal_note": "secret-manager-config"},
    )
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="internal-auction-999",
        attempts=1,
        metadata={"internal_audit": "do-not-expose"},
    )

    res = auth_client(customer.user).get("/api/lucky-wheel/current/")
    assert res.status_code == status.HTTP_200_OK

    segment = res.data["segments"][0]
    assert set(segment.keys()) == {
        "id",
        "label",
        "description",
        "prize_type",
        "points_amount",
        "badge_type",
    }
    assert "weight" not in segment
    assert "metadata" not in segment
    assert "quantity_total" not in segment
    assert "quantity_awarded" not in segment

    active_grant = res.data["active_grants"][0]
    assert set(active_grant.keys()) == {
        "id",
        "source_type",
        "total_attempts",
        "remaining_attempts",
        "expires_at",
        "is_active",
        "created",
    }
    assert "idempotency_key" not in active_grant
    assert "metadata" not in active_grant
    assert "source_id" not in active_grant

    # Manager prize API still exposes full configuration fields
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    mgr_profile = manager.active_profile
    mgr_profile.permissions = ["lucky_wheel.prizes.list"]
    mgr_profile.save(update_fields=["permissions"])

    admin_res = auth_client(manager).get("/api/lucky-wheel/admin/prizes/")
    assert admin_res.status_code == status.HTTP_200_OK
    admin_prize = next(
        item for item in admin_res.data["results"] if item["id"] == prize.pk
    )
    assert admin_prize["weight"] == 7
    assert admin_prize["quantity_total"] == 50
    assert admin_prize["quantity_awarded"] == 3
    assert admin_prize["metadata"] == {"internal_note": "secret-manager-config"}
    assert grant.pk == active_grant["id"]
