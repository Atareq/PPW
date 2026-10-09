from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone, translation
import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.loyalty.models import PointTransaction
from apps.promotions.models import (
    CashbackOffer,
    DiscountOffer,
    OfferTargetType,
    OfferValueType,
    PromotionCampaign,
    PromotionCampaignStatus,
    PromotionCampaignType,
)
from apps.promotions.services import PromotionCampaignService
from apps.seasons.models import Season, SeasonStatus
from apps.users.constants import ProfileType
from tests.factories import DiscountOfferFactory, UserAccountFactory

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


def test_01_create_monthly_campaign_succeeds():
    now = timezone.now()
    manager = make_manager(["promotions.campaigns.create"])
    campaign = PromotionCampaignService.create(
        {
            "name": "October Monthly Campaign",
            "campaign_type": PromotionCampaignType.MONTHLY,
            "starts_at": now + timedelta(days=1),
            "ends_at": now + timedelta(days=31),
            "description": "Monthly promotion window",
            "metadata": {"notes": "October window"},
        },
        created_by=manager.active_profile,
    )
    assert campaign.pk is not None
    assert campaign.campaign_type == PromotionCampaignType.MONTHLY
    assert campaign.is_enabled is True
    assert campaign.created_by_id == manager.active_profile.pk
    assert campaign.metadata == {"notes": "October window"}


def test_02_create_seasonal_campaign_succeeds():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Winter Racing Season Promo",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now + timedelta(days=5),
        ends_at=now + timedelta(days=95),
        description="Seasonal promo window independent of apps.seasons.Season",
    )
    assert campaign.pk is not None
    assert campaign.campaign_type == PromotionCampaignType.SEASONAL
    assert Season.objects.count() == 0


def test_03_create_flash_campaign_succeeds():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Weekend Flash Promo",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=26),
    )
    assert campaign.pk is not None
    assert campaign.campaign_type == PromotionCampaignType.FLASH


def test_04_invalid_campaign_type_rejected():
    now = timezone.now()
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignService.create(
            name="Invalid Type Campaign",
            campaign_type="WEEKLY",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=5),
        )
    assert "campaign_type" in exc_info.value.detail

    with pytest.raises(DjangoValidationError):
        PromotionCampaign.objects.create(
            name="Direct ORM Invalid Type",
            campaign_type="UNKNOWN",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=5),
        )


def test_05_starts_at_equal_to_ends_at_rejected():
    now = timezone.now() + timedelta(days=1)
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignService.create(
            name="Zero Duration Campaign",
            campaign_type=PromotionCampaignType.FLASH,
            starts_at=now,
            ends_at=now,
        )
    assert "ends_at" in exc_info.value.detail

    with pytest.raises(DjangoValidationError):
        PromotionCampaign.objects.create(
            name="Direct Zero Duration",
            campaign_type=PromotionCampaignType.FLASH,
            starts_at=now,
            ends_at=now,
        )


def test_06_starts_at_after_ends_at_rejected():
    now = timezone.now()
    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignService.create(
            name="Reversed Window Campaign",
            campaign_type=PromotionCampaignType.MONTHLY,
            starts_at=now + timedelta(days=10),
            ends_at=now + timedelta(days=5),
        )
    assert "ends_at" in exc_info.value.detail

    # Also verify DB CheckConstraint when bypassing save/full_clean via bulk_create
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PromotionCampaign.objects.bulk_create(
                [
                    PromotionCampaign(
                        name="DB Check Violation",
                        campaign_type=PromotionCampaignType.MONTHLY,
                        starts_at=now + timedelta(days=10),
                        ends_at=now + timedelta(days=5),
                    )
                ]
            )


