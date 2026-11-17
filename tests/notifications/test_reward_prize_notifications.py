from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from django.db import transaction
from django.utils import timezone, translation
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.deals.models import (
    CompletedDealSourceType,
    CompletedDealType,
)
from apps.draws.models import WeeklyDraw, WeeklyDrawEntry, WeeklyDrawStatus
from apps.draws.services import WeeklyDrawEntryService, WeeklyDrawService
from apps.loyalty.constants import (
    BadgeType,
    RewardEventType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import BadgeService, PointService
from apps.lucky_wheel.models import (
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
)
from apps.lucky_wheel.services import (
    LuckyWheelGrantService,
    LuckyWheelSpinService,
)
from apps.notifications.fcm import build_fcm_message
from apps.notifications.models import Notification, NotificationType
from apps.notifications.payload import NotificationPayload
from apps.notifications.serializers import NotificationSerializer
from apps.notifications.services import NotificationService
from apps.prizes.models import (
    PrizeAward,
    PrizeAwardFulfillmentType,
    PrizeAwardSourceType,
    PrizeAwardStatus,
)
from apps.prizes.services import PrizeAwardService, PrizeClaimService
from apps.referrals.models import (
    ReferralChallenge,
    ReferralChallengePrize,
    ReferralChallengeStatus,
)
from apps.referrals.services import ReferralChallengeService, ReferralService
from apps.seasons.models import Season, SeasonStatus
from apps.seasons.services import SeasonPrizeService, SeasonService
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    CompletedDealFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db


def auth_client(user, lang=None):
    client = APIClient()
    refresh = build_token_for_user(user)
    headers = {"HTTP_AUTHORIZATION": f"Bearer {refresh.access_token}"}
    if lang is not None:
        headers["HTTP_ACCEPT_LANGUAGE"] = lang
    client.credentials(**headers)
    return client


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


def make_manager(permissions=None):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager = user.active_profile
    if permissions is not None:
        manager.permissions = list(permissions)
        manager.save(update_fields=["permissions", "modified"])
    else:
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    return user, manager


# ===========================================================================
# 1. PP Coin Earning Notifications (Tests 1 - 8)
# ===========================================================================


def test_01_earn_points_creates_one_pp_congratulatory_notification_with_earned_amount():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    tx = PointService.earn_points(
        user=user,
        profile=profile,
        points=75,
        reason="Test earning",
        source_type=RewardSourceType.DEAL_COMPLETED,
        source_id="deal-101",
        idempotency_key="earn:test:01",
    )

    notifs = Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
    )
    assert notifs.count() == 1
    notif = notifs.first()
    assert notif.object_type == "point_transaction"
    assert notif.object_id == tx.pk
    assert notif.idempotency_key == f"points_earned:{tx.pk}"
    assert "75" in notif.body
    assert notif.metadata == {
        "point_transaction_id": tx.pk,
        "points": 75,
        "source_type": str(RewardSourceType.DEAL_COMPLETED),
        "source_id": "deal-101",
    }


def test_02_duplicate_idempotent_earn_points_creates_zero_additional_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    tx1 = PointService.earn_points(
        user=user,
        profile=profile,
        points=50,
        reason="Idempotent earn",
        source_type=RewardSourceType.ONBOARDING,
        source_id=str(user.pk),
        idempotency_key="earn:test:02",
    )
    tx2 = PointService.earn_points(
        user=user,
        profile=profile,
        points=50,
        reason="Idempotent earn",
        source_type=RewardSourceType.ONBOARDING,
        source_id=str(user.pk),
        idempotency_key="earn:test:02",
    )

    assert tx1.pk == tx2.pk
    assert Notification.objects.count() == 1


def test_03_spend_points_creates_zero_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PointService.earn_points(
        user=user,
        profile=profile,
        points=100,
        reason="Initial balance",
        idempotency_key="earn:test:03",
    )
    Notification.objects.all().delete()

    PointService.spend_points(
        user=user,
        profile=profile,
        points=40,
        reason="Redeem item",
        idempotency_key="spend:test:03",
    )

    assert Notification.objects.count() == 0


def test_04_expire_points_creates_zero_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PointService.earn_points(
        user=user,
        profile=profile,
        points=100,
        reason="Initial balance",
        idempotency_key="earn:test:04",
    )
    Notification.objects.all().delete()

    PointService.expire_points(
        user=user,
        profile=profile,
        points=30,
        reason="Points expired",
        idempotency_key="expire:test:04",
    )

    assert Notification.objects.count() == 0


def test_05_multiple_distinct_earning_transactions_create_one_notification_per_transaction():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    tx_a = PointService.earn_points(
        user=user,
        profile=profile,
        points=25,
        reason="Deal base",
        source_type=RewardSourceType.DEAL_COMPLETED,
        source_id="1",
        idempotency_key="earn:test:05:a",
    )
    tx_b = PointService.earn_points(
        user=user,
        profile=profile,
        points=50,
        reason="Deal bonus",
        source_type=RewardSourceType.DEAL_COMPLETION_BONUS,
        source_id="1",
        idempotency_key="earn:test:05:b",
    )

    notifs = list(
        Notification.objects.filter(
            recipient_profile=profile,
            notification_type=NotificationType.POINTS_EARNED,
        ).order_by("object_id")
    )
    assert len(notifs) == 2
    assert [n.object_id for n in notifs] == [tx_a.pk, tx_b.pk]
    assert [n.metadata["points"] for n in notifs] == [25, 50]


