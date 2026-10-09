"""Tests verifying BE-DRW-CANCEL-01: Weekly Onboarding Draw cancellation invariants."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.deals.models import CompletedDeal, CompletedDealSourceType, CompletedDealType
from apps.draws.models import WeeklyDraw, WeeklyDrawEntry, WeeklyDrawStatus
from apps.draws.services import (
    WeeklyDrawEntryService,
    WeeklyDrawFinalizationService,
    WeeklyDrawService,
)
from apps.loyalty.constants import (
    BadgeType,
    RewardEventType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import PointService, RewardRuleService
from apps.lucky_wheel.models import (
    LuckyWheelGrant,
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
    LuckyWheelSpinStatus,
)
from apps.lucky_wheel.services import LuckyWheelGrantService, LuckyWheelSpinService
from apps.notifications.models import Notification, NotificationType
from apps.prizes.models import (
    PrizeAward,
    PrizeAwardFulfillmentType,
    PrizeAwardSourceType,
    PrizeAwardStatus,
)
from apps.promotions.services import OnboardingHighlightBenefitService
from apps.referrals.models import (
    Referral,
    ReferralChallenge,
    ReferralChallengeWinner,
    ReferralStatus,
)
from apps.referrals.services import ReferralChallengeService
from apps.seasons.models import Season, SeasonStatus
from apps.seasons.services import SeasonService
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.manager_permissions import (
    get_manager_permission_codes,
    get_manager_permission_groups,
    is_manager_permission_allowed,
)
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile
from tests.factories import (
    BirdFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db


def auth_client(user=None):
    client = APIClient()
    client.credentials(HTTP_ACCEPT_LANGUAGE="en")
    if user is not None:
        refresh = build_token_for_user(user)
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
            HTTP_ACCEPT_LANGUAGE="en",
        )
    return client


def make_superuser():
    user = UserAccountFactory(with_profile=False)
    user.is_superuser = True
    user.is_staff = True
    user.save(update_fields=["is_superuser", "is_staff"])
    ManagerProfile.objects.create(
        user=user,
        is_active=True,
        activated=True,
        is_valid=True,
        permissions=[],
    )
    return user


def test_01_new_user_account_onboarding_creates_100_pp_once():
    payload = customer_registration_payload(phone_number="+201091110001")
    payload["username"] = "cancel_draw_user_01"

    response = auth_client().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    user = UserAccount.objects.get(username="cancel_draw_user_01")
    assert PointService.get_balance(user) == 100
    assert (
        PointTransaction.objects.filter(
            user=user,
            source_type=RewardSourceType.ONBOARDING,
            points=100,
        ).count()
        == 1
    )


def test_02_onboarding_creates_no_weekly_draw_entry():
    payload = customer_registration_payload(phone_number="+201091110002")
    payload["username"] = "cancel_draw_user_02"

    response = auth_client().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    user = UserAccount.objects.get(username="cancel_draw_user_02")
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    assert WeeklyDrawEntry.all_objects.filter(user=user).count() == 0


def test_03_customer_profile_onboarding_creates_no_weekly_draw_entry():
    user = UserAccountFactory(with_profile=False)
    seller = SellerProfile.objects.create(
        user=user,
        nickname=user.username,
        country=CountryOptions.EGYPT,
        currency=CurrencyOptions.EGP,
        recoded_voice="voice.mp3",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    assert seller.pk is not None

    response = auth_client(user).post(
        "/api/users/mobile/customers/",
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert CustomerProfile.objects.filter(user=user).exists()
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_04_seller_profile_onboarding_creates_no_weekly_draw_entry():
    user = UserAccountFactory(with_profile=False)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        "/api/users/mobile/sellers/",
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
            "recoded_voice": "voice.mp3",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert SellerProfile.objects.filter(user=user).exists()
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_05_adding_second_mode_profile_creates_no_weekly_draw_entry():
    payload = customer_registration_payload(phone_number="+201091110005")
    payload["username"] = "cancel_draw_user_05"
    reg = auth_client().post("/api/auth/register/customer/", payload, format="json")
    assert reg.status_code == status.HTTP_201_CREATED

    user = UserAccount.objects.get(username="cancel_draw_user_05")
    seller_resp = auth_client(user).post(
        "/api/users/mobile/sellers/",
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
            "recoded_voice": "voice.mp3",
        },
        format="json",
    )
    assert seller_resp.status_code == status.HTTP_201_CREATED
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    assert PointService.get_balance(user) == 100
    assert (
        BadgeAward.objects.filter(
            profile__user=user,
            badge_type__in=[BadgeType.WELCOME_BUYER, BadgeType.WELCOME_SELLER],
        ).count()
        == 2
    )


def test_06_first_highlight_20_percent_onboarding_benefit_remains_unchanged():
    payload = customer_registration_payload(phone_number="+201091110006")
    payload["username"] = "cancel_draw_user_06"
    reg = auth_client().post("/api/auth/register/customer/", payload, format="json")
    assert reg.status_code == status.HTTP_201_CREATED

    user = UserAccount.objects.get(username="cancel_draw_user_06")
    benefit = OnboardingHighlightBenefitService.get_benefit(user)
    assert benefit is not None
    assert benefit.is_available is True
    assert benefit.is_consumed is False
    assert benefit.discount_percent == Decimal("20.00")

    quote = OnboardingHighlightBenefitService.quote(
        user=user, base_amount=Decimal("100.00")
    )
    assert quote.discount_percent == Decimal("20.00")
    assert quote.final_amount == Decimal("80.00")
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_07_and_08_onboarding_pp_notification_remains_unchanged_and_zero_draw_notifications():
    payload = customer_registration_payload(phone_number="+201091110007")
    payload["username"] = "cancel_draw_user_07"
    reg = auth_client().post("/api/auth/register/customer/", payload, format="json")
    assert reg.status_code == status.HTTP_201_CREATED

    user = UserAccount.objects.get(username="cancel_draw_user_07")
    customer = CustomerProfile.objects.get(user=user)
    notifs = Notification.objects.filter(recipient_profile=customer)
    assert (
        notifs.filter(notification_type=NotificationType.POINTS_EARNED).count()
        == 1
    )
    assert (
        notifs.filter(notification_type=NotificationType.BADGE_AWARDED).count()
        == 1
    )
    assert (
        notifs.filter(notification_type=NotificationType.PRIZE_AWARDED).count()
        == 0
    )
    assert notifs.count() == 2
    assert not notifs.filter(object_type__icontains="draw").exists()


def test_09_no_weekly_draw_prize_award_is_created():
    payload = customer_registration_payload(phone_number="+201091110009")
    payload["username"] = "cancel_draw_user_09"
    reg = auth_client().post("/api/auth/register/customer/", payload, format="json")
    assert reg.status_code == status.HTTP_201_CREATED

    user = UserAccount.objects.get(username="cancel_draw_user_09")
    assert PrizeAward.objects.filter(user=user).count() == 0
    assert "WEEKLY_DRAW" not in PrizeAwardSourceType.values


def test_10_lucky_wheel_remains_unchanged():
    customer = CustomerProfileFactory(is_active=True)
    LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="mgr-grant-10",
        attempts=1,
        idempotency_key="lw:cancel_draw:10",
    )
    LuckyWheelPrize.objects.create(
        label="250 PP Wheel",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=250,
        weight=10,
        is_active=True,
    )

    spin, _ = LuckyWheelSpinService.spin(
        customer.user, customer, "lw-spin-cancel-draw-10"
    )
    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert PointService.get_balance(customer.user) == 250
    assert (
        PrizeAward.objects.filter(
            user=customer.user,
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            status=PrizeAwardStatus.FULFILLED,
            fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
        ).count()
        == 1
    )
    assert WeeklyDrawEntry.objects.count() == 0


def test_11_referral_challenge_remains_unchanged():
    from apps.referrals.models import LinkSharingCode

    now = timezone.now()
    superuser = make_superuser()
    challenge = ReferralChallengeService.create_challenge(
        name="Monthly Challenge",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
        created_by=superuser,
    )
    inviter = CustomerProfileFactory(is_active=True)
    referred = CustomerProfileFactory(is_active=True)
    code = LinkSharingCode.objects.create(
        owner_profile=inviter,
        code="REF101",
        expires_at=now + timedelta(hours=1),
        is_used=True,
        used_by_profile=referred,
        used_at=now - timedelta(days=5),
    )
    Referral.objects.create(
        inviter_user=inviter.user,
        inviter_profile=inviter,
        referred_user=referred.user,
        referred_profile=referred,
        code=code,
        status=ReferralStatus.APPROVED,
        redeemed_at=now - timedelta(days=5),
        reviewed_at=now - timedelta(days=4),
        reviewed_by=superuser,
    )

    ReferralChallengeService.finalize(challenge.pk, superuser, now=now)
    assert ReferralChallengeWinner.objects.filter(challenge=challenge).count() == 1
    assert (
        PrizeAward.objects.filter(
            user=inviter.user,
            source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
            fulfillment_type=PrizeAwardFulfillmentType.CLAIM_REQUIRED,
            status=PrizeAwardStatus.PENDING,
        ).count()
        == 1
    )


def test_12_seller_seasons_remain_unchanged():
    now = timezone.now()
    season = Season.objects.create(
        title="Season Check",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
        status=SeasonStatus.DRAFT,
    )
    seller = SellerProfileFactory(is_active=True)
    buyer = CustomerProfileFactory(is_active=True)
    bird = BirdFactory(owner=seller)
    CompletedDeal.objects.create(
        deal_type=CompletedDealType.MARKET,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        source_id=91201,
        buyer_user=buyer.user,
        buyer_profile=buyer,
        seller_user=seller.user,
        seller_profile=seller,
        asset=bird,
        amount=Decimal("500.00"),
        currency="EGP",
        completed_at=now - timedelta(days=3),
    )

    finalized = SeasonService.finalize(season)
    assert finalized.status == SeasonStatus.FINALIZED
    assert PointService.get_balance(seller.user) == 100000
    assert (
        PrizeAward.objects.filter(
            user=seller.user,
            source_type=PrizeAwardSourceType.SEASON,
            status=PrizeAwardStatus.FULFILLED,
        ).count()
        == 1
    )


def test_13_onboarding_idempotency_remains_unchanged():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer = user.active_profile
    seller = SellerProfile.objects.create(
        user=user,
        nickname=user.username,
        country=CountryOptions.EGYPT,
        currency=CurrencyOptions.EGP,
        recoded_voice="voice.mp3",
        is_active=False,
        activated=True,
        is_valid=True,
    )

    tx1 = RewardEngine.grant_account_onboarding_reward(user, customer)
    tx2 = RewardEngine.grant_account_onboarding_reward(user, customer)
    tx3 = RewardEngine.grant_account_onboarding_reward(user, seller)

    assert tx1 is not None
    assert tx2.pk == tx1.pk
    assert tx3.pk == tx1.pk
    assert PointService.get_balance(user) == 100
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    assert RewardRuleService.get_points(RewardRuleCode.ACCOUNT_ONBOARDING) == 100
    assert RewardEventType.CUSTOMER_PROFILE_CREATED == "customer_profile_created"


def test_14_and_15_no_active_weekly_draw_user_or_admin_endpoint_remains():
    customer = CustomerProfileFactory(is_active=True)
    superuser = make_superuser()

    user_resp = auth_client(customer.user).get("/api/draws/my-weekly-entry/")
    assert user_resp.status_code == status.HTTP_404_NOT_FOUND

    for method, path in [
        ("get", "/api/draws/weekly/"),
        ("post", "/api/draws/weekly/"),
        ("get", "/api/draws/weekly/1/"),
        ("patch", "/api/draws/weekly/1/"),
        ("post", "/api/draws/weekly/1/open/"),
        ("post", "/api/draws/weekly/1/close/"),
        ("post", "/api/draws/weekly/1/finalize/"),
        ("get", "/api/draws/weekly/1/entries/"),
    ]:
        resp = getattr(auth_client(superuser), method)(path, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND, (method, path)


def test_16_openapi_no_longer_advertises_cancelled_weekly_draw_endpoints():
    superuser = make_superuser()
    response = auth_client(superuser).get("/api/schema/?format=json")
    assert response.status_code == status.HTTP_200_OK
    paths = response.data.get("paths", {})
    assert not any(path.startswith("/api/draws/") for path in paths)


def test_17_manager_permission_catalog_has_no_stale_weekly_draw_permissions():
    codes = get_manager_permission_codes()
    assert not any(code.startswith("draws.") for code in codes)
    for code in [
        "draws.weekly.list",
        "draws.weekly.retrieve",
        "draws.weekly.create",
        "draws.weekly.update",
        "draws.weekly.open",
        "draws.weekly.close",
        "draws.weekly.finalize",
        "draws.weekly.entries.list",
    ]:
        assert not is_manager_permission_allowed(code)

    group_keys = {group["key"] for group in get_manager_permission_groups()}
    assert "draws.weekly" not in group_keys


def test_18_migrations_are_consistent_and_retired_entry_service_is_safe_noop():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    autodetector = MigrationAutodetector(
        loader.project_state(),
        ProjectState.from_apps(django_apps),
    )
    changes = autodetector.changes(graph=loader.graph)
    assert "draws" not in changes

    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert WeeklyDrawEntryService.ensure_for_onboarding(user) is None
    assert (
        WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
            user, profile=user.active_profile
        )
        is None
    )
    assert WeeklyDrawEntry.objects.count() == 0
    assert (
        WeeklyDrawService.WINNER_SELECTION_STATUS_CANCELLED
        == "CANCELLED_BY_PRODUCT_DECISION"
    )
    assert WeeklyDrawFinalizationService is not None

    # Historical dormant table compatibility remains intact
    now = timezone.now()
    hist_draw = WeeklyDraw.objects.create(
        title="Historical Dormant Draw",
        starts_at=now - timedelta(days=14),
        ends_at=now - timedelta(days=7),
        status=WeeklyDrawStatus.CLOSED,
    )
    assert hist_draw.pk is not None
