from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone, translation
import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.loft_subscriptions.models import (
    PedigreeTemplatePackage,
    SubscriptionDurationOption,
    SubscriptionInvoice,
)
from apps.loft_subscriptions.services import (
    SubscriptionActivationService,
    SubscriptionCheckoutService,
    SubscriptionPricingService,
)
from apps.loyalty.constants import RewardRuleCode
from apps.loyalty.models import PointTransaction
from apps.loyalty.services import RewardRuleService
from apps.packages.models import Package, SellerPackage
from apps.packages.services import SellerPackageService
from apps.promotions.models import (
    CashbackOffer,
    DiscountOffer,
    OfferTargetType,
    OfferValueType,
    PromotionCampaign,
    PromotionCampaignEffect,
    PromotionCampaignType,
    PromotionEffectType,
    PromotionTargetType,
    SellerPackagePromotionSnapshot,
)
from apps.promotions.services import (
    DiscountOfferService,
    PromotionCampaignEffectService,
    PromotionCampaignService,
    PromotionEffectService,
    PromotionPricingService,
)
from apps.referrals.models import Referral
from apps.seasons.models import Season, SeasonStatus
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    BirdFactory,
    CustomerProfileFactory,
    DiscountOfferFactory,
    PackageFactory,
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
    profile = user.active_profile
    profile.permissions = list(permissions or [])
    profile.save(update_fields=["permissions"])
    return user


def results_of(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


def create_campaign_with_effect(
    *,
    name="Promo Campaign",
    campaign_type=PromotionCampaignType.MONTHLY,
    starts_at,
    ends_at,
    is_enabled=True,
    target_type=PromotionTargetType.SELLER_PACKAGE,
    discount_percent=Decimal("20.00"),
    target_package=None,
    target_subscription_duration=None,
    target_code="",
    created_at=None,
):
    campaign = PromotionCampaignService.create(
        name=name,
        campaign_type=campaign_type,
        starts_at=starts_at,
        ends_at=ends_at,
        is_enabled=is_enabled,
    )
    effect_now = created_at or (starts_at - timedelta(minutes=10))
    effect = PromotionCampaignEffectService.create(
        campaign,
        effect_type=PromotionEffectType.PERCENT_DISCOUNT,
        target_type=target_type,
        discount_percent=discount_percent,
        target_package=target_package,
        target_subscription_duration=target_subscription_duration,
        target_code=target_code,
        now=effect_now,
    )
    return campaign, effect


def ensure_paid_loft_catalog():
    duration, _ = SubscriptionDurationOption.objects.update_or_create(
        code="months_6",
        defaults={
            "name": "6 Months",
            "duration_days": None,
            "duration_months": 6,
            "price": Decimal("600.00"),
            "currency": "EGP",
            "is_trial": False,
            "is_active": True,
            "sort_order": 2,
        },
    )
    other_duration, _ = SubscriptionDurationOption.objects.update_or_create(
        code="months_12",
        defaults={
            "name": "12 Months",
            "duration_days": None,
            "duration_months": 12,
            "price": Decimal("1000.00"),
            "currency": "EGP",
            "is_trial": False,
            "is_active": True,
            "sort_order": 3,
        },
    )
    pedigree_pkg, _ = PedigreeTemplatePackage.objects.update_or_create(
        code="shapes_2",
        defaults={
            "name": "2 Templates",
            "template_limit": 2,
            "is_unlimited": False,
            "price": Decimal("200.00"),
            "currency": "EGP",
            "is_active": True,
            "sort_order": 2,
        },
    )
    return duration, other_duration, pedigree_pkg


# ==============================================================================
# 1-9: Effect definition & validation
# ==============================================================================


def test_01_create_seller_package_discount_effect_succeeds():
    now = timezone.now()
    package = PackageFactory(price=Decimal("250.00"))
    campaign = PromotionCampaignService.create(
        name="Monthly Seller Package Promo",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=30),
    )
    effect = PromotionCampaignEffectService.create(
        campaign,
        effect_type=PromotionEffectType.PERCENT_DISCOUNT,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("15.00"),
        description="15% off specific seller package",
    )
    assert effect.pk is not None
    assert effect.campaign_id == campaign.pk
    assert effect.effect_type == PromotionEffectType.PERCENT_DISCOUNT
    assert effect.target_type == PromotionTargetType.SELLER_PACKAGE
    assert effect.target_package_id == package.pk
    assert effect.discount_percent == Decimal("15.00")


def test_02_create_auction_highlight_discount_effect_succeeds():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Monthly Highlight Promo",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=30),
    )
    effect = PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        target_code="featured_7d",
        discount_percent=Decimal("25.00"),
    )
    assert effect.pk is not None
    assert effect.target_type == PromotionTargetType.AUCTION_HIGHLIGHT
    assert effect.target_code == "featured_7d"
    assert effect.discount_percent == Decimal("25.00")