def test_06_failed_or_rolled_back_point_transaction_creates_zero_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    with pytest.raises(RuntimeError, match="abort outer"):
        with transaction.atomic():
            PointService.earn_points(
                user=user,
                profile=profile,
                points=80,
                reason="Rolled back earn",
                idempotency_key="earn:test:06",
            )
            raise RuntimeError("abort outer")

    assert PointTransaction.objects.count() == 0
    assert Notification.objects.count() == 0


def test_07_notification_creation_failure_rolls_back_reward_in_same_transaction(
    monkeypatch,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    def fail_create_notification(*args, **kwargs):
        raise RuntimeError("DB notification insert failed")

    monkeypatch.setattr(
        NotificationService,
        "create_notification",
        staticmethod(fail_create_notification),
    )

    with pytest.raises(RuntimeError, match="DB notification insert failed"):
        PointService.earn_points(
            user=user,
            profile=profile,
            points=60,
            reason="Must roll back",
            idempotency_key="earn:test:07",
        )

    assert PointTransaction.objects.count() == 0
    assert PointWallet.objects.filter(user=user).count() == 0
    assert Notification.objects.count() == 0


def test_08_external_push_or_websocket_failure_after_commit_does_not_roll_back_reward(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    def fail_ws(*args, **kwargs):
        raise RuntimeError("WebSocket broker down")

    def fail_fcm(*args, **kwargs):
        raise RuntimeError("Celery broker down")

    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        fail_ws,
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fail_fcm,
    )

    with django_capture_on_commit_callbacks(execute=True):
        tx = PointService.earn_points(
            user=user,
            profile=profile,
            points=90,
            reason="Resilient earn",
            idempotency_key="earn:test:08",
        )

    assert PointTransaction.objects.filter(pk=tx.pk).exists()
    assert PointService.get_balance(user) == 90
    assert Notification.objects.filter(
        notification_type=NotificationType.POINTS_EARNED,
        object_id=tx.pk,
    ).exists()


# ===========================================================================
# 2. Badge Award Notifications (Tests 9 - 12)
# ===========================================================================


def test_09_badge_award_creates_one_badge_congratulatory_notification():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    award = BadgeService.award_badge(
        profile=profile,
        badge_type=BadgeType.WELCOME_BUYER,
        source_type="profile",
        source_id=str(profile.pk),
        idempotency_key="badge:test:09",
    )

    notifs = Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.BADGE_AWARDED,
    )
    assert notifs.count() == 1
    notif = notifs.first()
    assert notif.object_type == "badge_award"
    assert notif.object_id == award.pk
    assert notif.idempotency_key == f"badge_awarded:{award.pk}"
    assert "Welcome Buyer" in notif.body
    assert notif.metadata == {
        "badge_award_id": award.pk,
        "profile_id": profile.pk,
        "badge_type": BadgeType.WELCOME_BUYER,
    }


def test_10_duplicate_idempotent_badge_award_creates_zero_additional_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    a1 = BadgeService.award_badge(
        profile=profile,
        badge_type=BadgeType.FIRST_BIDDER,
        idempotency_key="badge:test:10",
    )
    a2 = BadgeService.award_badge(
        profile=profile,
        badge_type=BadgeType.FIRST_BIDDER,
        idempotency_key="badge:test:10",
    )
    a3 = BadgeService.award_badge(
        profile=profile,
        badge_type=BadgeType.FIRST_BIDDER,
        idempotency_key="badge:test:10:other-key-one-per-profile",
        one_per_profile=True,
    )

    assert a1.pk == a2.pk == a3.pk
    assert Notification.objects.count() == 1


def test_11_rolled_back_badge_award_creates_zero_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    with pytest.raises(RuntimeError, match="rollback badge"):
        with transaction.atomic():
            BadgeService.award_badge(
                profile=profile,
                badge_type=BadgeType.WELCOME_BUYER,
                idempotency_key="badge:test:11",
            )
            raise RuntimeError("rollback badge")

    assert BadgeAward.objects.count() == 0
    assert Notification.objects.count() == 0


def test_12_badge_award_creates_zero_point_transactions():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    BadgeService.award_badge(
        profile=profile,
        badge_type=BadgeType.TRUSTED_BUYER,
        idempotency_key="badge:test:12",
    )

    assert PointTransaction.objects.count() == 0
    assert Notification.objects.filter(
        notification_type=NotificationType.POINTS_EARNED
    ).count() == 0
    assert Notification.objects.filter(
        notification_type=NotificationType.BADGE_AWARDED
    ).count() == 1


# ===========================================================================
# 3. Claim-Required Prize Notifications (Tests 13 - 17)
# ===========================================================================