def test_07_naive_datetime_rejected():
    naive_start = datetime(2026, 10, 10, 12, 0, 0)
    naive_end = datetime(2026, 10, 11, 12, 0, 0)

    with pytest.raises(ValidationError) as exc_info:
        PromotionCampaignService.create(
            name="Naive Datetime Campaign",
            campaign_type=PromotionCampaignType.FLASH,
            starts_at=naive_start,
            ends_at=naive_end,
        )
    assert "starts_at" in exc_info.value.detail

    manager = make_manager(["promotions.campaigns.create"])
    response = auth_client(manager).post(
        "/api/promotions/admin/campaigns/",
        {
            "name": "API Naive Datetime Campaign",
            "campaign_type": PromotionCampaignType.FLASH,
            "starts_at": "2026-10-10T12:00:00",
            "ends_at": "2026-10-11T12:00:00",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "starts_at" in response.data


def test_08_effective_status_scheduled_before_starts_at():
    t0 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    campaign = PromotionCampaignService.create(
        name="Scheduled Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0,
        ends_at=t0 + timedelta(days=30),
    )
    one_sec_before = t0 - timedelta(seconds=1)
    assert (
        PromotionCampaignService.effective_status(campaign, now=one_sec_before)
        == PromotionCampaignStatus.SCHEDULED
    )
    assert campaign.is_active_at(now=one_sec_before) is False


def test_09_effective_status_active_at_exact_starts_at():
    t0 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    campaign = PromotionCampaignService.create(
        name="Exact Start Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0,
        ends_at=t0 + timedelta(days=30),
    )
    assert (
        PromotionCampaignService.effective_status(campaign, now=t0)
        == PromotionCampaignStatus.ACTIVE
    )
    assert campaign.is_active_at(now=t0) is True


def test_10_effective_status_active_inside_window():
    t0 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    campaign = PromotionCampaignService.create(
        name="Mid Window Campaign",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=t0,
        ends_at=t0 + timedelta(days=30),
    )
    mid = t0 + timedelta(days=15)
    assert (
        PromotionCampaignService.effective_status(campaign, now=mid)
        == PromotionCampaignStatus.ACTIVE
    )
    assert campaign.is_active_at(now=mid) is True


def test_11_effective_status_ended_at_exact_ends_at():
    t0 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    t1 = t0 + timedelta(days=30)
    campaign = PromotionCampaignService.create(
        name="Exact End Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0,
        ends_at=t1,
    )
    assert (
        PromotionCampaignService.effective_status(campaign, now=t1)
        == PromotionCampaignStatus.ENDED
    )
    assert campaign.is_active_at(now=t1) is False


def test_12_effective_status_ended_after_ends_at():
    t0 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    t1 = t0 + timedelta(days=30)
    campaign = PromotionCampaignService.create(
        name="After End Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0,
        ends_at=t1,
    )
    after = t1 + timedelta(seconds=1)
    assert (
        PromotionCampaignService.effective_status(campaign, now=after)
        == PromotionCampaignStatus.ENDED
    )


def test_13_effective_status_disabled_when_disabled_inside_window():
    t0 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    campaign = PromotionCampaignService.create(
        name="Disabled Mid Window Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0,
        ends_at=t0 + timedelta(hours=24),
        is_enabled=False,
    )
    assert (
        PromotionCampaignService.effective_status(
            campaign, now=t0 + timedelta(hours=12)
        )
        == PromotionCampaignStatus.DISABLED
    )
    assert campaign.is_active_at(now=t0 + timedelta(hours=12)) is False


def test_14_active_campaigns_returns_only_enabled_in_window():
    t0 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=dt_timezone.utc)
    active_c = PromotionCampaignService.create(
        name="Active at t0",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0,
        ends_at=t0 + timedelta(days=30),
        is_enabled=True,
    )
    PromotionCampaignService.create(
        name="Starts later",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0 + timedelta(seconds=1),
        ends_at=t0 + timedelta(days=30),
        is_enabled=True,
    )
    PromotionCampaignService.create(
        name="Ended at t0",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0 - timedelta(hours=24),
        ends_at=t0,
        is_enabled=True,
    )
    PromotionCampaignService.create(
        name="Disabled at t0",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=t0 - timedelta(days=1),
        ends_at=t0 + timedelta(days=10),
        is_enabled=False,
    )

    active_ids = list(
        PromotionCampaignService.active_campaigns(now=t0).values_list("id", flat=True)
    )
    assert active_ids == [active_c.pk]


def test_15_active_campaigns_filters_by_campaign_type():
    t0 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=dt_timezone.utc)
    monthly = PromotionCampaignService.create(
        name="Monthly Active",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0 - timedelta(days=1),
        ends_at=t0 + timedelta(days=20),
    )
    seasonal = PromotionCampaignService.create(
        name="Seasonal Active",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=t0 - timedelta(days=5),
        ends_at=t0 + timedelta(days=50),
    )
    flash = PromotionCampaignService.create(
        name="Flash Active",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0 - timedelta(hours=2),
        ends_at=t0 + timedelta(hours=22),
    )

    assert list(
        PromotionCampaignService.active_campaigns(
            now=t0, campaign_type=PromotionCampaignType.MONTHLY
        )
    ) == [monthly]
    assert list(
        PromotionCampaignService.active_campaigns(
            now=t0, campaign_type=PromotionCampaignType.SEASONAL
        )
    ) == [seasonal]
    assert list(
        PromotionCampaignService.active_campaigns(
            now=t0, campaign_type=PromotionCampaignType.FLASH
        )
    ) == [flash]