def test_03_create_loft_subscription_discount_effect_succeeds():
    now = timezone.now()
    duration, _, _ = ensure_paid_loft_catalog()
    campaign = PromotionCampaignService.create(
        name="Seasonal Loft Promo",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=60),
    )
    effect = PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        target_subscription_duration=duration,
        discount_percent=Decimal("30.00"),
    )
    assert effect.pk is not None
    assert effect.target_type == PromotionTargetType.LOFT_SUBSCRIPTION
    assert effect.target_subscription_duration_id == duration.pk
    assert effect.target_code == duration.code
    assert effect.discount_percent == Decimal("30.00")


def test_04_invalid_effect_type_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Invalid Effect Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=10),
    )
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignEffectService.create(
            campaign,
            effect_type="POINTS_MULTIPLIER",
            target_type=PromotionTargetType.SELLER_PACKAGE,
            discount_percent=Decimal("20.00"),
        )
    assert "effect_type" in exc_info.value.detail

    with pytest.raises(DjangoValidationError):
        PromotionCampaignEffect.objects.create(
            campaign=campaign,
            effect_type="CASH_DISCOUNT",
            target_type=PromotionTargetType.SELLER_PACKAGE,
            discount_percent=Decimal("20.00"),
        )


def test_05_invalid_target_type_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Invalid Target Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=10),
    )
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignEffectService.create(
            campaign,
            target_type="MARKET_PRODUCT",
            discount_percent=Decimal("20.00"),
        )
    assert "target_type" in exc_info.value.detail


def test_06_zero_or_negative_discount_percent_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Bounds Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=10),
    )
    for bad_pct in (Decimal("0.00"), Decimal("-5.00")):
        with pytest.raises(ValidationError) as exc_info:
            PromotionCampaignEffectService.create(
                campaign,
                target_type=PromotionTargetType.SELLER_PACKAGE,
                discount_percent=bad_pct,
            )
        assert "discount_percent" in exc_info.value.detail


def test_07_discount_percent_over_100_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Over 100 Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=10),
    )
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignEffectService.create(
            campaign,
            target_type=PromotionTargetType.SELLER_PACKAGE,
            discount_percent=Decimal("100.01"),
        )
    assert "discount_percent" in exc_info.value.detail


def test_08_float_discount_percent_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Float Rejection Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(hours=5),
    )
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignEffectService.create(
            campaign,
            target_type=PromotionTargetType.SELLER_PACKAGE,
            discount_percent=15.5,
        )
    assert "discount_percent" in exc_info.value.detail

    with pytest.raises(DjangoValidationError):
        PromotionCampaignEffect.objects.create(
            campaign=campaign,
            target_type=PromotionTargetType.SELLER_PACKAGE,
            discount_percent=20.0,
        )


def test_09_duplicate_effect_for_same_campaign_and_target_rejected():
    now = timezone.now()
    package = PackageFactory(price=Decimal("100.00"))
    campaign = PromotionCampaignService.create(
        name="No Duplicate Target Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=15),
    )
    PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("10.00"),
    )
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignEffectService.create(
            campaign,
            target_type=PromotionTargetType.SELLER_PACKAGE,
            target_package=package,
            discount_percent=Decimal("20.00"),
        )
    assert "target_type" in exc_info.value.detail


# ==============================================================================
# 10-15: Active window integration
# ==============================================================================


def test_10_scheduled_campaign_effect_does_not_apply():
    now = timezone.now()
    package = PackageFactory(price=Decimal("200.00"))
    create_campaign_with_effect(
        name="Scheduled Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=26),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("25.00"),
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        base_price=package.price,
        now=now,
    )
    assert res.has_discount is False
    assert res.discount_percent == Decimal("0.00")
    assert res.discount_amount == Decimal("0.00")
    assert res.final_price == Decimal("200.00")
    assert res.campaign is None
    assert res.effect is None


def test_11_active_campaign_effect_applies():
    now = timezone.now()
    package = PackageFactory(price=Decimal("200.00"))
    campaign, effect = create_campaign_with_effect(
        name="Active Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=23),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("25.00"),
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        base_price=package.price,
        now=now,
    )
    assert res.has_discount is True
    assert res.discount_percent == Decimal("25.00")
    assert res.discount_amount == Decimal("50.00")
    assert res.final_price == Decimal("150.00")
    assert res.campaign_id == campaign.pk
    assert res.effect_id == effect.pk


def test_12_ended_campaign_effect_does_not_apply():
    now = timezone.now()
    package = PackageFactory(price=Decimal("200.00"))
    create_campaign_with_effect(
        name="Ended Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=5),
        ends_at=now - timedelta(hours=1),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("30.00"),
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        base_price=package.price,
        now=now,
    )
    assert res.has_discount is False
    assert res.final_price == Decimal("200.00")


def test_13_disabled_campaign_effect_does_not_apply():
    now = timezone.now()
    package = PackageFactory(price=Decimal("200.00"))
    create_campaign_with_effect(
        name="Disabled Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=5),
        is_enabled=False,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("40.00"),
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        base_price=package.price,
        now=now,
    )
    assert res.has_discount is False
    assert res.final_price == Decimal("200.00")