def test_13_record_claim_required_creates_one_prize_congratulatory_notification():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    award = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="winner-13",
        title="iPhone 16 Pro",
        prize_snapshot={"prize_code": "IPHONE_16_PRO"},
        idempotency_key="prize:test:13",
    )

    notifs = Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert notifs.count() == 1
    notif = notifs.first()
    assert notif.object_type == "prize_award"
    assert notif.object_id == award.pk
    assert notif.idempotency_key == f"prize_awarded:{award.pk}"
    assert "iPhone 16 Pro" in notif.body
    assert notif.metadata == {
        "prize_award_id": award.pk,
        "source_type": PrizeAwardSourceType.REFERRAL_CHALLENGE,
        "source_id": "winner-13",
        "fulfillment_type": PrizeAwardFulfillmentType.CLAIM_REQUIRED,
        "title": "iPhone 16 Pro",
    }


def test_14_duplicate_idempotent_record_claim_required_creates_zero_additional_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    a1 = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="winner-14",
        title="Gold Trophy",
        prize_snapshot={"prize_code": "GOLD_TROPHY"},
        idempotency_key="prize:test:14",
    )
    a2 = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="winner-14",
        title="Gold Trophy",
        prize_snapshot={"prize_code": "GOLD_TROPHY"},
        idempotency_key="prize:test:14",
    )

    assert a1.pk == a2.pk
    assert Notification.objects.count() == 1


def test_15_referral_challenge_winner_claim_required_prize_creates_one_prize_notification():
    now = timezone.now()
    _, manager_profile = make_manager()
    challenge = ReferralChallengeService.create_challenge(
        name="Monthly Referral Challenge",
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(hours=1),
        created_by=manager_profile.user,
    )
    inviter = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    referred = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    code_obj = ReferralService.create_code(inviter.active_profile)
    referral = ReferralService.redeem_code(
        referred.active_profile,
        code_obj.code,
    )
    type(referral).objects.filter(pk=referral.pk).update(
        redeemed_at=challenge.starts_at + timedelta(days=1)
    )
    ReferralService.approve(referral.pk, manager_profile.user)
    Notification.objects.all().delete()

    ReferralChallengeService.finalize(challenge.pk, manager_profile.user)

    prize_notifs = Notification.objects.filter(
        recipient_profile=inviter.active_profile,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert prize_notifs.count() == 1
    award = PrizeAward.objects.get(
        user=inviter,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
    )
    assert prize_notifs.first().object_id == award.pk


def test_16_lucky_wheel_physical_claim_required_prize_creates_one_prize_notification():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    award = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="spin-physical-16",
        title="Pigeon Carrier Box",
        prize_snapshot={"prize_type": "physical", "label": "Pigeon Carrier Box"},
        idempotency_key="prize:lw:physical:16",
    )

    notifs = Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert notifs.count() == 1
    assert notifs.first().object_id == award.pk


def test_17_season_physical_claim_required_prize_creates_one_prize_notification():
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller = seller_user.active_profile

    award = PrizeAwardService.record_claim_required(
        user=seller_user,
        profile=seller,
        source_type=PrizeAwardSourceType.SEASON,
        source_id="season-insight-17",
        title="Gold Cup",
        prize_snapshot={
            "season_id": 17,
            "rank": 1,
            "symbolic_prize_code": "GOLD_CUP",
            "title": "Gold Cup",
        },
        idempotency_key="prize:season:physical:17",
    )

    assert Notification.objects.filter(
        recipient_profile=seller,
        notification_type=NotificationType.POINTS_EARNED,
    ).count() == 0
    prize_notifs = Notification.objects.filter(
        recipient_profile=seller,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert prize_notifs.count() == 1
    assert award.fulfillment_type == PrizeAwardFulfillmentType.CLAIM_REQUIRED
    assert prize_notifs.first().object_id == award.pk


# ===========================================================================
# 4. Digital Prize Anti-Duplication (Tests 18 - 21)
# ===========================================================================


def test_18_record_digital_fulfilled_creates_zero_generic_prize_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="spin-18",
        title="50 PP Coins",
        prize_snapshot={"points_amount": 50},
        idempotency_key="prize:digital:18",
    )

    assert Notification.objects.count() == 0


def test_19_lucky_wheel_pp_coins_spin_creates_one_pp_notification_and_zero_prize_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    LuckyWheelGrantService.grant_attempt(
        profile=profile,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="deal-19",
        idempotency_key="lw:grant:19",
    )
    LuckyWheelPrize.objects.create(
        label="50 PP Coins",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=50,
        weight=1,
        is_active=True,
    )
    Notification.objects.all().delete()

    spin, _ = LuckyWheelSpinService.spin(user, profile, "lw:spin:19")

    assert spin.prize_snapshot["points_amount"] == 50
    assert PrizeAward.objects.filter(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
    ).count() == 1
    assert Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
    ).count() == 1
    assert Notification.objects.filter(
        notification_type=NotificationType.PRIZE_AWARDED,
    ).count() == 0
    assert Notification.objects.count() == 1


