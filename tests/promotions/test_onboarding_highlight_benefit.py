"""BE-PRO-03: Onboarding 20% first auction-highlight discount tests (test_01..test_56)."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.loft_subscriptions.models import PedigreeTemplatePackage, SubscriptionDurationOption
from apps.loft_subscriptions.services import (
    SubscriptionCheckoutService,
    SubscriptionPricingService,
)
from apps.loyalty.constants import RewardEventType, RewardSourceType
from apps.loyalty.models import PointTransaction, PointWallet
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import PointService
from apps.notifications.models import Notification
from apps.packages.models import Package
from apps.packages.services import SellerPackageService
from apps.payments.models import PaymentRequest
from apps.promotions.models import (
    CashbackTransaction,
    OfferTriggerType,
    PromotionCampaign,
    PromotionCampaignType,
    PromotionEffectType,
    PromotionTargetType,
    UserPromotionGrant,
    UserPromotionGrantStatus,
    UserPromotionGrantType,
)
from apps.promotions.services import (
    ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT,
    OnboardingHighlightBenefitService,
    OnboardingHighlightDiscountSource,
    PromotionCampaignEffectService,
    PromotionCampaignService,
    PromotionPricingService,
)
from apps.referrals.models import LinkSharingCode, ReferralStatus
from apps.referrals.services import ReferralService
from apps.seasons.models import Season, SeasonStatus
from apps.seasons.services import SeasonScoreService, SeasonService
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    CustomerProfileFactory,
    PackageFactory,
    SellerProfileFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db

REGISTER_CUSTOMER_URL = "/api/auth/register/customer/"
SELLER_PROFILE_URL = "/api/users/mobile/sellers/"
CUSTOMER_PROFILE_URL = "/api/users/mobile/customers/"
HIGHLIGHT_BENEFIT_URL = "/api/promotions/onboarding/highlight-benefit/"


def authenticated_client(user=None):
    client = APIClient()
    if user is not None:
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def register_customer(username="onboard_pro03", phone_number="+201009990001"):
    payload = customer_registration_payload(phone_number=phone_number)
    payload["username"] = username
    response = authenticated_client().post(
        REGISTER_CUSTOMER_URL,
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    return UserAccount.objects.get(username=username)


def create_seller_mode(user):
    response = authenticated_client(user).post(
        SELLER_PROFILE_URL,
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
            "recoded_voice": "voice-key",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    return SellerProfile.objects.get(user=user)


def create_customer_mode(user):
    response = authenticated_client(user).post(
        CUSTOMER_PROFILE_URL,
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    return CustomerProfile.objects.get(user=user)


def onboarding_highlight_grants(user):
    return UserPromotionGrant.objects.filter(
        user=user,
        idempotency_key=OnboardingHighlightBenefitService.idempotency_key(user),
    )


def create_highlight_campaign_effect(
    *,
    discount_percent: Decimal,
    starts_at=None,
    ends_at=None,
    is_enabled: bool = True,
    campaign_type: str = PromotionCampaignType.FLASH,
    target_code: str = "",
    now=None,
):
    eval_now = now or timezone.now()
    create_now = eval_now - timedelta(days=10)
    c_start = starts_at if starts_at is not None else eval_now - timedelta(hours=2)
    c_end = ends_at if ends_at is not None else eval_now + timedelta(hours=2)
    if create_now >= c_start:
        create_now = c_start - timedelta(days=1)

    campaign = PromotionCampaignService.create(
        name=f"Highlight {discount_percent}%",
        campaign_type=campaign_type,
        starts_at=c_start,
        ends_at=c_end,
        is_enabled=is_enabled,
    )
    effect = PromotionCampaignEffectService.create(
        campaign,
        effect_type=PromotionEffectType.PERCENT_DISCOUNT,
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        target_code=target_code,
        discount_percent=discount_percent,
        now=create_now,
    )
    return campaign, effect


# ============================================================================
# ONBOARDING GRANT (1..8)
# ============================================================================


def test_01_new_customer_registration_creates_one_benefit():
    user = register_customer(username="cust_pro03_01", phone_number="+201009990011")
    customer_profile = CustomerProfile.objects.get(user=user)

    grants = onboarding_highlight_grants(user)
    assert grants.count() == 1
    grant = grants.get()
    assert grant.user_id == user.pk
    assert grant.grant_type == UserPromotionGrantType.DISCOUNT
    assert grant.source_type == OfferTriggerType.ONBOARDING
    assert grant.source_id == str(user.pk)
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.redeemed_at is None
    assert grant.consumed_at is None
    assert grant.is_available is True
    assert grant.is_consumed is False
    assert grant.discount_percent == Decimal("20.00")
    assert grant.idempotency_key == f"promotion:onboarding_highlight:user:{user.pk}"
    assert grant.metadata["onboarding_profile_id"] == customer_profile.pk
    assert grant.metadata["onboarding_profile_type"] == ProfileType.CUSTOMER


def test_02_adding_seller_profile_later_keeps_one_benefit():
    user = register_customer(username="cust_pro03_02", phone_number="+201009990012")
    customer_profile = CustomerProfile.objects.get(user=user)
    first_grant = onboarding_highlight_grants(user).get()

    create_seller_mode(user)

    grants = onboarding_highlight_grants(user)
    assert grants.count() == 1
    second_grant = grants.get()
    assert second_grant.pk == first_grant.pk
    assert second_grant.metadata["onboarding_profile_id"] == customer_profile.pk


def test_03_seller_first_onboarding_creates_one_benefit():
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_profile = user.active_profile

    grant = OnboardingHighlightBenefitService.ensure_for_onboarding(
        user=user,
        profile=seller_profile,
    )

    assert onboarding_highlight_grants(user).count() == 1
    assert grant.user_id == user.pk
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.discount_percent == Decimal("20.00")
    assert grant.metadata["onboarding_profile_id"] == seller_profile.pk
    assert grant.metadata["onboarding_profile_type"] == ProfileType.SELLER


def test_04_adding_customer_profile_later_keeps_one_benefit():
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_profile = user.active_profile
    first_grant = OnboardingHighlightBenefitService.ensure_for_onboarding(
        user=user,
        profile=seller_profile,
    )

    customer_profile = create_customer_mode(user)

    grants = onboarding_highlight_grants(user)
    assert grants.count() == 1
    second_grant = grants.get()
    assert second_grant.pk == first_grant.pk
    assert second_grant.metadata["onboarding_profile_id"] == seller_profile.pk
    assert customer_profile.user_id == user.pk


def test_05_repeated_onboarding_event_creates_one_benefit():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    seller_profile = SellerProfileFactory(user=user, is_active=False)

    g1 = OnboardingHighlightBenefitService.ensure_for_onboarding(user, customer_profile)
    g2 = OnboardingHighlightBenefitService.ensure_for_onboarding(user, customer_profile)
    g3 = OnboardingHighlightBenefitService.ensure_for_onboarding(user, seller_profile)

    assert g1.pk == g2.pk == g3.pk
    assert onboarding_highlight_grants(user).count() == 1


def test_06_concurrent_or_duplicate_ensure_creates_one_benefit():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    profile = user.active_profile
    first = OnboardingHighlightBenefitService.ensure_for_onboarding(user, profile)

    # Simulate race condition where filter().first() misses and create() raises IntegrityError
    with patch.object(
        UserPromotionGrant.objects,
        "create",
        side_effect=IntegrityError("duplicate idempotency_key"),
    ):
        with patch(
            "apps.promotions.services.UserPromotionGrant.objects.select_for_update"
        ) as mock_sfu:
            mock_sfu.return_value.select_related.return_value.filter.return_value.first.return_value = (
                None
            )
            recovered = OnboardingHighlightBenefitService.ensure_for_onboarding(
                user, profile
            )

    assert recovered.pk == first.pk
    assert onboarding_highlight_grants(user).count() == 1


def test_07_exact_decimal_20_00_discount_percent():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    grant = OnboardingHighlightBenefitService.ensure_for_onboarding(
        user, user.active_profile
    )
    status_data = OnboardingHighlightBenefitService.get_benefit_status(user)

    assert ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT == Decimal("20.00")
    assert isinstance(grant.discount_percent, Decimal)
    assert grant.discount_percent == Decimal("20.00")
    assert isinstance(status_data["discount_percent"], Decimal)
    assert status_data["discount_percent"] == Decimal("20.00")


def test_08_benefit_grant_creates_zero_point_transactions():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    grant = OnboardingHighlightBenefitService.ensure_for_onboarding(
        user, user.active_profile
    )

    assert grant.pk is not None
    assert PointTransaction.objects.filter(user=user).count() == 0
    assert PointWallet.objects.filter(user=user).count() == 0


# ============================================================================
# LEGACY (9)
# ============================================================================


def test_09_pre_existing_account_receives_no_accidental_backfill():
    legacy_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    legacy_seller = UserAccountFactory(with_profile=ProfileType.SELLER)

    assert OnboardingHighlightBenefitService.get_benefit(legacy_customer) is None
    assert OnboardingHighlightBenefitService.is_available(legacy_customer) is False
    assert OnboardingHighlightBenefitService.get_benefit(legacy_seller) is None
    assert OnboardingHighlightBenefitService.is_available(legacy_seller) is False

    quote = OnboardingHighlightBenefitService.quote(
        user=legacy_seller,
        base_amount=Decimal("100.00"),
    )
    assert quote.discount_source == OnboardingHighlightDiscountSource.NONE
    assert quote.discount_percent == Decimal("0.00")
    assert quote.final_amount == Decimal("100.00")
    assert quote.onboarding_available is False
    assert quote.should_consume_on_commit is False

    response = authenticated_client(legacy_seller).get(HIGHLIGHT_BENEFIT_URL)
    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "available": False,
        "discount_percent": "20.00",
        "consumed_at": None,
    }
    assert UserPromotionGrant.objects.filter(user=legacy_seller).count() == 0


# ============================================================================
# ELIGIBILITY (10..12)
# ============================================================================


def test_10_available_benefit_reports_true():
    user = register_customer(username="cust_pro03_10", phone_number="+201009990020")

    assert OnboardingHighlightBenefitService.is_available(user) is True
    response = authenticated_client(user).get(HIGHLIGHT_BENEFIT_URL)
    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "available": True,
        "discount_percent": "20.00",
        "consumed_at": None,
    }


def test_11_consumed_benefit_reports_false():
    user = register_customer(username="cust_pro03_11", phone_number="+201009990021")
    now = timezone.now()

    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert OnboardingHighlightBenefitService.is_available(user, now=now) is False
    response = authenticated_client(user).get(HIGHLIGHT_BENEFIT_URL)
    assert response.status_code == status.HTTP_200_OK
    assert response.data["available"] is False
    assert response.data["discount_percent"] == "20.00"
    assert response.data["consumed_at"] is not None


def test_12_profile_switching_sees_same_state():
    user = register_customer(username="cust_pro03_12", phone_number="+201009990022")
    seller_profile = create_seller_mode(user)
    seller_profile.activated = True
    seller_profile.save(update_fields=["activated"])

    # In Customer mode
    cust_res = authenticated_client(user).get(HIGHLIGHT_BENEFIT_URL)
    assert cust_res.status_code == status.HTTP_200_OK
    assert cust_res.data["available"] is True

    # Switch to Seller mode
    switched, role = switch_user_profile(user, ProfileType.SELLER)
    assert switched is True
    assert role == ProfileType.SELLER
    user.refresh_from_db()

    seller_res = authenticated_client(user).get(HIGHLIGHT_BENEFIT_URL)
    assert seller_res.status_code == status.HTTP_200_OK
    assert seller_res.data == cust_res.data

    # Consume while in Seller mode
    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("150.00"),
        profile=seller_profile,
    )

    seller_after = authenticated_client(user).get(HIGHLIGHT_BENEFIT_URL)
    assert seller_after.data["available"] is False
    assert seller_after.data["consumed_at"] is not None

    # Switch back to Customer mode: must NOT reset benefit
    switched_back, _ = switch_user_profile(user, ProfileType.CUSTOMER)
    assert switched_back is True
    user.refresh_from_db()

    cust_after = authenticated_client(user).get(HIGHLIGHT_BENEFIT_URL)
    assert cust_after.data == seller_after.data


# ============================================================================
# QUOTE (13..16)
# ============================================================================


def test_13_quote_100_base_gives_20_discount_and_80_final():
    user = register_customer(username="cust_pro03_13", phone_number="+201009990023")
    grant = OnboardingHighlightBenefitService.get_benefit(user)

    result = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
    )

    assert result.base_amount == Decimal("100.00")
    assert result.discount_percent == Decimal("20.00")
    assert result.discount_amount == Decimal("20.00")
    assert result.final_amount == Decimal("80.00")
    assert (
        result.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert result.campaign_id is None
    assert result.effect_id is None
    assert result.onboarding_benefit_id == grant.pk
    assert result.onboarding_available is True
    assert result.should_consume_on_commit is True


def test_14_quote_does_not_consume():
    user = register_customer(username="cust_pro03_14", phone_number="+201009990024")

    OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
    )

    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.redeemed_at is None
    assert OnboardingHighlightBenefitService.is_available(user) is True


def test_15_repeated_quote_does_not_consume():
    user = register_customer(username="cust_pro03_15", phone_number="+201009990025")

    for _ in range(5):
        res = OnboardingHighlightBenefitService.quote(
            user=user,
            base_amount=Decimal("250.00"),
        )
        assert res.discount_percent == Decimal("20.00")
        assert res.discount_amount == Decimal("50.00")
        assert res.final_amount == Decimal("200.00")
        assert (
            res.discount_source
            == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
        )

    assert OnboardingHighlightBenefitService.is_available(user) is True


def test_16_zero_price_highlight_does_not_consume_or_qualify():
    user = register_customer(username="cust_pro03_16", phone_number="+201009990026")

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("0.00"),
    )

    assert res.base_amount == Decimal("0.00")
    assert res.discount_percent == Decimal("0.00")
    assert res.discount_amount == Decimal("0.00")
    assert res.final_amount == Decimal("0.00")
    assert res.discount_source == OnboardingHighlightDiscountSource.NONE
    assert res.onboarding_available is True
    assert res.should_consume_on_commit is False
    assert OnboardingHighlightBenefitService.is_available(user) is True


# ============================================================================
# CAMPAIGN COMPARISON (17..28)
# ============================================================================


def test_17_onboarding_20_vs_campaign_10_onboarding_wins():
    now = timezone.now()
    user = register_customer(username="cust_pro03_17", phone_number="+201009990027")
    create_highlight_campaign_effect(discount_percent=Decimal("10.00"), now=now)

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert res.discount_percent == Decimal("20.00")
    assert res.discount_amount == Decimal("20.00")
    assert res.final_amount == Decimal("80.00")
    assert (
        res.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert res.campaign_id is None
    assert res.effect_id is None
    assert res.should_consume_on_commit is True


def test_18_committed_case_onboarding_20_vs_campaign_10_consumes_benefit():
    now = timezone.now()
    user = register_customer(username="cust_pro03_18", phone_number="+201009990028")
    create_highlight_campaign_effect(discount_percent=Decimal("10.00"), now=now)

    res = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert (
        res.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert res.final_amount == Decimal("80.00")
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is False
    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.REDEEMED
    assert grant.redeemed_at == now


def test_19_onboarding_20_vs_campaign_30_campaign_wins():
    now = timezone.now()
    user = register_customer(username="cust_pro03_19", phone_number="+201009990029")
    campaign, effect = create_highlight_campaign_effect(
        discount_percent=Decimal("30.00"),
        now=now,
    )

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert res.discount_percent == Decimal("30.00")
    assert res.discount_amount == Decimal("30.00")
    assert res.final_amount == Decimal("70.00")
    assert res.discount_source == OnboardingHighlightDiscountSource.CAMPAIGN
    assert res.campaign_id == campaign.pk
    assert res.effect_id == effect.pk
    assert res.onboarding_available is True
    assert res.should_consume_on_commit is False


def test_20_campaign_winning_case_leaves_benefit_available():
    now = timezone.now()
    user = register_customer(username="cust_pro03_20", phone_number="+201009990030")
    campaign, effect = create_highlight_campaign_effect(
        discount_percent=Decimal("30.00"),
        now=now,
    )

    committed = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert committed.discount_source == OnboardingHighlightDiscountSource.CAMPAIGN
    assert committed.discount_percent == Decimal("30.00")
    assert committed.final_amount == Decimal("70.00")
    assert committed.campaign_id == campaign.pk
    assert committed.effect_id == effect.pk
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is True
    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.redeemed_at is None

    # After the 30% campaign ends, the 20% onboarding benefit is still available!
    after_campaign = now + timedelta(hours=5)
    next_quote = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=after_campaign,
    )
    assert (
        next_quote.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert next_quote.discount_percent == Decimal("20.00")


def test_21_onboarding_20_vs_campaign_20_onboarding_wins_deterministic_tie():
    now = timezone.now()
    user = register_customer(username="cust_pro03_21", phone_number="+201009990031")
    create_highlight_campaign_effect(discount_percent=Decimal("20.00"), now=now)

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert (
        res.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert res.discount_percent == Decimal("20.00")
    assert res.discount_amount == Decimal("20.00")
    assert res.final_amount == Decimal("80.00")
    assert res.campaign_id is None
    assert res.effect_id is None
    assert res.should_consume_on_commit is True


def test_22_tie_quote_alone_does_not_consume():
    now = timezone.now()
    user = register_customer(username="cust_pro03_22", phone_number="+201009990032")
    create_highlight_campaign_effect(discount_percent=Decimal("20.00"), now=now)

    OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert OnboardingHighlightBenefitService.is_available(user, now=now) is True
    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.redeemed_at is None


def test_23_tie_successful_commit_consumes():
    now = timezone.now()
    user = register_customer(username="cust_pro03_23", phone_number="+201009990033")
    campaign, effect = create_highlight_campaign_effect(
        discount_percent=Decimal("20.00"),
        now=now,
    )

    committed = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert (
        committed.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is False

    # Subsequent quote during the same 20% campaign now resolves to CAMPAIGN
    follow_up = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )
    assert follow_up.discount_source == OnboardingHighlightDiscountSource.CAMPAIGN
    assert follow_up.campaign_id == campaign.pk
    assert follow_up.effect_id == effect.pk


def test_24_campaign_100_wins_and_onboarding_remains_available():
    now = timezone.now()
    user = register_customer(username="cust_pro03_24", phone_number="+201009990034")
    campaign, effect = create_highlight_campaign_effect(
        discount_percent=Decimal("100.00"),
        now=now,
    )

    committed = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert committed.discount_source == OnboardingHighlightDiscountSource.CAMPAIGN
    assert committed.discount_percent == Decimal("100.00")
    assert committed.discount_amount == Decimal("100.00")
    assert committed.final_amount == Decimal("0.00")
    assert committed.campaign_id == campaign.pk
    assert committed.effect_id == effect.pk
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is True


def test_25_discounts_never_stack():
    now = timezone.now()
    user = register_customer(username="cust_pro03_25", phone_number="+201009990035")
    create_highlight_campaign_effect(
        discount_percent=Decimal("15.00"),
        campaign_type=PromotionCampaignType.MONTHLY,
        now=now,
    )
    create_highlight_campaign_effect(
        discount_percent=Decimal("30.00"),
        campaign_type=PromotionCampaignType.FLASH,
        now=now,
    )

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    # Single best discount is 30% (never 20+30=50%, never 20+15+30=65%, never compounded)
    assert res.discount_percent == Decimal("30.00")
    assert res.discount_amount == Decimal("30.00")
    assert res.final_amount == Decimal("70.00")
    assert res.discount_source == OnboardingHighlightDiscountSource.CAMPAIGN


def test_26_disabled_campaign_ignored():
    now = timezone.now()
    user = register_customer(username="cust_pro03_26", phone_number="+201009990036")
    create_highlight_campaign_effect(
        discount_percent=Decimal("50.00"),
        is_enabled=False,
        now=now,
    )

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert (
        res.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert res.discount_percent == Decimal("20.00")
    assert res.final_amount == Decimal("80.00")


def test_27_ended_campaign_ignored():
    now = timezone.now()
    user = register_customer(username="cust_pro03_27", phone_number="+201009990037")
    create_highlight_campaign_effect(
        discount_percent=Decimal("50.00"),
        starts_at=now - timedelta(hours=5),
        ends_at=now - timedelta(hours=1),
        now=now,
    )

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert (
        res.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert res.discount_percent == Decimal("20.00")


def test_28_scheduled_campaign_ignored():
    now = timezone.now()
    user = register_customer(username="cust_pro03_28", phone_number="+201009990038")
    create_highlight_campaign_effect(
        discount_percent=Decimal("50.00"),
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(hours=5),
        now=now,
    )

    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )

    assert (
        res.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert res.discount_percent == Decimal("20.00")


# ============================================================================
# CONSUMPTION (29..35)
# ============================================================================


def test_29_successful_paid_onboarding_winning_commit_consumes_once():
    now = timezone.now()
    user = register_customer(username="cust_pro03_29", phone_number="+201009990039")
    seller_profile = create_seller_mode(user)

    committed = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("200.00"),
        target_code="SPOTLIGHT_7D",
        profile=seller_profile,
        source_id="auction-highlight-101",
        now=now,
    )

    assert (
        committed.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert committed.discount_percent == Decimal("20.00")
    assert committed.discount_amount == Decimal("40.00")
    assert committed.final_amount == Decimal("160.00")

    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.REDEEMED
    assert grant.redeemed_at == now
    assert grant.consumed_at == now
    assert grant.profile_id == seller_profile.pk
    assert grant.metadata["consumed_by_profile_id"] == seller_profile.pk
    assert grant.metadata["consumed_source_id"] == "auction-highlight-101"
    assert grant.metadata["consumed_target_code"] == "SPOTLIGHT_7D"


def test_30_second_qualifying_highlight_receives_no_onboarding_candidate():
    now = timezone.now()
    user = register_customer(username="cust_pro03_30", phone_number="+201009990040")

    first = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )
    assert (
        first.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )

    second_quote = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now + timedelta(minutes=5),
    )
    assert second_quote.discount_source == OnboardingHighlightDiscountSource.NONE
    assert second_quote.discount_percent == Decimal("0.00")
    assert second_quote.discount_amount == Decimal("0.00")
    assert second_quote.final_amount == Decimal("100.00")
    assert second_quote.onboarding_available is False
    assert second_quote.should_consume_on_commit is False


def test_31_different_seller_profile_cannot_consume_again():
    now = timezone.now()
    user = register_customer(username="cust_pro03_31", phone_number="+201009990041")
    customer_profile = CustomerProfile.objects.get(user=user)
    seller_profile = create_seller_mode(user)

    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        profile=customer_profile,
        now=now,
    )

    second = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        profile=seller_profile,
        now=now + timedelta(minutes=1),
    )
    assert second.discount_source == OnboardingHighlightDiscountSource.NONE
    assert second.final_amount == Decimal("100.00")

    other_seller = SellerProfileFactory()
    with pytest.raises(ValidationError):
        OnboardingHighlightBenefitService.consume_if_selected(
            user=user,
            base_amount=Decimal("100.00"),
            profile=other_seller,
            now=now,
        )


def test_32_different_auction_cannot_consume_again():
    now = timezone.now()
    user = register_customer(username="cust_pro03_32", phone_number="+201009990042")

    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        source_id="auction-1",
        target_code="BOOST_A",
        now=now,
    )

    second = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("150.00"),
        source_id="auction-2",
        target_code="BOOST_B",
        now=now + timedelta(minutes=10),
    )
    assert second.discount_source == OnboardingHighlightDiscountSource.NONE
    assert second.discount_percent == Decimal("0.00")
    assert second.final_amount == Decimal("150.00")


def test_33_failed_commit_rolls_back_consumption():
    now = timezone.now()
    user = register_customer(username="cust_pro03_33", phone_number="+201009990043")

    def fail_activation(_quote):
        raise RuntimeError("Downstream highlight activation failed")

    with pytest.raises(RuntimeError, match="Downstream highlight activation failed"):
        OnboardingHighlightBenefitService.consume_if_selected(
            user=user,
            base_amount=Decimal("100.00"),
            now=now,
            on_commit_callback=fail_activation,
        )

    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.redeemed_at is None
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is True


def test_34_cancelled_or_uncommitted_attempt_does_not_consume():
    now = timezone.now()
    user = register_customer(username="cust_pro03_34", phone_number="+201009990044")

    quote = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )
    assert quote.should_consume_on_commit is True
    # Caller cancels before calling consume_if_selected
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is True
    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.redeemed_at is None


def test_35_free_highlight_does_not_consume():
    now = timezone.now()
    user = register_customer(username="cust_pro03_35", phone_number="+201009990045")

    res = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("0.00"),
        now=now,
    )

    assert res.discount_source == OnboardingHighlightDiscountSource.NONE
    assert res.discount_percent == Decimal("0.00")
    assert res.final_amount == Decimal("0.00")
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is True
    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.ACTIVE
    assert grant.redeemed_at is None


# ============================================================================
# CONCURRENCY (36..38)
# ============================================================================


def test_36_two_stale_simultaneous_evaluations_only_one_final_consumption():
    now = timezone.now()
    user = register_customer(username="cust_pro03_36", phone_number="+201009990046")

    # Both concurrent attempts initially quote 20%
    stale_quote_a = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )
    stale_quote_b = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("100.00"),
        now=now,
    )
    assert (
        stale_quote_a.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert (
        stale_quote_b.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )

    # First commit acquires lock and consumes the benefit
    commit_a = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        source_id="attempt-a",
        now=now,
    )
    # Second commit re-evaluates under lock and sees the benefit is already REDEEMED
    commit_b = OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        source_id="attempt-b",
        now=now + timedelta(seconds=1),
    )

    assert (
        commit_a.discount_source
        == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
    )
    assert commit_a.final_amount == Decimal("80.00")
    assert commit_b.discount_source == OnboardingHighlightDiscountSource.NONE
    assert commit_b.final_amount == Decimal("100.00")

    grant = OnboardingHighlightBenefitService.get_benefit(user)
    assert grant.status == UserPromotionGrantStatus.REDEEMED
    assert grant.redeemed_at == now
    assert grant.metadata["consumed_source_id"] == "attempt-a"

    # Strict caller requiring onboarding discount fails cleanly on stale second commit
    with pytest.raises(ValidationError):
        OnboardingHighlightBenefitService.consume_if_selected(
            user=user,
            base_amount=Decimal("100.00"),
            require_onboarding_discount=True,
            now=now + timedelta(seconds=2),
        )


def test_37_select_for_update_path_tested():
    now = timezone.now()
    user = register_customer(username="cust_pro03_37", phone_number="+201009990047")

    with patch.object(
        UserPromotionGrant.objects,
        "select_for_update",
        wraps=UserPromotionGrant.objects.select_for_update,
    ) as spy_grant_sfu, patch.object(
        UserAccount.objects,
        "select_for_update",
        wraps=UserAccount.objects.select_for_update,
    ) as spy_user_sfu:
        OnboardingHighlightBenefitService.consume_if_selected(
            user=user,
            base_amount=Decimal("100.00"),
            now=now,
        )
        assert spy_user_sfu.called
        assert spy_grant_sfu.called


def test_38_db_uniqueness_protects_one_account_grant():
    user = register_customer(username="cust_pro03_38", phone_number="+201009990048")
    existing = OnboardingHighlightBenefitService.get_benefit(user)

    with pytest.raises((IntegrityError, Exception)):
        with transaction.atomic():
            UserPromotionGrant.objects.create(
                user=user,
                profile=None,
                grant_type=UserPromotionGrantType.DISCOUNT,
                discount_offer=existing.discount_offer,
                source_type=OfferTriggerType.ONBOARDING,
                source_id=str(user.pk),
                status=UserPromotionGrantStatus.ACTIVE,
                idempotency_key=existing.idempotency_key,
            )


# ============================================================================
# MONEY (39..40)
# ============================================================================


def test_39_round_half_up_reused():
    user = register_customer(username="cust_pro03_39", phone_number="+201009990049")

    # 33.33 * 20% = 6.666 -> 6.67 ROUND_HALF_UP -> final 26.66
    res = OnboardingHighlightBenefitService.quote(
        user=user,
        base_amount=Decimal("33.33"),
    )
    assert res.discount_amount == Decimal("6.67")
    assert res.final_amount == Decimal("26.66")

    # Float base_amount is rejected
    with pytest.raises(ValidationError):
        OnboardingHighlightBenefitService.quote(
            user=user,
            base_amount=33.33,
        )


def test_40_final_amount_never_negative():
    now = timezone.now()
    user = register_customer(username="cust_pro03_40", phone_number="+201009990050")
    create_highlight_campaign_effect(discount_percent=Decimal("100.00"), now=now)

    for amount in (Decimal("0.00"), Decimal("0.01"), Decimal("0.03"), Decimal("99.99")):
        res = OnboardingHighlightBenefitService.quote(
            user=user,
            base_amount=amount,
            now=now,
        )
        assert res.final_amount >= Decimal("0.00")
        assert res.discount_amount <= res.base_amount


# ============================================================================
# SECURITY (41..44)
# ============================================================================


def test_41_client_cannot_edit_available_or_consumed_status():
    user = register_customer(username="cust_pro03_41", phone_number="+201009990051")
    client = authenticated_client(user)

    patch_res = client.patch(
        HIGHLIGHT_BENEFIT_URL,
        {"available": False},
        format="json",
    )
    put_res = client.put(
        HIGHLIGHT_BENEFIT_URL,
        {"available": False},
        format="json",
    )
    assert patch_res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert put_res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert OnboardingHighlightBenefitService.is_available(user) is True


def test_42_client_cannot_consume_via_public_endpoint():
    user = register_customer(username="cust_pro03_42", phone_number="+201009990052")
    client = authenticated_client(user)

    post_res = client.post(
        HIGHLIGHT_BENEFIT_URL,
        {"base_amount": "100.00"},
        format="json",
    )
    delete_res = client.delete(HIGHLIGHT_BENEFIT_URL)
    consume_res = client.post(
        "/api/promotions/onboarding/highlight-benefit/consume/",
        {"base_amount": "100.00"},
        format="json",
    )

    assert post_res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert delete_res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert consume_res.status_code == status.HTTP_404_NOT_FOUND
    assert OnboardingHighlightBenefitService.is_available(user) is True


def test_43_client_cannot_query_another_users_benefit():
    user_a = register_customer(username="cust_pro03_43a", phone_number="+201009990053")
    user_b = UserAccountFactory(with_profile=ProfileType.CUSTOMER)  # No benefit

    # user_b tries to pass user_a's id in query params
    res = authenticated_client(user_b).get(
        HIGHLIGHT_BENEFIT_URL,
        {"user": user_a.pk, "user_id": user_a.pk},
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data["available"] is False
    assert "idempotency_key" not in res.data
    assert "metadata" not in res.data

    # Unauthenticated client is rejected
    unauth_res = APIClient().get(HIGHLIGHT_BENEFIT_URL)
    assert unauth_res.status_code == status.HTTP_401_UNAUTHORIZED


def test_44_arbitrary_client_base_price_is_not_trusted_by_public_api():
    user = register_customer(username="cust_pro03_44", phone_number="+201009990054")
    client = authenticated_client(user)

    quote_res = client.post(
        "/api/promotions/onboarding/highlight-benefit/quote/",
        {"base_amount": "0.01"},
        format="json",
    )
    assert quote_res.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# SIDE EFFECTS (45..49)
# ============================================================================


def test_45_grant_creates_zero_pp():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    OnboardingHighlightBenefitService.ensure_for_onboarding(user, user.active_profile)

    assert PointTransaction.objects.filter(user=user).count() == 0
    assert PointService.get_balance(user) == 0


def test_46_quote_creates_zero_pp():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    OnboardingHighlightBenefitService.ensure_for_onboarding(user, user.active_profile)

    OnboardingHighlightBenefitService.quote(user=user, base_amount=Decimal("100.00"))

    assert PointTransaction.objects.filter(user=user).count() == 0
    assert PointService.get_balance(user) == 0


def test_47_consume_creates_zero_pp():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    OnboardingHighlightBenefitService.ensure_for_onboarding(user, user.active_profile)

    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
    )

    assert PointTransaction.objects.filter(user=user).count() == 0
    assert PointService.get_balance(user) == 0


def test_48_zero_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    before = Notification.objects.count()

    OnboardingHighlightBenefitService.ensure_for_onboarding(user, user.active_profile)
    OnboardingHighlightBenefitService.quote(user=user, base_amount=Decimal("100.00"))
    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
    )

    assert Notification.objects.count() == before


def test_49_no_gateway_or_payment_provider_objects():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    payments_before = PaymentRequest.objects.count()
    cashback_before = CashbackTransaction.objects.count()

    OnboardingHighlightBenefitService.ensure_for_onboarding(user, user.active_profile)
    OnboardingHighlightBenefitService.quote(user=user, base_amount=Decimal("100.00"))
    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
    )

    assert PaymentRequest.objects.count() == payments_before
    assert CashbackTransaction.objects.count() == cashback_before


# ============================================================================
# REGRESSION (50..56)
# ============================================================================


def test_50_regression_be_pro_02_package_discounts_unchanged():
    now = timezone.now()
    seller_user = register_customer(
        username="cust_pro03_50", phone_number="+201009990060"
    )
    seller_profile = create_seller_mode(seller_user)
    package = PackageFactory(price=Decimal("200.00"))
    campaign = PromotionCampaignService.create(
        name="Package 25% Off",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=5),
        is_enabled=True,
    )
    PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("25.00"),
        now=now - timedelta(hours=2),
    )

    seller_pkg = SellerPackageService.request_package(seller_profile, package.pk)

    assert seller_pkg.base_price == Decimal("200.00")
    assert seller_pkg.discount_percent == Decimal("25.00")
    assert seller_pkg.discount_amount == Decimal("50.00")
    assert seller_pkg.final_price == Decimal("150.00")
    # Onboarding highlight benefit is untouched by package purchase
    assert OnboardingHighlightBenefitService.is_available(seller_user, now=now) is True


def test_51_regression_be_pro_02_loft_discounts_unchanged():
    now = timezone.now()
    user = register_customer(username="cust_pro03_51", phone_number="+201009990061")
    duration = SubscriptionDurationOption.objects.create(
        code="ANNUAL_PRO03",
        name="Annual",
        duration_days=365,
        price=Decimal("300.00"),
        is_active=True,
    )
    campaign = PromotionCampaignService.create(
        name="Loft 15% Off",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=5),
        is_enabled=True,
    )
    PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        target_subscription_duration=duration,
        discount_percent=Decimal("15.00"),
        now=now - timedelta(hours=2),
    )

    pedigree_pkg = PedigreeTemplatePackage.objects.create(
        code="PED_PRO03",
        name="Basic Pedigree",
        template_limit=2,
        price=Decimal("100.00"),
        is_active=True,
    )
    selection = {
        "duration_id": duration.pk,
        "pedigree_package_id": pedigree_pkg.pk,
        "addon_ids": [],
        "ai_package_id": None,
        "template_ids": [1, 2],
    }
    pricing = SubscriptionPricingService.calculate(
        profile=user.active_profile,
        validated_selection=selection,
    )
    assert pricing.subtotal == Decimal("400.00")
    assert pricing.discount_amount == Decimal("60.00")
    assert pricing.total == Decimal("340.00")
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is True


def test_52_regression_be_pro_02_auction_highlight_campaign_resolver_unchanged():
    now = timezone.now()
    campaign, effect = create_highlight_campaign_effect(
        discount_percent=Decimal("35.00"),
        target_code="FEATURED_HOME",
        now=now,
    )

    resolution = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        base_price=Decimal("100.00"),
        target_code="FEATURED_HOME",
        now=now,
    )
    assert resolution.campaign_id == campaign.pk
    assert resolution.effect_id == effect.pk
    assert resolution.discount_percent == Decimal("35.00")
    assert resolution.discount_amount == Decimal("35.00")
    assert resolution.final_price == Decimal("65.00")


def test_53_regression_be_pro_01_scheduling_unchanged():
    now = timezone.now()
    active_campaign, _ = create_highlight_campaign_effect(
        discount_percent=Decimal("10.00"),
        now=now,
    )
    assert list(PromotionCampaignService.active_campaigns(now=now)) == [active_campaign]


def test_54_regression_be_sea_01_to_03_unchanged():
    now = timezone.now()
    season = Season.objects.create(
        title="PRO03 Regression Season",
        status=SeasonStatus.DRAFT,
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
    )
    snapshots = SeasonScoreService.calculate_season_scores(season)
    assert snapshots == []
    finalized = SeasonService.finalize_season(season)
    assert finalized.status == SeasonStatus.FINALIZED


def test_55_regression_be_ref_01_to_03_unchanged():
    inviter = CustomerProfileFactory(is_active=True)
    redeemer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    redeemer = redeemer_user.active_profile
    code = LinkSharingCode.objects.create(
        owner_profile=inviter,
        code="PRO03REF",
        expires_at=timezone.now() + timedelta(hours=2),
    )

    referral = ReferralService.redeem_code(redeemer, code.code)
    assert referral.status == ReferralStatus.PENDING


def test_56_regression_be_rew_02_to_10_unchanged():
    user = register_customer(username="cust_pro03_56", phone_number="+201009990066")
    create_seller_mode(user)

    assert PointService.get_balance(user) == 100
    assert (
        PointTransaction.objects.filter(
            user=user,
            source_type=RewardSourceType.ONBOARDING,
        ).count()
        == 1
    )
    assert onboarding_highlight_grants(user).count() == 1


# ============================================================================
# BE-PRO-03A — NO-BACKFILL & DISCOUNT_PERCENT HARDENING (57..72)
# ============================================================================


def test_57_pro03a_fresh_customer_registration_gets_one_benefit():
    user = register_customer(
        username="cust_pro03a_57", phone_number="+201009990070"
    )
    grants = onboarding_highlight_grants(user)
    assert grants.count() == 1
    grant = grants.get()
    assert grant.is_onboarding_highlight_benefit is True
    assert grant.discount_percent == Decimal("20.00")


def test_58_pro03a_fresh_seller_first_onboarding_gets_one_benefit():
    user = UserAccountFactory(with_profile=False)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        SELLER_PROFILE_URL,
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
            "recoded_voice": "voice-pro03a-58",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    seller_profile = SellerProfile.objects.get(user=user)

    grants = onboarding_highlight_grants(user)
    assert grants.count() == 1
    grant = grants.get()
    assert grant.metadata["onboarding_profile_id"] == seller_profile.pk
    assert grant.metadata["onboarding_profile_type"] == ProfileType.SELLER


def test_59_pro03a_fresh_customer_then_seller_remains_one_grant():
    user = register_customer(
        username="cust_pro03a_59", phone_number="+201009990071"
    )
    first_grant = onboarding_highlight_grants(user).get()

    seller_profile = create_seller_mode(user)

    assert onboarding_highlight_grants(user).count() == 1
    second_grant = onboarding_highlight_grants(user).get()
    assert second_grant.pk == first_grant.pk
    assert second_grant.metadata["onboarding_profile_type"] == ProfileType.CUSTOMER
    assert (
        OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
            user=user,
            profile=seller_profile,
        )
        is None
    )


def test_60_pro03a_fresh_seller_then_customer_remains_one_grant():
    user = UserAccountFactory(with_profile=False)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        SELLER_PROFILE_URL,
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
            "recoded_voice": "voice-pro03a-60",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    seller_profile = SellerProfile.objects.get(user=user)
    seller_profile.is_active = True
    seller_profile.activated = True
    seller_profile.save(update_fields=["is_active", "activated"])
    first_grant = onboarding_highlight_grants(user).get()

    customer_profile = create_customer_mode(user)

    assert onboarding_highlight_grants(user).count() == 1
    second_grant = onboarding_highlight_grants(user).get()
    assert second_grant.pk == first_grant.pk
    assert second_grant.metadata["onboarding_profile_id"] == seller_profile.pk
    assert (
        OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
            user=user,
            profile=customer_profile,
        )
        is None
    )


def test_61_pro03a_historical_customer_without_grant_later_creates_seller_gets_zero_grants():
    # Historical pre-BE-PRO-03 account with CustomerProfile and 0 grants
    legacy_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0

    seller_profile = create_seller_mode(legacy_user)

    assert seller_profile.user_id == legacy_user.pk
    assert onboarding_highlight_grants(legacy_user).count() == 0
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0
    assert OnboardingHighlightBenefitService.is_available(legacy_user) is False


def test_62_pro03a_historical_seller_without_grant_later_creates_customer_gets_zero_grants():
    # Historical pre-BE-PRO-03 account with SellerProfile and 0 grants
    legacy_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0

    customer_profile = create_customer_mode(legacy_user)

    assert customer_profile.user_id == legacy_user.pk
    assert onboarding_highlight_grants(legacy_user).count() == 0
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0
    assert OnboardingHighlightBenefitService.is_available(legacy_user) is False


def test_63_pro03a_historical_soft_deleted_customer_profile_later_creates_seller_gets_zero_grants():
    legacy_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    old_customer = CustomerProfile.objects.get(user=legacy_user)
    old_customer.is_active = False
    old_customer.is_valid = False
    old_customer.save(update_fields=["is_active", "is_valid"])
    old_customer.delete()
    assert CustomerProfile.objects.filter(user=legacy_user).count() == 0
    assert CustomerProfile.all_objects.filter(user=legacy_user).count() == 1
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0

    client = APIClient()
    client.force_authenticate(user=legacy_user)
    response = client.post(
        SELLER_PROFILE_URL,
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
            "recoded_voice": "voice-pro03a-63",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    new_seller = SellerProfile.objects.get(user=legacy_user)

    assert (
        OnboardingHighlightBenefitService.has_prior_onboarding_profile_history(
            legacy_user,
            current_profile=new_seller,
        )
        is True
    )
    assert onboarding_highlight_grants(legacy_user).count() == 0
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0


def test_64_pro03a_historical_soft_deleted_seller_profile_later_creates_customer_gets_zero_grants():
    legacy_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    old_seller = SellerProfile.objects.get(user=legacy_user)
    old_seller.is_active = False
    old_seller.is_valid = False
    old_seller.save(update_fields=["is_active", "is_valid"])
    old_seller.delete()
    assert SellerProfile.objects.filter(user=legacy_user).count() == 0
    assert SellerProfile.all_objects.filter(user=legacy_user).count() == 1
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0

    client = APIClient()
    client.force_authenticate(user=legacy_user)
    response = client.post(
        CUSTOMER_PROFILE_URL,
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    new_customer = CustomerProfile.objects.get(user=legacy_user)

    assert (
        OnboardingHighlightBenefitService.has_prior_onboarding_profile_history(
            legacy_user,
            current_profile=new_customer,
        )
        is True
    )
    assert onboarding_highlight_grants(legacy_user).count() == 0
    assert UserPromotionGrant.objects.filter(user=legacy_user).count() == 0


def test_65_pro03a_repeated_first_profile_onboarding_hook_is_idempotent():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile

    g1 = OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
        user=user,
        profile=customer_profile,
    )
    g2 = OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
        user=user,
        profile=customer_profile,
    )

    assert g1 is not None
    assert g2 is not None
    assert g1.pk == g2.pk
    assert onboarding_highlight_grants(user).count() == 1


def test_66_pro03a_direct_trusted_ensure_for_onboarding_remains_idempotent():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    seller_profile = SellerProfileFactory(user=user, is_active=False)

    g1 = OnboardingHighlightBenefitService.ensure_for_onboarding(
        user=user,
        profile=customer_profile,
    )
    g2 = OnboardingHighlightBenefitService.ensure_for_onboarding(
        user=user,
        profile=seller_profile,
    )

    assert g1.pk == g2.pk
    assert onboarding_highlight_grants(user).count() == 1


def test_67_pro03a_consumed_grant_cannot_be_reset_by_second_profile_creation():
    user = register_customer(
        username="cust_pro03a_67", phone_number="+201009990077"
    )
    now = timezone.now()
    OnboardingHighlightBenefitService.consume_if_selected(
        user=user,
        base_amount=Decimal("100.00"),
        source_id="hl-pro03a-67",
        now=now,
    )
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is False

    create_seller_mode(user)

    grant = onboarding_highlight_grants(user).get()
    assert grant.status == UserPromotionGrantStatus.REDEEMED
    assert grant.redeemed_at == now
    assert OnboardingHighlightBenefitService.is_available(user, now=now) is False


def test_68_pro03a_manager_excluded_from_first_profile_hook():
    manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager_profile = manager_user.active_profile

    with pytest.raises(ValidationError):
        OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
            user=manager_user,
            profile=manager_profile,
        )
    assert UserPromotionGrant.objects.filter(user=manager_user).count() == 0


def test_69_pro03a_100_pp_onboarding_remains_once_and_no_backfill_hook_creates_zero_point_transactions():
    legacy_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    # Award the 100 PP onboarding reward on the legacy customer as would have happened historically
    RewardEngine.grant_account_onboarding_reward(
        legacy_user, legacy_user.active_profile
    )
    tx_count_before = PointTransaction.objects.filter(user=legacy_user).count()
    assert tx_count_before == 1
    assert PointService.get_balance(legacy_user) == 100

    # Calling the automatic no-backfill hook directly creates 0 PointTransactions and 0 grants
    seller_profile = SellerProfileFactory(user=legacy_user, is_active=False)
    hook_result = (
        OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
            user=legacy_user,
            profile=seller_profile,
        )
    )

    assert hook_result is None
    assert onboarding_highlight_grants(legacy_user).count() == 0
    assert PointTransaction.objects.filter(user=legacy_user).count() == tx_count_before
    assert PointService.get_balance(legacy_user) == 100


def test_70_pro03a_generic_discount_percent_does_not_treat_other_onboarding_grants_as_20_percent():
    from apps.promotions.models import (
        CashbackOffer,
        DiscountOffer,
        OfferTargetType,
        OfferValueType,
    )

    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    # 1) Another onboarding percentage discount offer (e.g. 10%)
    other_pct_offer = DiscountOffer.objects.create(
        title="Other Onboarding 10% Offer",
        description="Hypothetical 10% onboarding offer",
        value_type=OfferValueType.PERCENTAGE,
        value=Decimal("10.00"),
        target_type=OfferTargetType.ALL,
        minimum_order_price=Decimal("0.00"),
        trigger_type=OfferTriggerType.ONBOARDING,
        is_active=False,
    )
    other_pct_grant = UserPromotionGrant.objects.create(
        user=user,
        grant_type=UserPromotionGrantType.DISCOUNT,
        discount_offer=other_pct_offer,
        source_type=OfferTriggerType.ONBOARDING,
        source_id=str(user.pk),
        idempotency_key=f"promotion:other_onboarding_pct:user:{user.pk}",
    )
    assert other_pct_grant.is_onboarding_highlight_benefit is False
    assert other_pct_grant.discount_percent == Decimal("10.00")

    # 2) An onboarding fixed-amount discount offer (e.g. 50.00 fixed)
    fixed_offer = DiscountOffer.objects.create(
        title="Other Onboarding Fixed 50 Offer",
        description="Hypothetical fixed-amount onboarding offer",
        value_type=OfferValueType.FIXED_AMOUNT,
        value=Decimal("50.00"),
        target_type=OfferTargetType.ALL,
        minimum_order_price=Decimal("0.00"),
        trigger_type=OfferTriggerType.ONBOARDING,
        is_active=False,
    )
    fixed_grant = UserPromotionGrant.objects.create(
        user=user,
        grant_type=UserPromotionGrantType.DISCOUNT,
        discount_offer=fixed_offer,
        source_type=OfferTriggerType.ONBOARDING,
        source_id=str(user.pk),
        idempotency_key=f"promotion:other_onboarding_fixed:user:{user.pk}",
    )
    assert fixed_grant.is_onboarding_highlight_benefit is False
    assert fixed_grant.discount_percent == Decimal("0.00")

    # 3) An onboarding cashback grant
    cb_offer = CashbackOffer.objects.create(
        title="Other Onboarding Cashback Offer",
        description="Hypothetical onboarding cashback offer",
        value_type=OfferValueType.PERCENTAGE,
        value=Decimal("15.00"),
        target_type=OfferTargetType.ALL,
        minimum_order_price=Decimal("0.00"),
        allowed_redemption_amount=Decimal("10.00"),
        trigger_type=OfferTriggerType.ONBOARDING,
        is_active=False,
    )
    cb_grant = UserPromotionGrant.objects.create(
        user=user,
        grant_type=UserPromotionGrantType.CASHBACK,
        cashback_offer=cb_offer,
        source_type=OfferTriggerType.ONBOARDING,
        source_id=str(user.pk),
        idempotency_key=f"promotion:other_onboarding_cb:user:{user.pk}",
    )
    assert cb_grant.is_onboarding_highlight_benefit is False
    assert cb_grant.discount_percent == Decimal("0.00")


def test_71_pro03a_real_onboarding_highlight_grant_reports_decimal_20_00():
    user = register_customer(
        username="cust_pro03a_71", phone_number="+201009990081"
    )
    grant = onboarding_highlight_grants(user).get()

    assert grant.is_onboarding_highlight_benefit is True
    assert isinstance(grant.discount_percent, Decimal)
    assert grant.discount_percent == Decimal("20.00")


def test_72_pro03a_soft_deleted_single_profile_does_not_qualify_for_automatic_first_profile_hook():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    deleted_profile = CustomerProfile.objects.get(user=user)
    deleted_profile.is_active = False
    deleted_profile.is_valid = False
    deleted_profile.save(update_fields=["is_active", "is_valid"])
    deleted_profile.delete()

    result = OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
        user=user,
        profile=None,
    )
    assert result is None
    assert onboarding_highlight_grants(user).count() == 0