def test_16_disabled_campaign_excluded_from_active_campaigns():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Toggleable Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=23),
        is_enabled=True,
    )
    assert campaign in PromotionCampaignService.active_campaigns(now=now)

    PromotionCampaignService.disable(campaign)
    assert campaign not in PromotionCampaignService.active_campaigns(now=now)


def test_17_scheduled_future_campaign_excluded_from_active_campaigns():
    now = timezone.now()
    future = PromotionCampaignService.create(
        name="Future Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(minutes=5),
        ends_at=now + timedelta(days=30),
        is_enabled=True,
    )
    assert future not in PromotionCampaignService.active_campaigns(now=now)


def test_18_ended_campaign_excluded_from_active_campaigns():
    now = timezone.now()
    ended = PromotionCampaignService.create(
        name="Ended Campaign",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(seconds=1),
        is_enabled=True,
    )
    assert ended not in PromotionCampaignService.active_campaigns(now=now)


def test_19_flash_campaign_with_24h_window_works():
    t0 = datetime(2026, 12, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    flash_24h = PromotionCampaignService.create(
        name="24h Flash",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0,
        ends_at=t0 + timedelta(hours=24),
    )
    assert (flash_24h.ends_at - flash_24h.starts_at) == timedelta(hours=24)
    assert flash_24h in PromotionCampaignService.active_campaigns(
        now=t0 + timedelta(hours=23, minutes=59)
    )
    assert flash_24h not in PromotionCampaignService.active_campaigns(
        now=t0 + timedelta(hours=24)
    )


def test_20_flash_campaign_with_48h_window_works():
    t0 = datetime(2026, 12, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    flash_48h = PromotionCampaignService.create(
        name="48h Flash",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0,
        ends_at=t0 + timedelta(hours=48),
    )
    assert (flash_48h.ends_at - flash_48h.starts_at) == timedelta(hours=48)
    assert flash_48h in PromotionCampaignService.active_campaigns(
        now=t0 + timedelta(hours=36)
    )
    assert flash_48h not in PromotionCampaignService.active_campaigns(
        now=t0 + timedelta(hours=48)
    )


def test_21_flash_campaign_with_12h_window_works():
    t0 = datetime(2026, 12, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    flash_12h = PromotionCampaignService.create(
        name="12h Flash",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0,
        ends_at=t0 + timedelta(hours=12),
    )
    assert (flash_12h.ends_at - flash_12h.starts_at) == timedelta(hours=12)
    assert flash_12h in PromotionCampaignService.active_campaigns(
        now=t0 + timedelta(hours=6)
    )
    assert flash_12h not in PromotionCampaignService.active_campaigns(
        now=t0 + timedelta(hours=12)
    )


def test_22_overlapping_monthly_and_flash_campaigns_both_active():
    t0 = datetime(2026, 12, 10, 12, 0, 0, tzinfo=dt_timezone.utc)
    monthly = PromotionCampaignService.create(
        name="December Monthly",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=t0 - timedelta(days=9),
        ends_at=t0 + timedelta(days=21),
    )
    flash = PromotionCampaignService.create(
        name="Mid-December Flash",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0 - timedelta(hours=6),
        ends_at=t0 + timedelta(hours=18),
    )
    active = list(PromotionCampaignService.active_campaigns(now=t0))
    assert active == [monthly, flash]


def test_23_overlapping_seasonal_and_flash_campaigns_both_active():
    t0 = datetime(2026, 12, 10, 12, 0, 0, tzinfo=dt_timezone.utc)
    seasonal = PromotionCampaignService.create(
        name="Winter Seasonal",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=t0 - timedelta(days=20),
        ends_at=t0 + timedelta(days=40),
    )
    flash = PromotionCampaignService.create(
        name="Winter Flash",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0 - timedelta(hours=2),
        ends_at=t0 + timedelta(hours=46),
    )
    active = list(PromotionCampaignService.active_campaigns(now=t0))
    assert active == [seasonal, flash]


def test_24_same_type_overlapping_campaigns_allowed_and_deterministically_ordered():
    t0 = datetime(2026, 12, 10, 12, 0, 0, tzinfo=dt_timezone.utc)
    flash_a = PromotionCampaignService.create(
        name="Flash A",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0,
        ends_at=t0 + timedelta(hours=24),
    )
    flash_b = PromotionCampaignService.create(
        name="Flash B",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=t0,
        ends_at=t0 + timedelta(hours=48),
    )
    active = list(
        PromotionCampaignService.active_campaigns(
            now=t0 + timedelta(hours=1),
            campaign_type=PromotionCampaignType.FLASH,
        )
    )
    assert active == [flash_a, flash_b]


def test_25_enable_and_disable_are_idempotent():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Idempotent Toggle Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=30),
        is_enabled=True,
    )
    manager = make_manager(
        ["promotions.campaigns.enable", "promotions.campaigns.disable"]
    )
    client = auth_client(manager)

    # Disable twice via API
    r1 = client.post(f"/api/promotions/admin/campaigns/{campaign.pk}/disable/")
    r2 = client.post(f"/api/promotions/admin/campaigns/{campaign.pk}/disable/")
    assert r1.status_code == status.HTTP_200_OK
    assert r2.status_code == status.HTTP_200_OK
    assert r2.data["is_enabled"] is False
    assert r2.data["effective_status"] == PromotionCampaignStatus.DISABLED

    # Enable twice via API
    r3 = client.post(f"/api/promotions/admin/campaigns/{campaign.pk}/enable/")
    r4 = client.post(f"/api/promotions/admin/campaigns/{campaign.pk}/enable/")
    assert r3.status_code == status.HTTP_200_OK
    assert r4.status_code == status.HTTP_200_OK
    assert r4.data["is_enabled"] is True
    assert r4.data["effective_status"] == PromotionCampaignStatus.SCHEDULED


def test_26_manager_permissions_enforced_and_customer_seller_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Permission Check Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=30),
    )

    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    unprivileged_manager = make_manager([])

    for user in (customer, seller, unprivileged_manager):
        c = auth_client(user)
        assert (
            c.get("/api/promotions/admin/campaigns/").status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.get(f"/api/promotions/admin/campaigns/{campaign.pk}/").status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.post(
                "/api/promotions/admin/campaigns/",
                {
                    "name": "Unauthorized",
                    "campaign_type": PromotionCampaignType.FLASH,
                    "starts_at": (now + timedelta(days=1)).isoformat(),
                    "ends_at": (now + timedelta(days=2)).isoformat(),
                },
                format="json",
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.patch(
                f"/api/promotions/admin/campaigns/{campaign.pk}/",
                {"name": "Renamed"},
                format="json",
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.post(
                f"/api/promotions/admin/campaigns/{campaign.pk}/disable/"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.post(
                f"/api/promotions/admin/campaigns/{campaign.pk}/enable/"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )

    # Manager with exact permissions succeeds
    privileged_manager = make_manager(
        [
            "promotions.campaigns.list",
            "promotions.campaigns.retrieve",
            "promotions.campaigns.create",
            "promotions.campaigns.update",
            "promotions.campaigns.enable",
            "promotions.campaigns.disable",
        ]
    )
    mc = auth_client(privileged_manager)
    assert mc.get("/api/promotions/admin/campaigns/").status_code == status.HTTP_200_OK
    assert (
        mc.get(f"/api/promotions/admin/campaigns/{campaign.pk}/").status_code
        == status.HTTP_200_OK
    )


def test_27_updating_campaign_before_start_succeeds():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Draft Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=32),
        description="Initial",
        metadata={"phase": 1},
    )
    new_start = now + timedelta(days=3)
    new_end = now + timedelta(days=5)

    manager = make_manager(["promotions.campaigns.update"])
    response = auth_client(manager).patch(
        f"/api/promotions/admin/campaigns/{campaign.pk}/",
        {
            "name": "Updated Flash Campaign",
            "campaign_type": PromotionCampaignType.FLASH,
            "starts_at": new_start.isoformat(),
            "ends_at": new_end.isoformat(),
            "is_enabled": False,
            "description": "Updated description",
            "metadata": {"phase": 2},
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    campaign.refresh_from_db()
    assert campaign.name == "Updated Flash Campaign"
    assert campaign.campaign_type == PromotionCampaignType.FLASH
    assert campaign.starts_at == new_start
    assert campaign.ends_at == new_end
    assert campaign.is_enabled is False
    assert campaign.description == "Updated description"
    assert campaign.metadata == {"phase": 2}


def test_28_invalid_update_ends_at_lte_starts_at_rejected():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="Scheduled Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=5),
        ends_at=now + timedelta(days=10),
    )
    manager = make_manager(["promotions.campaigns.update"])
    response = auth_client(manager).patch(
        f"/api/promotions/admin/campaigns/{campaign.pk}/",
        {
            "ends_at": (now + timedelta(days=2)).isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "ends_at" in response.data


def test_29_updating_schedule_or_type_after_start_or_end_rejected_while_descriptive_allowed():
    now = timezone.now()
    active_campaign = PromotionCampaignService.create(
        name="Active Campaign",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=29),
    )
    ended_campaign = PromotionCampaignService.create(
        name="Ended Campaign",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(days=3),
        ends_at=now - timedelta(days=1),
    )

    manager = make_manager(["promotions.campaigns.update"])
    client = auth_client(manager)

    # Active campaign: schedule and type changes are locked
    for field, val in [
        ("starts_at", (now - timedelta(days=2)).isoformat()),
        ("ends_at", (now + timedelta(days=10)).isoformat()),
        ("campaign_type", PromotionCampaignType.FLASH),
    ]:
        resp = client.patch(
            f"/api/promotions/admin/campaigns/{active_campaign.pk}/",
            {field: val},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert field in resp.data

    # Active campaign: descriptive edits and is_enabled toggle succeed
    resp_ok = client.patch(
        f"/api/promotions/admin/campaigns/{active_campaign.pk}/",
        {
            "name": "Active Renamed",
            "description": "Updated notes",
            "metadata": {"banner": "gold"},
            "is_enabled": False,
        },
        format="json",
    )
    assert resp_ok.status_code == status.HTTP_200_OK
    active_campaign.refresh_from_db()
    assert active_campaign.name == "Active Renamed"
    assert active_campaign.is_enabled is False

    # Ended campaign: schedule/type mutation to reactivate is rejected
    resp_ended = client.patch(
        f"/api/promotions/admin/campaigns/{ended_campaign.pk}/",
        {"ends_at": (now + timedelta(days=5)).isoformat()},
        format="json",
    )
    assert resp_ended.status_code == status.HTTP_400_BAD_REQUEST
    assert "ends_at" in resp_ended.data


def test_30_zero_point_transactions_created_by_campaign_lifecycle():
    now = timezone.now()
    before_count = PointTransaction.objects.count()

    campaign = PromotionCampaignService.create(
        name="No Points Campaign",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=23),
    )
    assert PromotionCampaignService.effective_status(campaign, now=now) == PromotionCampaignStatus.ACTIVE
    assert list(PromotionCampaignService.active_campaigns(now=now)) == [campaign]
    PromotionCampaignService.disable(campaign)
    PromotionCampaignService.enable(campaign)

    assert PointTransaction.objects.count() == before_count


def test_31_existing_discount_cashback_and_season_models_unaffected_and_decoupled():
    now = timezone.now()
    discount_offer = DiscountOfferFactory(
        title="Existing Marketplace Offer",
        value_type=OfferValueType.PERCENTAGE,
        value=Decimal("10.00"),
        target_type=OfferTargetType.ALL,
        is_active=True,
    )
    season = Season.objects.create(
        title="Independent Seller Season",
        starts_at=now - timedelta(days=10),
        ends_at=now + timedelta(days=20),
        status=SeasonStatus.DRAFT,
    )
    seasonal_campaign = PromotionCampaignService.create(
        name="Independent Seasonal Campaign",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )

    assert DiscountOffer.objects.filter(pk=discount_offer.pk).exists()
    assert CashbackOffer.objects.count() == 0
    assert season.status == SeasonStatus.DRAFT
    assert not hasattr(seasonal_campaign, "season")
    assert seasonal_campaign.effective_status(now=now) == PromotionCampaignStatus.ACTIVE


def test_32_public_active_endpoint_returns_safe_active_campaigns():
    now = timezone.now()
    active_monthly = PromotionCampaignService.create(
        name="Public Monthly",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=20),
        description="Public description",
        metadata={"internal_only": True},
    )
    active_flash = PromotionCampaignService.create(
        name="Public Flash",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=23),
    )
    PromotionCampaignService.create(
        name="Disabled Flash",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=23),
        is_enabled=False,
    )
    PromotionCampaignService.create(
        name="Future Seasonal",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=30),
    )

    # Unauthenticated rejected
    assert APIClient().get("/api/promotions/active/").status_code == status.HTTP_401_UNAUTHORIZED

    # Authenticated customer can list active campaigns
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    resp = auth_client(customer).get("/api/promotions/active/")
    assert resp.status_code == status.HTTP_200_OK
    rows = results_of(resp)
    assert [row["id"] for row in rows] == [active_monthly.pk, active_flash.pk]
    assert set(rows[0].keys()) == {
        "id",
        "name",
        "campaign_type",
        "starts_at",
        "ends_at",
        "effective_status",
        "description",
    }

    # Filter by campaign_type=FLASH
    resp_flash = auth_client(customer).get(
        "/api/promotions/active/?campaign_type=FLASH"
    )
    assert resp_flash.status_code == status.HTTP_200_OK
    assert [row["id"] for row in results_of(resp_flash)] == [active_flash.pk]