def test_14_starts_at_inclusive_applies_effect():
    now = timezone.now()
    starts_at = now + timedelta(hours=1)
    ends_at = starts_at + timedelta(hours=4)
    campaign, effect = create_campaign_with_effect(
        name="Boundary Start Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=starts_at,
        ends_at=ends_at,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("20.00"),
        created_at=now,
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        base_price=Decimal("100.00"),
        now=starts_at,
    )
    assert res.has_discount is True
    assert res.campaign_id == campaign.pk
    assert res.effect_id == effect.pk
    assert res.final_price == Decimal("80.00")


def test_15_ends_at_exclusive_stops_effect():
    now = timezone.now()
    starts_at = now + timedelta(hours=1)
    ends_at = starts_at + timedelta(hours=4)
    create_campaign_with_effect(
        name="Boundary End Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=starts_at,
        ends_at=ends_at,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("20.00"),
        created_at=now,
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        base_price=Decimal("100.00"),
        now=ends_at,
    )
    assert res.has_discount is False
    assert res.final_price == Decimal("100.00")


# ==============================================================================
# 16-19: Campaign family behavior
# ==============================================================================


def test_16_monthly_campaign_applies_seller_package_discount():
    now = timezone.now()
    package = PackageFactory(price=Decimal("500.00"))
    campaign, effect = create_campaign_with_effect(
        name="Monthly Package Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=28),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("15.00"),
    )
    res = SellerPackageService.quote_package(package, now=now)
    assert res.campaign_id == campaign.pk
    assert res.effect_id == effect.pk
    assert res.discount_percent == Decimal("15.00")
    assert res.discount_amount == Decimal("75.00")
    assert res.final_price == Decimal("425.00")


def test_17_monthly_campaign_applies_auction_highlight_discount():
    now = timezone.now()
    campaign, effect = create_campaign_with_effect(
        name="Monthly Highlight Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=29),
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        target_code="homepage_spotlight",
        discount_percent=Decimal("20.00"),
    )
    res = PromotionEffectService.resolve_service_discount(
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        target_code="homepage_spotlight",
        base_price=Decimal("150.00"),
        now=now,
    )
    assert res.campaign_id == campaign.pk
    assert res.effect_id == effect.pk
    assert res.discount_amount == Decimal("30.00")
    assert res.final_price == Decimal("120.00")


def test_18_seasonal_campaign_applies_loft_subscription_discount():
    now = timezone.now()
    duration, _, pedigree_pkg = ensure_paid_loft_catalog()
    customer = CustomerProfileFactory()
    campaign, effect = create_campaign_with_effect(
        name="Seasonal Loft Campaign",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(days=3),
        ends_at=now + timedelta(days=60),
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        target_subscription_duration=duration,
        discount_percent=Decimal("25.00"),
    )
    pricing = SubscriptionPricingService.calculate(
        profile=customer,
        validated_selection={
            "duration_id": duration.pk,
            "pedigree_package_id": pedigree_pkg.pk,
            "addon_ids": [],
            "ai_package_id": None,
            "template_ids": [1, 2],
        },
        now=now,
    )
    assert pricing.subtotal == Decimal("800.00")
    assert pricing.discount_percent == Decimal("25.00")
    assert pricing.discount_amount == Decimal("200.00")
    assert pricing.total == Decimal("600.00")
    assert pricing.promotion_campaign_id == campaign.pk
    assert pricing.promotion_effect_id == effect.pk


def test_19_flash_campaign_applies_discount_during_window():
    now = timezone.now()
    package = PackageFactory(price=Decimal("300.00"))
    starts_at = now + timedelta(hours=1)
    ends_at = starts_at + timedelta(hours=3)
    campaign, effect = create_campaign_with_effect(
        name="3-Hour Flash Deal",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=starts_at,
        ends_at=ends_at,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("50.00"),
        created_at=now,
    )
    before = SellerPackageService.quote_package(
        package, now=starts_at - timedelta(seconds=1)
    )
    during = SellerPackageService.quote_package(
        package, now=starts_at + timedelta(hours=1)
    )
    after = SellerPackageService.quote_package(package, now=ends_at)

    assert before.final_price == Decimal("300.00")
    assert during.final_price == Decimal("150.00")
    assert during.campaign_id == campaign.pk
    assert during.effect_id == effect.pk
    assert after.final_price == Decimal("300.00")


# ==============================================================================
# 20-26: Conflict resolution / non-stacking
# ==============================================================================


def test_20_overlapping_campaigns_use_highest_discount_only():
    now = timezone.now()
    package = PackageFactory(price=Decimal("100.00"))
    create_campaign_with_effect(
        name="Monthly 15%",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=25),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("15.00"),
    )
    flash_campaign, flash_effect = create_campaign_with_effect(
        name="Flash 25%",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=5),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("25.00"),
    )
    res = SellerPackageService.quote_package(package, now=now)
    assert res.discount_percent == Decimal("25.00")
    assert res.discount_amount == Decimal("25.00")
    assert res.final_price == Decimal("75.00")
    assert res.campaign_id == flash_campaign.pk
    assert res.effect_id == flash_effect.pk