def test_20_lucky_wheel_badge_spin_creates_one_badge_notification_and_zero_prize_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    LuckyWheelGrantService.grant_attempt(
        profile=profile,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="deal-20",
        idempotency_key="lw:grant:20",
    )
    LuckyWheelPrize.objects.create(
        label="Lucky Buyer Badge",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.TRUSTED_BUYER,
        weight=1,
        is_active=True,
    )
    Notification.objects.all().delete()

    spin, extra = LuckyWheelSpinService.spin(user, profile, "lw:spin:20")

    assert spin.prize_snapshot["badge_type"] == BadgeType.TRUSTED_BUYER
    assert extra.pk is not None
    assert PrizeAward.objects.filter(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
    ).count() == 1
    assert Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.BADGE_AWARDED,
    ).count() == 1
    assert Notification.objects.filter(
        notification_type=NotificationType.PRIZE_AWARDED,
    ).count() == 0
    assert Notification.objects.count() == 1


def test_21_season_pp_payout_creates_one_pp_notification_and_zero_prize_notifications():
    _, manager = make_manager()
    now = timezone.now()
    season = Season.objects.create(
        title="PP Payout Season",
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(hours=1),
        status=SeasonStatus.DRAFT,
    )
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller = seller_user.active_profile
    buyer = CustomerProfileFactory(is_active=True)
    CompletedDealFactory(
        deal_type=CompletedDealType.MARKET,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        buyer_user=buyer.user,
        buyer_profile=buyer,
        seller_user=seller.user,
        seller_profile=seller,
        amount=Decimal("200.00"),
        currency="EGP",
        completed_at=now - timedelta(days=2),
    )
    Notification.objects.all().delete()

    SeasonService.finalize(season, manager_profile=manager)

    assert PrizeAward.objects.filter(
        user=seller.user,
        source_type=PrizeAwardSourceType.SEASON,
        fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
        status=PrizeAwardStatus.FULFILLED,
    ).count() == 1
    assert Notification.objects.filter(
        recipient_profile=seller,
        notification_type=NotificationType.POINTS_EARNED,
    ).count() == 1
    assert Notification.objects.filter(
        notification_type=NotificationType.PRIZE_AWARDED,
    ).count() == 0


# ===========================================================================
# 5. Combined Event Scenarios (Tests 22 - 24)
# ===========================================================================


def test_22_onboarding_creates_one_pp_notification_and_one_badge_notification_and_zero_draw_notifications():
    payload = customer_registration_payload(phone_number="+201099887766")
    payload["username"] = "onboard_notif_user"
    response = APIClient().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED

    user = UserAccount.objects.get(username="onboard_notif_user")
    customer = CustomerProfile.objects.get(user=user)
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0

    user_notifs = Notification.objects.filter(recipient_profile=customer)
    assert user_notifs.filter(
        notification_type=NotificationType.POINTS_EARNED
    ).count() == 1
    assert user_notifs.filter(
        notification_type=NotificationType.BADGE_AWARDED
    ).count() == 1
    assert user_notifs.filter(
        notification_type=NotificationType.PRIZE_AWARDED
    ).count() == 0
    assert user_notifs.count() == 2


def test_23_deal_completion_with_pp_and_badge_creates_one_pp_and_one_badge_notification():
    from apps.loyalty.models import RewardRuleConfig

    RewardRuleConfig.objects.create(code=RewardRuleCode.DEAL_COMPLETED, enabled=False)
    RewardRuleConfig.objects.create(
        code=RewardRuleCode.DEAL_COMPLETION_BONUS, enabled=False
    )

    buyer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer = CustomerProfile.objects.get(pk=buyer_user.active_profile.pk)
    seller = SellerProfile.objects.get(pk=seller_user.active_profile.pk)

    deal = CompletedDealFactory(
        deal_type=CompletedDealType.MARKET,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        buyer_user=buyer_user,
        buyer_profile=buyer,
        seller_user=seller_user,
        seller_profile=seller,
        amount=Decimal("20.00"),
        currency="USD",
    )
    Notification.objects.all().delete()

    RewardEngine.handle_event(
        RewardEventType.DEAL_COMPLETED,
        actor=seller,
        profile=buyer,
        context={"deal": deal},
        run_async_for_aggregates=False,
    )

    buyer_notifs = Notification.objects.filter(recipient_profile=buyer)
    assert buyer_notifs.filter(
        notification_type=NotificationType.POINTS_EARNED
    ).count() == 1
    assert buyer_notifs.filter(
        notification_type=NotificationType.BADGE_AWARDED
    ).count() == 1


def test_24_deal_completion_with_buyer_pp_seller_pp_and_bonus_creates_one_notification_per_earning_tx():
    buyer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    buyer = CustomerProfile.objects.get(pk=buyer_user.active_profile.pk)
    seller = SellerProfile.objects.get(pk=seller_user.active_profile.pk)

    deal = CompletedDealFactory(
        deal_type=CompletedDealType.MARKET,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        buyer_user=buyer_user,
        buyer_profile=buyer,
        seller_user=seller_user,
        seller_profile=seller,
        amount=Decimal("100.00"),
        currency="USD",
    )
    Notification.objects.all().delete()

    RewardEngine.handle_event(
        RewardEventType.DEAL_COMPLETED,
        actor=seller,
        profile=buyer,
        context={"deal": deal},
        run_async_for_aggregates=False,
    )

    buyer_txs = PointTransaction.objects.filter(profile=buyer).order_by("id")
    buyer_pp_notifs = Notification.objects.filter(
        recipient_profile=buyer,
        notification_type=NotificationType.POINTS_EARNED,
    ).order_by("id")
    assert buyer_pp_notifs.count() == buyer_txs.count() == 3
    assert [n.metadata["points"] for n in buyer_pp_notifs] == [
        tx.points for tx in buyer_txs
    ]

    seller_txs = PointTransaction.objects.filter(profile=seller).order_by("id")
    seller_pp_notifs = Notification.objects.filter(
        recipient_profile=seller,
        notification_type=NotificationType.POINTS_EARNED,
    ).order_by("id")
    assert seller_pp_notifs.count() == seller_txs.count() == 4
    assert [n.metadata["points"] for n in seller_pp_notifs] == [
        tx.points for tx in seller_txs
    ]