def test_33_admin_list_filters_by_type_enabled_and_effective_status():
    now = timezone.now()
    c_scheduled = PromotionCampaignService.create(
        name="Scheduled",
        campaign_type=PromotionCampaignType.MONTHLY,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=30),
        is_enabled=True,
    )
    c_active = PromotionCampaignService.create(
        name="Active",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=2),
        ends_at=now + timedelta(hours=22),
        is_enabled=True,
    )
    c_ended = PromotionCampaignService.create(
        name="Ended",
        campaign_type=PromotionCampaignType.SEASONAL,
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
        is_enabled=True,
    )
    c_disabled = PromotionCampaignService.create(
        name="Disabled",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=10),
        is_enabled=False,
    )

    manager = make_manager(["promotions.campaigns.list"])
    client = auth_client(manager)

    assert [
        r["id"]
        for r in results_of(
            client.get("/api/promotions/admin/campaigns/?effective_status=SCHEDULED")
        )
    ] == [c_scheduled.pk]
    assert [
        r["id"]
        for r in results_of(
            client.get("/api/promotions/admin/campaigns/?effective_status=ACTIVE")
        )
    ] == [c_active.pk]
    assert [
        r["id"]
        for r in results_of(
            client.get("/api/promotions/admin/campaigns/?effective_status=ENDED")
        )
    ] == [c_ended.pk]
    assert [
        r["id"]
        for r in results_of(
            client.get("/api/promotions/admin/campaigns/?effective_status=DISABLED")
        )
    ] == [c_disabled.pk]
    assert [
        r["id"]
        for r in results_of(
            client.get("/api/promotions/admin/campaigns/?campaign_type=FLASH&is_enabled=true")
        )
    ] == [c_active.pk]


def test_34_soft_deleted_campaign_excluded():
    now = timezone.now()
    campaign = PromotionCampaignService.create(
        name="To Be Soft Deleted",
        campaign_type=PromotionCampaignType.FLASH,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=23),
    )
    campaign.delete()
    assert list(PromotionCampaignService.active_campaigns(now=now)) == []


def test_35_migrations_match_models_and_constraints():
    with override_settings(MIGRATION_MODULES={}), translation.override("en"):
        call_command("makemigrations", "promotions", "--check", "--dry-run")

    constraint_names = {c.name for c in PromotionCampaign._meta.constraints}
    index_names = {idx.name for idx in PromotionCampaign._meta.indexes}
    assert "promo_campaign_starts_before_ends_ck" in constraint_names
    assert "promo_campaign_active_win_idx" in index_names
    assert "promo_campaign_type_win_idx" in index_names