def test_21_overlapping_campaigns_never_stack_discounts():
    now = timezone.now()
    package = PackageFactory(price=Decimal("100.00"))
    create_campaign_with_effect(
        name="Campaign A 15%",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=10),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("15.00"),
    )
    create_campaign_with_effect(
        name="Campaign B 25%",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("25.00"),
    )
    res = SellerPackageService.quote_package(package, now=now)
    # Neither additive (40% -> 60.00) nor multiplicative (100 * 0.85 * 0.75 = 63.75)
    assert res.discount_percent == Decimal("25.00")
    assert res.final_price == Decimal("75.00")
    assert res.final_price != Decimal("60.00")
    assert res.final_price != Decimal("63.75")


def test_22_equal_discount_tie_breaks_by_earlier_starts_at():
    now = timezone.now()
    package = PackageFactory(price=Decimal("100.00"))
    earlier_campaign, earlier_effect = create_campaign_with_effect(
        name="Earlier Start 20%",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=3),
        ends_at=now + timedelta(days=10),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("20.00"),
    )
    create_campaign_with_effect(
        name="Later Start 20%",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=2),
        ends_at=now + timedelta(hours=10),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("20.00"),
    )
    res = SellerPackageService.quote_package(package, now=now)
    assert res.campaign_id == earlier_campaign.pk
    assert res.effect_id == earlier_effect.pk


def test_23_equal_discount_and_starts_at_tie_breaks_by_lower_campaign_id():
    now = timezone.now()
    same_start = now - timedelta(hours=3)
    same_end = now + timedelta(hours=12)
    c1, e1 = create_campaign_with_effect(
        name="Campaign 1",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=same_start,
        ends_at=same_end,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("20.00"),
    )
    c2, _ = create_campaign_with_effect(
        name="Campaign 2",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=same_start,
        ends_at=same_end,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("20.00"),
    )
    assert c1.pk < c2.pk
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        base_price=Decimal("100.00"),
        now=now,
    )
    assert res.campaign_id == c1.pk
    assert res.effect_id == e1.pk


def test_24_equal_campaign_tie_breaks_by_lower_effect_id():
    now = timezone.now()
    package = PackageFactory(price=Decimal("100.00"))
    starts_at = now - timedelta(hours=1)
    ends_at = now + timedelta(hours=5)
    campaign = PromotionCampaignService.create(
        name="Single Campaign Tie Break",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    before_start = starts_at - timedelta(minutes=5)
    e_global = PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=None,
        discount_percent=Decimal("20.00"),
        now=before_start,
    )
    e_specific = PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("20.00"),
        now=before_start,
    )
    assert e_global.pk < e_specific.pk
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        base_price=package.price,
        now=now,
    )
    assert res.effect_id == e_global.pk


def test_25_specific_target_and_all_target_effects_resolve_highest_non_stacking():
    now = timezone.now()
    package = PackageFactory(price=Decimal("200.00"))
    starts_at = now - timedelta(hours=1)
    ends_at = now + timedelta(hours=5)
    campaign = PromotionCampaignService.create(
        name="Global + Specific Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    before_start = starts_at - timedelta(minutes=5)
    PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=None,
        discount_percent=Decimal("10.00"),
        now=before_start,
    )
    e_specific = PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("30.00"),
        now=before_start,
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        base_price=package.price,
        now=now,
    )
    assert res.effect_id == e_specific.pk
    assert res.discount_percent == Decimal("30.00")
    assert res.final_price == Decimal("140.00")


def test_26_unmatched_specific_target_effect_does_not_apply_to_other_target():
    now = timezone.now()
    package_a = PackageFactory(price=Decimal("100.00"))
    package_b = PackageFactory(price=Decimal("100.00"))
    create_campaign_with_effect(
        name="Targeted Package A Only",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=10),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package_a,
        discount_percent=Decimal("35.00"),
    )
    res_b = SellerPackageService.quote_package(package_b, now=now)
    assert res_b.has_discount is False
    assert res_b.final_price == Decimal("100.00")


# ==============================================================================
# 27-29: Immutability
# ==============================================================================


def test_27_scheduled_campaign_allows_effect_create_update_delete():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Scheduled Mutable Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=20),
    )
    effect = PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("10.00"),
    )
    updated = PromotionCampaignEffectService.update(
        effect,
        discount_percent=Decimal("25.00"),
        description="Updated before start",
    )
    assert updated.discount_percent == Decimal("25.00")
    assert updated.description == "Updated before start"

    PromotionCampaignEffectService.delete(updated)
    assert list(PromotionCampaignEffectService.list_for_campaign(campaign)) == []