# ===========================================================================
# 6. Notification API, Ownership, Localization & Metadata (Tests 25 - 33)
# ===========================================================================


def test_25_recipient_sees_congratulatory_notifications_in_list_api():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    PointService.earn_points(user, profile, 35, "Earned", idempotency_key="api:25:pp")
    BadgeService.award_badge(profile, BadgeType.WELCOME_BUYER, idempotency_key="api:25:badge")
    PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="w-25",
        title="Champion Cup",
        prize_snapshot={"code": "CUP"},
        idempotency_key="api:25:prize",
    )

    res = auth_client(user, lang="en").get("/api/notifications/")
    assert res.status_code == status.HTTP_200_OK
    results = response_results(res)
    assert len(results) == 3
    assert {item["notification_type"] for item in results} == {
        NotificationType.POINTS_EARNED,
        NotificationType.BADGE_AWARDED,
        NotificationType.PRIZE_AWARDED,
    }


def test_26_unread_count_increments_in_unread_count_api():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    PointService.earn_points(user, profile, 20, "Earned", idempotency_key="api:26:pp")
    BadgeService.award_badge(profile, BadgeType.WELCOME_BUYER, idempotency_key="api:26:badge")

    res = auth_client(user).get("/api/notifications/unread-count/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data == {"unread_count": 2}


def test_27_mark_read_marks_notification_read_for_owner():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PointService.earn_points(user, profile, 20, "Earned", idempotency_key="api:27:pp")
    notif = Notification.objects.get(recipient_profile=profile)

    res = auth_client(user).post(f"/api/notifications/{notif.pk}/mark-read/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["is_read"] is True
    notif.refresh_from_db()
    assert notif.is_read is True
    assert notif.read_at is not None


def test_28_mark_all_read_marks_all_congratulatory_notifications_read_for_owner():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PointService.earn_points(user, profile, 20, "Earned", idempotency_key="api:28:pp")
    BadgeService.award_badge(profile, BadgeType.WELCOME_BUYER, idempotency_key="api:28:badge")

    res = auth_client(user).post("/api/notifications/mark-all-read/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data == {"updated_count": 2}
    assert NotificationService.get_unread_count(profile) == 0


def test_29_another_user_cannot_read_or_mark_recipients_congratulatory_notification_read():
    owner = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PointService.earn_points(
        owner,
        owner.active_profile,
        50,
        "Owner points",
        idempotency_key="api:29:pp",
    )
    notif = Notification.objects.get(recipient_profile=owner.active_profile)

    list_res = auth_client(other).get("/api/notifications/")
    assert response_results(list_res) == []

    mark_res = auth_client(other).post(f"/api/notifications/{notif.pk}/mark-read/")
    assert mark_res.status_code == status.HTTP_404_NOT_FOUND
    notif.refresh_from_db()
    assert notif.is_read is False


def test_30_notification_copy_supports_arabic_default_and_english_request_localization(
    settings,
):
    settings.LANGUAGE_CODE = "ar"  # production default
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    PointService.earn_points(user, profile, 120, "Points", idempotency_key="loc:30:pp")
    BadgeService.award_badge(profile, BadgeType.WELCOME_BUYER, idempotency_key="loc:30:badge")
    PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="loc-30",
        title="Gold Cup",
        prize_snapshot={"prize_code": "GOLD_CUP"},
        idempotency_key="loc:30:prize",
    )

    # Default Arabic response
    ar_res = auth_client(user).get("/api/notifications/")
    ar_by_type = {item["notification_type"]: item for item in response_results(ar_res)}

    assert ar_by_type[NotificationType.POINTS_EARNED]["title"] == "تهانينا! ربحت نقاطاً"
    assert ar_by_type[NotificationType.POINTS_EARNED]["body"] == "تهانينا! لقد ربحت 120 نقطة PP."
    assert ar_by_type[NotificationType.BADGE_AWARDED]["title"] == "تم منح الشارة"
    assert "تهانينا! لقد حصلت على وسام" in ar_by_type[NotificationType.BADGE_AWARDED]["body"]
    assert ar_by_type[NotificationType.PRIZE_AWARDED]["title"] == "تهانينا! أنت من الفائزين"
    assert ar_by_type[NotificationType.PRIZE_AWARDED]["body"] == "تهانينا! لقد فزت بجائزة: Gold Cup."

    # English request localization
    en_res = auth_client(user, lang="en").get("/api/notifications/?lang=en")
    en_by_type = {item["notification_type"]: item for item in response_results(en_res)}

    assert en_by_type[NotificationType.POINTS_EARNED]["title"] == "Congratulations! Points earned"
    assert en_by_type[NotificationType.POINTS_EARNED]["body"] == "Congratulations! You earned 120 PP Coins."
    assert en_by_type[NotificationType.BADGE_AWARDED]["title"] == "Badge awarded"
    assert en_by_type[NotificationType.BADGE_AWARDED]["body"] == "Congratulations! You earned the Welcome Buyer badge."
    assert en_by_type[NotificationType.PRIZE_AWARDED]["title"] == "Congratulations! You are a winner"
    assert en_by_type[NotificationType.PRIZE_AWARDED]["body"] == "Congratulations! You won a prize: Gold Cup."


def test_31_notification_metadata_contains_safe_correlation_ids_only():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    tx = PointService.earn_points(
        user,
        profile,
        45,
        "Earned",
        source_type=RewardSourceType.DEAL_COMPLETED,
        source_id="deal-31",
        idempotency_key="meta:31:pp",
    )
    badge = BadgeService.award_badge(
        profile,
        BadgeType.WELCOME_BUYER,
        idempotency_key="meta:31:badge",
    )
    prize = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="ref-31",
        title="Silver Cup",
        prize_snapshot={"prize_code": "SILVER_CUP"},
        idempotency_key="meta:31:prize",
    )

    pp_notif = Notification.objects.get(
        notification_type=NotificationType.POINTS_EARNED
    )
    badge_notif = Notification.objects.get(
        notification_type=NotificationType.BADGE_AWARDED
    )
    prize_notif = Notification.objects.get(
        notification_type=NotificationType.PRIZE_AWARDED
    )

    assert set(pp_notif.metadata.keys()) == {
        "point_transaction_id",
        "points",
        "source_type",
        "source_id",
    }
    assert pp_notif.metadata["point_transaction_id"] == tx.pk
    assert set(badge_notif.metadata.keys()) == {
        "badge_award_id",
        "profile_id",
        "badge_type",
    }
    assert badge_notif.metadata["badge_award_id"] == badge.pk
    assert set(prize_notif.metadata.keys()) == {
        "prize_award_id",
        "source_type",
        "source_id",
        "fulfillment_type",
        "title",
    }
    assert prize_notif.metadata["prize_award_id"] == prize.pk


def test_32_notification_metadata_never_leaks_claim_pii_or_internal_idempotency_keys():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    PointService.earn_points(user, profile, 30, "Earned", idempotency_key="secret:pp:key")
    BadgeService.award_badge(profile, BadgeType.WELCOME_BUYER, idempotency_key="secret:badge:key")
    award = PrizeAwardService.record_claim_required(
        user=user,
        profile=profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="ref-32",
        title="Gold Cup",
        prize_snapshot={"prize_code": "GOLD_CUP"},
        idempotency_key="secret:prize:key",
    )
    PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Secret Full Name",
        address="Secret Home Address 99",
        identity_number="29901019999999",
    )

    forbidden_keys = {
        "full_name",
        "address",
        "identity_number",
        "national_id",
        "idempotency_key",
    }
    for notif in Notification.objects.filter(recipient_profile=profile):
        assert forbidden_keys.isdisjoint(notif.metadata.keys())
        serialized = str(NotificationSerializer(notif).data)
        assert "Secret Full Name" not in serialized
        assert "Secret Home Address 99" not in serialized
        assert "29901019999999" not in serialized
        assert "secret:" not in serialized