def test_28_active_campaign_blocks_effect_create_update_delete():
    now = timezone.now()
    starts_at = now - timedelta(hours=1)
    ends_at = now + timedelta(hours=5)
    campaign, effect = create_campaign_with_effect(
        name="Active Frozen Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=starts_at,
        ends_at=ends_at,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("15.00"),
    )

    with pytest.raises(ValidationError) as create_exc:
        PromotionCampaignEffectService.create(
            campaign,
            target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
            discount_percent=Decimal("20.00"),
            now=now,
        )
    assert "campaign" in create_exc.value.detail

    with pytest.raises(ValidationError) as update_exc:
        PromotionCampaignEffectService.update(
            effect,
            discount_percent=Decimal("30.00"),
            now=now,
        )
    assert "campaign" in update_exc.value.detail

    with pytest.raises(ValidationError) as delete_exc:
        PromotionCampaignEffectService.delete(effect, now=now)
    assert "campaign" in delete_exc.value.detail


def test_29_ended_campaign_blocks_effect_create_update_delete():
    now = timezone.now()
    starts_at = now - timedelta(days=5)
    ends_at = now - timedelta(days=1)
    campaign, effect = create_campaign_with_effect(
        name="Ended Frozen Campaign",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=starts_at,
        ends_at=ends_at,
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        discount_percent=Decimal("15.00"),
    )

    with pytest.raises(ValidationError):
        PromotionCampaignEffectService.create(
            campaign,
            target_type=PromotionTargetType.SELLER_PACKAGE,
            discount_percent=Decimal("20.00"),
            now=now,
        )
    with pytest.raises(ValidationError):
        PromotionCampaignEffectService.update(
            effect,
            discount_percent=Decimal("30.00"),
            now=now,
        )
    with pytest.raises(ValidationError):
        PromotionCampaignEffectService.delete(effect, now=now)


# ==============================================================================
# 30-36: Purchase / snapshot safety
# ==============================================================================


def test_30_seller_package_purchase_persists_discounted_price_snapshot():
    now = timezone.now()
    seller = SellerProfileFactory()
    package = PackageFactory(price=Decimal("400.00"))
    campaign, effect = create_campaign_with_effect(
        name="Seller Package Purchase Promo",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(hours=2),
        ends_at=now + timedelta(hours=20),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("20.00"),
    )

    seller_package = SellerPackageService.request_package(seller, package.pk)
    assert seller_package.base_price == Decimal("400.00")
    assert seller_package.discount_percent == Decimal("20.00")
    assert seller_package.discount_amount == Decimal("80.00")
    assert seller_package.final_price == Decimal("320.00")
    assert seller_package.promotion_campaign_id == campaign.pk
    assert seller_package.promotion_effect_id == effect.pk
    assert seller_package.pricing_snapshot == {
        "promotion_campaign_id": campaign.pk,
        "promotion_effect_id": effect.pk,
        "base_price": "400.00",
        "discount_percent": "20.00",
        "discount_amount": "80.00",
        "final_price": "320.00",
    }
    package.refresh_from_db()
    assert package.price == Decimal("400.00")


def test_31_loft_subscription_purchase_persists_discounted_price_snapshot():
    now = timezone.now()
    duration, _, pedigree_pkg = ensure_paid_loft_catalog()
    customer = CustomerProfileFactory()
    campaign, effect = create_campaign_with_effect(
        name="Loft Checkout Promo",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(hours=2),
        ends_at=now + timedelta(hours=20),
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        target_subscription_duration=duration,
        discount_percent=Decimal("25.00"),
    )
    selection = {
        "duration_id": duration.pk,
        "pedigree_package_id": pedigree_pkg.pk,
        "addon_ids": [],
        "ai_package_id": None,
        "template_ids": [1, 2],
    }
    quote = SubscriptionCheckoutService.execute(
        profile=customer,
        validated_selection=selection,
        dry_run=True,
    )
    checkout = SubscriptionCheckoutService.execute(
        profile=customer,
        validated_selection=selection,
        dry_run=False,
        quote_token=quote.quote_token,
        idempotency_key="loft-promo-checkout-1",
    )
    invoice = checkout.invoice
    assert invoice.subtotal == Decimal("800.00")
    assert invoice.total == Decimal("600.00")
    assert checkout.payment_request.amount == Decimal("600.00")
    assert invoice.pricing_snapshot["promotion"] == {
        "promotion_campaign_id": campaign.pk,
        "promotion_effect_id": effect.pk,
        "base_amount": "800.00",
        "discount_percent": "25.00",
        "discount_amount": "200.00",
        "final_amount": "600.00",
    }


def test_32_auction_highlight_purchase_or_resolver_persists_or_returns_discounted_price():
    now = timezone.now()
    campaign, effect = create_campaign_with_effect(
        name="Highlight Resolver Promo",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=4),
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        discount_percent=Decimal("30.00"),
    )
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        target_code="featured_banner",
        base_price=Decimal("100.00"),
        now=now,
    )
    assert res.as_dict() == {
        "promotion_campaign_id": campaign.pk,
        "promotion_effect_id": effect.pk,
        "base_price": Decimal("100.00"),
        "discount_percent": Decimal("30.00"),
        "discount_amount": Decimal("30.00"),
        "final_price": Decimal("70.00"),
    }


def test_33_campaign_expiry_after_purchase_does_not_change_saved_snapshot():
    now = timezone.now()
    seller = SellerProfileFactory()
    package = PackageFactory(price=Decimal("300.00"))
    campaign, _ = create_campaign_with_effect(
        name="Expiring Promo",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=2),
        ends_at=now + timedelta(hours=1),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        target_package=package,
        discount_percent=Decimal("20.00"),
    )
    seller_package = SellerPackageService.request_package(seller, package.pk)
    assert seller_package.final_price == Decimal("240.00")

    # Simulate campaign expiry in the past
    PromotionCampaign.objects.filter(pk=campaign.pk).update(
        starts_at=now - timedelta(hours=5),
        ends_at=now - timedelta(hours=1),
    )
    seller_package.refresh_from_db()
    assert seller_package.final_price == Decimal("240.00")
    assert seller_package.discount_amount == Decimal("60.00")

    # Activating the pending package after campaign expiry keeps its snapshot intact
    activated = SellerPackageService.activate_package(seller_package)
    assert activated.final_price == Decimal("240.00")
    assert activated.pricing_snapshot["final_price"] == "240.00"


def test_34_campaign_disable_after_purchase_does_not_change_saved_snapshot():
    now = timezone.now()
    duration, _, pedigree_pkg = ensure_paid_loft_catalog()
    customer = CustomerProfileFactory()
    campaign, _ = create_campaign_with_effect(
        name="Disabling Promo",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=4),
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        discount_percent=Decimal("50.00"),
    )
    selection = {
        "duration_id": duration.pk,
        "pedigree_package_id": pedigree_pkg.pk,
        "addon_ids": [],
        "ai_package_id": None,
        "template_ids": [1, 2],
    }
    quote = SubscriptionCheckoutService.execute(
        profile=customer,
        validated_selection=selection,
        dry_run=True,
    )
    checkout = SubscriptionCheckoutService.execute(
        profile=customer,
        validated_selection=selection,
        dry_run=False,
        quote_token=quote.quote_token,
        idempotency_key="loft-disable-after-purchase",
    )
    PromotionCampaignService.disable(campaign)

    invoice = SubscriptionInvoice.objects.get(pk=checkout.invoice.pk)
    assert invoice.subtotal == Decimal("800.00")
    assert invoice.total == Decimal("400.00")
    assert invoice.pricing_snapshot["promotion"]["final_amount"] == "400.00"


def test_35_100_percent_discount_results_in_zero_final_price_never_negative():
    now = timezone.now()
    duration, _, pedigree_pkg = ensure_paid_loft_catalog()
    customer = CustomerProfileFactory()
    manager = make_manager()
    create_campaign_with_effect(
        name="100% Free Promo",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=4),
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        discount_percent=Decimal("100.00"),
    )
    selection = {
        "duration_id": duration.pk,
        "pedigree_package_id": pedigree_pkg.pk,
        "addon_ids": [],
        "ai_package_id": None,
        "template_ids": [1, 2],
    }
    quote = SubscriptionCheckoutService.execute(
        profile=customer,
        validated_selection=selection,
        dry_run=True,
    )
    checkout = SubscriptionCheckoutService.execute(
        profile=customer,
        validated_selection=selection,
        dry_run=False,
        quote_token=quote.quote_token,
        idempotency_key="loft-100-pct-discount",
    )
    assert checkout.invoice.subtotal == Decimal("800.00")
    assert checkout.invoice.total == Decimal("0.00")
    assert checkout.payment_request.amount == Decimal("0.00")

    subscription = SubscriptionActivationService.activate_paid_invoice(
        invoice_id=checkout.invoice.pk,
        confirmed_by=manager.active_profile,
    )
    assert subscription.pk is not None


def test_36_decimal_rounding_is_exact_to_0_01():
    now = timezone.now()
    create_campaign_with_effect(
        name="Rounding Test Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=4),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("33.33"),
    )
    # 99.99 * 33.33 / 100 = 33.326667 -> ROUND_HALF_UP -> 33.33; final = 66.66
    res = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.SELLER_PACKAGE,
        base_price=Decimal("99.99"),
        now=now,
    )
    assert res.discount_amount == Decimal("33.33")
    assert res.final_price == Decimal("66.66")

    # 10.05 * 12.50 / 100 = 1.25625 -> ROUND_HALF_UP -> 1.26; final = 8.79
    create_campaign_with_effect(
        name="Half Up Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=4),
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        discount_percent=Decimal("12.50"),
    )
    res2 = PromotionPricingService.resolve_service_discount(
        target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
        base_price=Decimal("10.05"),
        now=now,
    )
    assert res2.discount_amount == Decimal("1.26")
    assert res2.final_price == Decimal("8.79")


# ==============================================================================
# 37-39: Reward / multiplier boundary
# ==============================================================================