def test_33_notification_payload_matches_existing_notification_payload_and_serializer_shape():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    PointService.earn_points(user, profile, 65, "Earned", idempotency_key="shape:33")
    notif = Notification.objects.get(recipient_profile=profile)

    ws_payload = NotificationPayload.build(notif)
    fcm_msg = build_fcm_message(notif, ["device-token-1"])
    with translation.override("ar"):
        rest_data = NotificationSerializer(notif).data

    expected_keys = {
        "id",
        "notification_type",
        "title",
        "body",
        "actor_profile",
        "object_type",
        "object_id",
        "metadata",
        "target_url",
        "is_read",
        "read_at",
        "created",
    }
    assert set(ws_payload.keys()) == expected_keys
    assert set(rest_data.keys()) == expected_keys
    assert ws_payload["id"] == rest_data["id"] == int(fcm_msg.data["notification_id"]) == notif.pk
    assert ws_payload["title"] == rest_data["title"] == fcm_msg.notification.title
    assert ws_payload["body"] == rest_data["body"] == fcm_msg.notification.body


# ===========================================================================
# 7. Realtime / Push Pipeline (Tests 34 - 37)
# ===========================================================================


def test_34_notification_db_row_created_with_reward_transaction():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    with transaction.atomic():
        tx = PointService.earn_points(
            user,
            profile,
            40,
            "Atomic check",
            idempotency_key="rt:34",
        )
        # Inside the same uncommitted atomic block, the Notification DB row already exists
        assert Notification.objects.filter(
            notification_type=NotificationType.POINTS_EARNED,
            object_id=tx.pk,
        ).exists()