def test_37_service_discount_resolution_creates_zero_point_transactions():
    now = timezone.now()
    seller = SellerProfileFactory()
    package = PackageFactory(price=Decimal("250.00"))
    before_count = PointTransaction.objects.count()

    create_campaign_with_effect(
        name="Zero PP Side Effects",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=10),
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("20.00"),
    )
    SellerPackageService.quote_package(package, now=now)
    SellerPackageService.request_package(seller, package.pk)
    assert PointTransaction.objects.count() == before_count


def test_38_deal_completed_reward_remains_untouched_by_be_pro_02():
    now = timezone.now()
    create_campaign_with_effect(
        name="Seasonal Campaign Does Not Touch DEAL_COMPLETED",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        discount_percent=Decimal("40.00"),
    )
    rule = RewardRuleService.get_rule(RewardRuleCode.DEAL_COMPLETED)
    assert rule.enabled is True
    assert rule.multiplier == Decimal("2")
    assert rule.configuration == {
        "points_per_unit_EGP": 1,
        "points_per_unit_USD": 1,
    }


def test_39_no_global_reward_engine_multiplier_exists_when_omitted():
    assert "POINTS_MULTIPLIER" not in PromotionEffectType.values
    assert list(PromotionEffectType.values) == ["PERCENT_DISCOUNT"]


# ==============================================================================
# 40-44: Permissions
# ==============================================================================


def test_40_unauthenticated_effect_admin_endpoints_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Auth Check Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=10),
    )
    url = f"/api/promotions/admin/campaigns/{campaign.pk}/effects/"
    assert APIClient().get(url).status_code == status.HTTP_401_UNAUTHORIZED
    assert APIClient().post(url, {}).status_code == status.HTTP_401_UNAUTHORIZED


def test_41_customer_and_seller_effect_admin_endpoints_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Role Check Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=10),
    )
    url = f"/api/promotions/admin/campaigns/{campaign.pk}/effects/"
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)

    assert auth_client(customer).get(url).status_code == status.HTTP_403_FORBIDDEN
    assert auth_client(seller).get(url).status_code == status.HTTP_403_FORBIDDEN
    assert auth_client(customer).post(url, {}).status_code == status.HTTP_403_FORBIDDEN
    assert auth_client(seller).post(url, {}).status_code == status.HTTP_403_FORBIDDEN


def test_42_manager_without_effect_permission_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Perm Check Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=10),
    )
    effect = PromotionCampaignEffectService.create(
        campaign,
        target_type=PromotionTargetType.SELLER_PACKAGE,
        discount_percent=Decimal("15.00"),
    )
    # Manager with campaign permissions but NOT campaign_effects permissions
    manager = make_manager(
        ["promotions.campaigns.list", "promotions.campaigns.update"]
    )
    client = auth_client(manager)
    list_url = f"/api/promotions/admin/campaigns/{campaign.pk}/effects/"
    detail_url = f"/api/promotions/admin/campaigns/{campaign.pk}/effects/{effect.pk}/"

    assert client.get(list_url).status_code == status.HTTP_403_FORBIDDEN
    assert (
        client.post(
            list_url,
            {
                "target_type": PromotionTargetType.AUCTION_HIGHLIGHT,
                "discount_percent": "20.00",
            },
            format="json",
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert client.get(detail_url).status_code == status.HTTP_403_FORBIDDEN
    assert (
        client.patch(
            detail_url, {"discount_percent": "25.00"}, format="json"
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert client.delete(detail_url).status_code == status.HTTP_403_FORBIDDEN


def test_43_manager_with_exact_effect_permission_allowed():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Exact Perm Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=10),
    )
    list_url = f"/api/promotions/admin/campaigns/{campaign.pk}/effects/"

    creator = make_manager(["promotions.campaign_effects.create"])
    create_resp = auth_client(creator).post(
        list_url,
        {
            "target_type": PromotionTargetType.SELLER_PACKAGE,
            "discount_percent": "15.00",
            "description": "Created by manager",
        },
        format="json",
    )
    assert create_resp.status_code == status.HTTP_201_CREATED
    effect_id = create_resp.data["id"]
    detail_url = f"/api/promotions/admin/campaigns/{campaign.pk}/effects/{effect_id}/"

    lister = make_manager(["promotions.campaign_effects.list"])
    list_resp = auth_client(lister).get(list_url)
    assert list_resp.status_code == status.HTTP_200_OK
    assert [r["id"] for r in results_of(list_resp)] == [effect_id]

    retriever = make_manager(["promotions.campaign_effects.retrieve"])
    retrieve_resp = auth_client(retriever).get(detail_url)
    assert retrieve_resp.status_code == status.HTTP_200_OK
    assert retrieve_resp.data["discount_percent"] == "15.00"

    updater = make_manager(["promotions.campaign_effects.update"])
    update_resp = auth_client(updater).patch(
        detail_url,
        {"discount_percent": "25.00"},
        format="json",
    )
    assert update_resp.status_code == status.HTTP_200_OK
    assert update_resp.data["discount_percent"] == "25.00"

    deleter = make_manager(["promotions.campaign_effects.delete"])
    delete_resp = auth_client(deleter).delete(detail_url)
    assert delete_resp.status_code == status.HTTP_204_NO_CONTENT


def test_44_superuser_allowed():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Superuser Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=2),
    )
    superuser = UserAccountFactory(with_profile=ProfileType.MANAGER, is_superuser=True)
    client = auth_client(superuser)
    list_url = f"/api/promotions/admin/campaigns/{campaign.pk}/effects/"

    resp = client.post(
        list_url,
        {
            "target_type": PromotionTargetType.LOFT_SUBSCRIPTION,
            "discount_percent": "35.00",
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED
    effect_id = resp.data["id"]
    assert client.get(list_url).status_code == status.HTTP_200_OK
    assert (
        client.delete(
            f"/api/promotions/admin/campaigns/{campaign.pk}/effects/{effect_id}/"
        ).status_code
        == status.HTTP_204_NO_CONTENT
    )


# ==============================================================================
# 45-51: Coexistence & regressions
# ==============================================================================


def test_45_existing_discount_offer_and_cashback_offer_flows_remain_unchanged():
    asset = BirdFactory(price=Decimal("200.00"))
    offer = DiscountOfferFactory(
        title="Marketplace 10% Offer",
        value_type=OfferValueType.PERCENTAGE,
        value=Decimal("10.00"),
        target_type=OfferTargetType.ALL,
        is_active=True,
    )
    chosen = DiscountOfferService.choose_best_offer(asset, Decimal("200.00"))
    assert chosen.pk == offer.pk
    assert DiscountOfferService.calculate_discount(chosen, Decimal("200.00")) == Decimal(
        "20.00"
    )
    assert CashbackOffer.objects.count() == 0


def test_46_seasons_remain_unchanged():
    now = timezone.now()
    season = Season.objects.create(
        title="Unchanged Season",
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=25),
        status=SeasonStatus.DRAFT,
    )
    create_campaign_with_effect(
        name="Seasonal Campaign Coexistence",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        target_type=PromotionTargetType.LOFT_SUBSCRIPTION,
        discount_percent=Decimal("15.00"),
    )
    season.refresh_from_db()
    assert season.status == SeasonStatus.DRAFT


def test_47_referrals_and_loyalty_remain_unchanged():
    assert Referral.objects.count() == 0
    assert RewardRuleService.get_rule(RewardRuleCode.REFERRAL_APPROVED).points == 30
    assert RewardRuleService.get_rule(RewardRuleCode.REFERRAL_FIRST_BID).points == 200


def test_48_existing_seller_package_tests_pass():
    seller = SellerProfileFactory()
    package = PackageFactory(price=Decimal("150.00"), points=10)
    sp = SellerPackageService.request_package(seller, package.pk)
    assert sp.final_price == Decimal("150.00")
    assert sp.discount_percent == Decimal("0.00")
    assert sp.discount_amount == Decimal("0.00")
    assert sp.promotion_campaign_id is None
    assert sp.promotion_effect_id is None
    activated = SellerPackageService.activate_package(sp)
    assert activated.activated is True
    assert activated.remaining_points == 10


def test_49_existing_loft_subscription_tests_pass():
    duration, _, pedigree_pkg = ensure_paid_loft_catalog()
    customer = CustomerProfileFactory()
    pricing = SubscriptionPricingService.calculate(
        profile=customer,
        validated_selection={
            "duration_id": duration.pk,
            "pedigree_package_id": pedigree_pkg.pk,
            "addon_ids": [],
            "ai_package_id": None,
            "template_ids": [1, 2],
        },
    )
    assert pricing.subtotal == Decimal("800.00")
    assert pricing.total == Decimal("800.00")
    assert "promotion" not in pricing.pricing_snapshot()


def test_50_existing_auction_tests_pass():
    seller = SellerProfileFactory()
    package = PackageFactory(points=10, auction_cost=2, product_cost=2)
    sp = SellerPackageService.request_package(seller, package.pk)
    sp = SellerPackageService.activate_package(sp)
    auction = AuctionFactory(owner=seller)
    assert auction.pk is not None
    assert sp.activated is True


def test_51_migrations_match_models_and_constraints():
    with override_settings(MIGRATION_MODULES={}), translation.override("en"):
        call_command("makemigrations", "promotions", "--check", "--dry-run")

    effect_constraints = {c.name for c in PromotionCampaignEffect._meta.constraints}
    effect_indexes = {idx.name for idx in PromotionCampaignEffect._meta.indexes}
    assert "promo_eff_discount_pct_range_ck" in effect_constraints
    assert "promo_eff_target_fk_matches_type_ck" in effect_constraints
    assert "promo_eff_camp_target_idx" in effect_indexes
    assert "promo_eff_target_disc_idx" in effect_indexes

    snap_constraints = {
        c.name for c in SellerPackagePromotionSnapshot._meta.constraints
    }
    assert "promo_pkg_snap_amounts_non_neg_ck" in snap_constraints