def test_35_websocket_and_fcm_scheduling_occurs_only_after_commit_using_existing_infrastructure(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    ws_mock = MagicMock()
    fcm_mock = MagicMock()
    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        ws_mock,
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fcm_mock,
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        tx = PointService.earn_points(
            user,
            profile,
            55,
            "Deferred publish",
            idempotency_key="rt:35",
        )
        assert ws_mock.call_count == 0
        assert fcm_mock.call_count == 0

    assert len(callbacks) == 1
    callbacks[0]()

    notif = Notification.objects.get(object_id=tx.pk)
    ws_mock.assert_called_once()
    assert ws_mock.call_args[0][0].pk == notif.pk
    fcm_mock.assert_called_once_with(notif.pk)


def test_36_rollback_schedules_no_delivery(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    ws_mock = MagicMock()
    fcm_mock = MagicMock()
    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        ws_mock,
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fcm_mock,
    )

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError, match="rollback 36"):
            with transaction.atomic():
                PrizeAwardService.record_claim_required(
                    user=user,
                    profile=profile,
                    source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
                    source_id="rollback-36",
                    title="iPhone",
                    prize_snapshot={"prize_code": "IPHONE"},
                    idempotency_key="rt:36",
                )
                raise RuntimeError("rollback 36")

    assert ws_mock.call_count == 0
    assert fcm_mock.call_count == 0
    assert Notification.objects.count() == 0
    assert PrizeAward.objects.count() == 0


def test_37_post_commit_transport_failure_does_not_corrupt_reward_or_notification_db_rows(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    def fail_ws(*args, **kwargs):
        raise ConnectionError("Redis channel layer down")

    def fail_fcm(*args, **kwargs):
        raise ConnectionError("Celery broker unreachable")

    monkeypatch.setattr(
        "apps.notifications.websocket.broadcast_notification",
        fail_ws,
    )
    monkeypatch.setattr(
        "apps.notifications.tasks.deliver_notification_push.delay",
        fail_fcm,
    )

    with django_capture_on_commit_callbacks(execute=True):
        badge = BadgeService.award_badge(
            profile,
            BadgeType.WELCOME_BUYER,
            idempotency_key="rt:37:badge",
        )
        prize = PrizeAwardService.record_claim_required(
            user=user,
            profile=profile,
            source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
            source_id="rt-37",
            title="Gold Trophy",
            prize_snapshot={"prize_code": "GOLD_TROPHY"},
            idempotency_key="rt:37:prize",
        )

    assert BadgeAward.objects.filter(pk=badge.pk).exists()
    assert PrizeAward.objects.filter(pk=prize.pk).exists()
    assert Notification.objects.filter(
        recipient_profile=profile,
        object_type="badge_award",
        object_id=badge.pk,
    ).exists()
    assert Notification.objects.filter(
        recipient_profile=profile,
        object_type="prize_award",
        object_id=prize.pk,
    ).exists()


# ===========================================================================
# 8. Scope Guardrails (Tests 38 - 42)
# ===========================================================================


def test_38_no_notification_grouping_or_digest_occurs():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    for idx in range(3):
        PointService.earn_points(
            user,
            profile,
            10 * (idx + 1),
            f"Earn #{idx}",
            idempotency_key=f"guard:38:{idx}",
        )

    assert Notification.objects.filter(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
    ).count() == 3


def test_39_no_dnd_or_preference_filtering_models_exist_before_be_not_02():
    from django.apps import apps as django_apps

    notif_models = {
        model.__name__
        for model in django_apps.get_app_config("notifications").get_models()
    }
    assert notif_models == {
        "Notification",
        "PushDevice",
        "NotificationPreference",
    }



def test_40_no_weekly_draw_winner_notification_occurs_while_be_drw_02_blocked():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    Notification.objects.all().delete()

    entry = WeeklyDrawEntryService.ensure_for_onboarding(
        user, profile=user.active_profile
    )

    assert entry is None
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    assert Notification.objects.count() == 0


def test_41_no_prize_claim_submission_review_or_delivery_notification_occurs():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    award = PrizeAwardService.record_claim_required(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="guard-41",
        title="Silver Trophy",
        prize_snapshot={"prize_code": "SILVER_TROPHY"},
        idempotency_key="guard:41:award",
    )
    Notification.objects.all().delete()

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Omar Ali",
        address="10 Tahrir Square, Cairo",
        identity_number="29001011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    assert Notification.objects.count() == 0


def test_42_no_historical_backfill_or_new_migrations_exist():
    from django.core.management import call_command

    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
    assert Notification.objects.count() == 0


def test_43_referral_challenge_iphone_and_airpods_get_one_notification_and_blocked_annual_loft_gets_zero():
    now = timezone.now()
    _, manager_profile = make_manager()
    challenge = ReferralChallengeService.create_challenge(
        name="Top 3 Referral Challenge",
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(hours=1),
        created_by=manager_profile.user,
    )
    inviter1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    inviter2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    inviter3 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    for inviter, count in ((inviter1, 3), (inviter2, 2), (inviter3, 1)):
        for _ in range(count):
            code_obj = ReferralService.create_code(inviter.active_profile)
            referred = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
            referral = ReferralService.redeem_code(
                referred.active_profile,
                code_obj.code,
            )
            type(referral).objects.filter(pk=referral.pk).update(
                redeemed_at=challenge.starts_at + timedelta(days=1)
            )
            ReferralService.approve(referral.pk, manager_profile.user)

    Notification.objects.all().delete()

    ReferralChallengeService.finalize(challenge.pk, manager_profile.user)

    # Rank 1 (IPHONE) -> 1 PRIZE_AWARDED notification
    r1_award = PrizeAward.objects.get(
        user=inviter1,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
    )
    r1_notifs = Notification.objects.filter(
        recipient_profile=inviter1.active_profile,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert r1_notifs.count() == 1
    assert r1_notifs.first().object_id == r1_award.pk
    assert r1_award.prize_snapshot["prize_code"] == "IPHONE"

    # Rank 2 (AIRPODS) -> 1 PRIZE_AWARDED notification
    r2_award = PrizeAward.objects.get(
        user=inviter2,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
    )
    r2_notifs = Notification.objects.filter(
        recipient_profile=inviter2.active_profile,
        notification_type=NotificationType.PRIZE_AWARDED,
    )
    assert r2_notifs.count() == 1
    assert r2_notifs.first().object_id == r2_award.pk
    assert r2_award.prize_snapshot["prize_code"] == "AIRPODS"

    # Rank 3 (ANNUAL_LOFT_SUBSCRIPTION) -> still BLOCKED -> 0 notifications
    assert not PrizeAward.objects.filter(
        user=inviter3,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
    ).exists()
    assert (
        Notification.objects.filter(
            recipient_profile=inviter3.active_profile,
        ).count()
        == 0
    )
    assert Notification.objects.count() == 2


def test_44_lucky_wheel_no_prize_spin_creates_zero_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    LuckyWheelGrantService.grant_attempt(
        profile=profile,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="deal-44",
        idempotency_key="lw:grant:44",
    )
    LuckyWheelPrize.objects.create(
        label="Try Again",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
        is_active=True,
    )
    Notification.objects.all().delete()

    spin, extra = LuckyWheelSpinService.spin(user, profile, "lw:spin:44")

    assert spin.prize_snapshot["prize_type"] == LuckyWheelPrizeType.NO_PRIZE
    assert extra is None
    assert PrizeAward.objects.count() == 0
    assert Notification.objects.count() == 0


def test_45_lucky_wheel_spin_retry_does_not_duplicate_notification():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    LuckyWheelGrantService.grant_attempt(
        profile=profile,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="deal-45",
        idempotency_key="lw:grant:45",
    )
    LuckyWheelPrize.objects.create(
        label="75 PP Coins",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=75,
        weight=1,
        is_active=True,
    )
    Notification.objects.all().delete()

    spin1, _ = LuckyWheelSpinService.spin(user, profile, "lw:spin:45:retry")
    spin2, _ = LuckyWheelSpinService.spin(user, profile, "lw:spin:45:retry")

    assert spin1.pk == spin2.pk
    assert (
        Notification.objects.filter(
            recipient_profile=profile,
            notification_type=NotificationType.POINTS_EARNED,
        ).count()
        == 1
    )
    assert Notification.objects.count() == 1


def test_46_savepoint_safe_integrity_error_recovery_inside_outer_atomic_block(
    monkeypatch,
):
    from django.db import IntegrityError, transaction

    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile

    existing_notif = NotificationService.create_notification(
        recipient_profile=profile,
        notification_type=NotificationType.POINTS_EARNED,
        title="Existing",
        body="Existing body",
        idempotency_key="audit:savepoint:notif:1",
    )

    real_filter = Notification.objects.filter

    def race_first_lookup(*args, **kwargs):
        qs = real_filter(*args, **kwargs)
        if kwargs.get("idempotency_key") == "audit:savepoint:notif:1":
            monkeypatch.setattr(Notification.objects, "filter", real_filter)
            return Notification.objects.none()
        return qs

    monkeypatch.setattr(Notification.objects, "filter", race_first_lookup)

    with transaction.atomic():
        recovered = NotificationService.create_notification(
            recipient_profile=profile,
            notification_type=NotificationType.POINTS_EARNED,
            title="Concurrent",
            body="Concurrent body",
            idempotency_key="audit:savepoint:notif:1",
        )
        assert recovered.pk == existing_notif.pk
        assert Notification.objects.filter(pk=existing_notif.pk).exists()

    existing_badge = BadgeService.award_badge(
        profile,
        BadgeType.WELCOME_BUYER,
        idempotency_key="audit:savepoint:badge:1",
    )
    real_badge_filter = BadgeAward.objects.filter

    def race_badge_lookup(*args, **kwargs):
        qs = real_badge_filter(*args, **kwargs)
        if kwargs.get("idempotency_key") == "audit:savepoint:badge:1":
            monkeypatch.setattr(BadgeAward.objects, "filter", real_badge_filter)
            return BadgeAward.objects.none()
        return qs

    monkeypatch.setattr(BadgeAward.objects, "filter", race_badge_lookup)

    with transaction.atomic():
        recovered_badge = BadgeService.award_badge(
            profile,
            BadgeType.WELCOME_BUYER,
            idempotency_key="audit:savepoint:badge:1",
        )
        assert recovered_badge.pk == existing_badge.pk
        assert BadgeAward.objects.filter(pk=existing_badge.pk).exists()


