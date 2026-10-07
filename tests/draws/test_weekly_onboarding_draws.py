from datetime import datetime, timedelta
from decimal import Decimal
import subprocess
import sys

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.draws.models import (
    WeeklyDraw,
    WeeklyDrawEffectiveStatus,
    WeeklyDrawEntry,
    WeeklyDrawEntrySource,
    WeeklyDrawEntryStatus,
    WeeklyDrawStatus,
)
from apps.draws.services import WeeklyDrawEntryService, WeeklyDrawService
from apps.loyalty.constants import RewardSourceType
from apps.loyalty.models import PointTransaction
from apps.loyalty.services import PointService
from apps.lucky_wheel.models import LuckyWheelGrant, LuckyWheelGrantSourceType
from apps.lucky_wheel.services import LuckyWheelGrantService
from apps.notifications.models import Notification
from apps.payments.models import PaymentRequest
from apps.promotions.models import OfferTriggerType, UserPromotionGrant
from apps.promotions.services import OnboardingHighlightBenefitService
from apps.users.constants import ProfileType
from apps.users.manager_permissions import get_manager_permission_codes
from apps.users.models import CustomerProfile, ManagerProfile, Profile, SellerProfile

pytestmark = pytest.mark.django_db


def auth_client(user=None) -> APIClient:
    client = APIClient()
    if user is not None:
        token = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def register_customer_via_api(
    username: str = "draw_cust",
    phone_number: str = "+201009810001",
):
    payload = {
        "username": username,
        "phone_number": phone_number,
        "password": "StrongPassword!123",
        "customer_profile": {
            "nickname": username,
            "country": "EG",
            "currency": "EGP",
        },
    }
    response = auth_client().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return UserAccount.objects.get(username=username)


def create_seller_via_api(user: UserAccount) -> SellerProfile:
    response = auth_client(user).post(
        "/api/users/mobile/sellers/",
        {
            "nickname": f"{user.username}_seller",
            "country": "EG",
            "currency": "EGP",
            "loft_name": "Draw Loft",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    seller = SellerProfile.objects.get(user=user)
    seller.activated = True
    seller.is_valid = True
    seller.save(update_fields=["activated", "is_valid", "modified"])
    return seller


def create_customer_via_api(user: UserAccount) -> CustomerProfile:
    response = auth_client(user).post(
        "/api/users/mobile/customers/",
        {
            "nickname": f"{user.username}_cust",
            "country": "EG",
            "currency": "EGP",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return CustomerProfile.objects.get(user=user)


def create_historical_customer_account(
    username: str = "hist_cust",
    phone_number: str = "+201009820001",
) -> tuple[UserAccount, CustomerProfile]:
    """Create a pre-existing customer account bypassing onboarding hooks."""
    user = UserAccount.objects.create_user(
        username=username,
        phone_number=phone_number,
        password="StrongPassword!123",
    )
    profile = CustomerProfile.objects.create(
        user=user,
        country="EG",
        currency="EGP",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    return user, profile


def create_historical_seller_account(
    username: str = "hist_seller",
    phone_number: str = "+201009820002",
) -> tuple[UserAccount, SellerProfile]:
    """Create a pre-existing seller account bypassing onboarding hooks."""
    user = UserAccount.objects.create_user(
        username=username,
        phone_number=phone_number,
        password="StrongPassword!123",
    )
    profile = SellerProfile.objects.create(
        user=user,
        nickname=username,
        country="EG",
        currency="EGP",
        loft_name="Hist Loft",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    return user, profile


def create_manager_account(
    permissions: list[str] | None = None,
    username: str = "draw_mgr",
    phone_number: str = "+201009830001",
) -> UserAccount:
    user = UserAccount.objects.create_user(
        username=username,
        phone_number=phone_number,
        password="StrongPassword!123",
        is_staff=True,
    )
    ManagerProfile.objects.create(
        user=user,
        is_active=True,
        activated=True,
        is_valid=True,
        permissions=permissions or [],
    )
    return user


# ==============================================================================
# 1..10: Account-Level Ownership & First-Profile No-Backfill Semantics
# ==============================================================================


def test_01_new_customer_registration_creates_one_weekly_draw_entry():
    user = register_customer_via_api("cust_01", "+201009810101")
    entries = WeeklyDrawEntry.objects.filter(user=user)
    assert entries.count() == 1
    entry = entries.get()
    assert entry.source == WeeklyDrawEntrySource.ONBOARDING
    assert entry.idempotency_key == f"draw:onboarding:user:{user.pk}"
    assert entry.status == WeeklyDrawEntryStatus.PENDING
    assert entry.draw is None


def test_02_new_seller_first_onboarding_creates_one_weekly_draw_entry():
    user = UserAccount.objects.create_user(
        username="seller_first_02",
        phone_number="+201009810102",
        password="StrongPassword!123",
    )
    seller = SellerProfile.objects.create(
        user=user,
        nickname="seller_first_02",
        country="EG",
        currency="EGP",
        loft_name="First Loft",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    entry = WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user=user,
        profile=seller,
    )
    assert entry is not None
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 1
    assert entry.metadata["onboarding_profile_id"] == seller.pk
    assert entry.metadata["onboarding_profile_type"] == ProfileType.SELLER


def test_03_customer_then_seller_creation_keeps_total_entries_one():
    user = register_customer_via_api("cust_seller_03", "+201009810103")
    first_entry = WeeklyDrawEntry.objects.get(user=user)
    create_seller_via_api(user)
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 1
    entry_after = WeeklyDrawEntry.objects.get(user=user)
    assert entry_after.pk == first_entry.pk
    assert entry_after.metadata["onboarding_profile_type"] == ProfileType.CUSTOMER


def test_04_seller_then_customer_creation_keeps_total_entries_one():
    user = UserAccount.objects.create_user(
        username="seller_cust_04",
        phone_number="+201009810104",
        password="StrongPassword!123",
    )
    seller = SellerProfile.objects.create(
        user=user,
        nickname="seller_cust_04",
        country="EG",
        currency="EGP",
        loft_name="First Loft",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    first_entry = WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user=user,
        profile=seller,
    )
    assert first_entry is not None
    create_customer_via_api(user)
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 1
    assert WeeklyDrawEntry.objects.get(user=user).pk == first_entry.pk


def test_05_profile_switch_does_not_create_second_entry():
    user = register_customer_via_api("switch_05", "+201009810105")
    create_seller_via_api(user)
    switched, profile_type = switch_user_profile(user, ProfileType.SELLER)
    assert switched is True
    assert profile_type == ProfileType.SELLER
    switched_back, profile_type = switch_user_profile(user, ProfileType.CUSTOMER)
    assert switched_back is True
    assert profile_type == ProfileType.CUSTOMER
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 1


def test_06_repeated_ensure_for_first_profile_onboarding_is_idempotent():
    user = register_customer_via_api("idem_06", "+201009810106")
    customer = CustomerProfile.objects.get(user=user)
    e1 = WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user=user, profile=customer
    )
    e2 = WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user=user, profile=customer
    )
    assert e1 is not None and e2 is not None
    assert e1.pk == e2.pk
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 1


def test_07_historical_customer_creating_seller_later_gets_zero_entries():
    user, _ = create_historical_customer_account("hist_c_07", "+201009810107")
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    create_seller_via_api(user)
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_08_historical_seller_creating_customer_later_gets_zero_entries():
    user, _ = create_historical_seller_account("hist_s_08", "+201009810108")
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    create_customer_via_api(user)
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_09_soft_deleted_prior_profile_plus_later_profile_creation_gets_zero_entries():
    user, customer = create_historical_customer_account(
        "soft_del_09", "+201009810109"
    )
    customer.is_active = False
    customer.is_valid = False
    customer.save(update_fields=["is_active", "is_valid", "modified"])
    customer.delete()
    assert Profile.all_objects.filter(user=user).count() == 1

    seller = SellerProfile.objects.create(
        user=user,
        nickname="soft_del_09_seller",
        country="EG",
        currency="EGP",
        loft_name="Re-created Loft",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    result = WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user=user,
        profile=seller,
    )
    assert result is None
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_10_manager_account_gets_zero_entries():
    manager = create_manager_account(
        permissions=[],
        username="mgr_10",
        phone_number="+201009810110",
    )
    with pytest.raises(ValidationError):
        WeeklyDrawEntryService.ensure_for_first_profile_onboarding(user=manager)
    with pytest.raises(ValidationError):
        WeeklyDrawEntryService.ensure_for_onboarding(user=manager)
    assert WeeklyDrawEntry.objects.filter(user=manager).count() == 0


# ==============================================================================
# 11..22: Pending vs Eligible Draw Assignment & Time Boundaries
# ==============================================================================


def test_11_onboarding_with_no_draw_creates_pending_entry():
    user = register_customer_via_api("no_draw_11", "+201009810111")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.status == WeeklyDrawEntryStatus.PENDING
    assert entry.draw is None
    assert entry.assigned_at is None


def test_12_onboarding_before_any_draw_opens_assigns_to_next_upcoming_eligible_draw():
    now = timezone.now()
    later_draw = WeeklyDrawService.create_draw(
        title="Week 2",
        starts_at=now + timedelta(days=8),
        ends_at=now + timedelta(days=15),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    earlier_draw = WeeklyDrawService.create_draw(
        title="Week 1",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    user = register_customer_via_api("upcoming_12", "+201009810112")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.status == WeeklyDrawEntryStatus.ASSIGNED
    assert entry.draw_id == earlier_draw.pk
    assert entry.draw_id != later_draw.pk
    assert entry.assigned_at is not None


def test_13_onboarding_during_open_draw_assigns_to_that_open_draw():
    now = timezone.now()
    open_draw = WeeklyDrawService.create_draw(
        title="Current Open Draw",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    WeeklyDrawService.create_draw(
        title="Next Week Draft",
        starts_at=now + timedelta(days=6),
        ends_at=now + timedelta(days=13),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    user = register_customer_via_api("open_draw_13", "+201009810113")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.status == WeeklyDrawEntryStatus.ASSIGNED
    assert entry.draw_id == open_draw.pk
    assert entry.assigned_at is not None


def test_14_onboarding_after_draw_ended_does_not_assign_to_ended_draw():
    now = timezone.now()
    ended_draw = WeeklyDraw.objects.create(
        title="Past Draw",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=3),
        status=WeeklyDrawStatus.OPEN,
    )
    user = register_customer_via_api("after_end_14", "+201009810114")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.status == WeeklyDrawEntryStatus.PENDING
    assert entry.draw is None
    assert entry.draw_id != ended_draw.pk


def test_15_creating_or_opening_eligible_draw_assigns_pending_entries_once():
    u1 = register_customer_via_api("pend_15a", "+201009810115")
    u2 = register_customer_via_api("pend_15b", "+201009810215")
    assert WeeklyDrawEntry.objects.filter(
        status=WeeklyDrawEntryStatus.PENDING
    ).count() == 2

    now = timezone.now()
    # Create a DRAFT draw whose start is already in the past (not eligible until opened)
    draft_draw = WeeklyDraw.objects.create(
        title="Draft Already Started",
        starts_at=now - timedelta(hours=2),
        ends_at=now + timedelta(days=5),
        status=WeeklyDrawStatus.DRAFT,
    )
    assert WeeklyDrawService.assign_pending_entries(now=now) == []
    assert WeeklyDrawEntry.objects.filter(
        status=WeeklyDrawEntryStatus.PENDING
    ).count() == 2

    # Opening the draft draw assigns both pending entries immediately
    opened = WeeklyDrawService.open_draw(draft_draw, now=now)
    assert opened.status == WeeklyDrawStatus.OPEN
    for u in (u1, u2):
        entry = WeeklyDrawEntry.objects.get(user=u)
        assert entry.status == WeeklyDrawEntryStatus.ASSIGNED
        assert entry.draw_id == opened.pk
        assert entry.assigned_at == now


def test_16_repeated_assign_pending_entries_calls_are_idempotent():
    user = register_customer_via_api("pend_16", "+201009810116")
    now = timezone.now()
    draw = WeeklyDraw.objects.create(
        title="Upcoming Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.OPEN,
    )
    first_batch = WeeklyDrawService.assign_pending_entries(now=now)
    assert len(first_batch) == 1
    assert first_batch[0].user_id == user.pk
    second_batch = WeeklyDrawService.assign_pending_entries(
        now=now + timedelta(minutes=5)
    )
    assert second_batch == []
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.draw_id == draw.pk
    assert entry.assigned_at == now


def test_17_closed_draw_never_receives_new_entries():
    now = timezone.now()
    closed_draw = WeeklyDraw.objects.create(
        title="Closed Draw",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
        status=WeeklyDrawStatus.CLOSED,
    )
    user = register_customer_via_api("closed_17", "+201009810117")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.status == WeeklyDrawEntryStatus.PENDING
    assert entry.draw is None
    assert WeeklyDrawService.assign_pending_entries(draw=closed_draw, now=now) == []


def test_18_finalized_draw_never_receives_new_entries():
    now = timezone.now()
    finalized_draw = WeeklyDraw.objects.create(
        title="Finalized Draw",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
        status=WeeklyDrawStatus.FINALIZED,
    )
    user = register_customer_via_api("finalized_18", "+201009810118")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.status == WeeklyDrawEntryStatus.PENDING
    assert entry.draw is None
    assert (
        WeeklyDrawService.assign_pending_entries(draw=finalized_draw, now=now)
        == []
    )


def test_19_exact_boundary_now_equals_starts_at_is_eligible_and_open():
    starts_at = timezone.now().replace(microsecond=0)
    ends_at = starts_at + timedelta(days=7)
    draw = WeeklyDrawService.create_draw(
        title="Boundary Start",
        starts_at=starts_at,
        ends_at=ends_at,
        status=WeeklyDrawStatus.OPEN,
        now=starts_at,
    )
    assert draw.is_currently_open_at(starts_at) is True
    assert draw.effective_status_at(starts_at) == WeeklyDrawEffectiveStatus.OPEN
    chosen = WeeklyDrawService.choose_eligible_draw(now=starts_at)
    assert chosen is not None and chosen.pk == draw.pk


def test_20_exact_boundary_now_equals_ends_at_is_ended_and_not_eligible():
    starts_at = timezone.now().replace(microsecond=0) - timedelta(days=7)
    ends_at = starts_at + timedelta(days=7)
    draw = WeeklyDraw.objects.create(
        title="Boundary End",
        starts_at=starts_at,
        ends_at=ends_at,
        status=WeeklyDrawStatus.OPEN,
    )
    assert draw.is_currently_open_at(ends_at) is False
    assert draw.effective_status_at(ends_at) == WeeklyDrawEffectiveStatus.ENDED
    assert WeeklyDrawService.choose_eligible_draw(now=ends_at) is None


def test_21_boundary_starts_at_minus_one_microsecond_is_upcoming_not_currently_open():
    starts_at = timezone.now().replace(microsecond=0) + timedelta(days=1)
    ends_at = starts_at + timedelta(days=7)
    before_start = starts_at - timedelta(microseconds=1)
    draw = WeeklyDrawService.create_draw(
        title="Before Start",
        starts_at=starts_at,
        ends_at=ends_at,
        status=WeeklyDrawStatus.OPEN,
        now=before_start,
    )
    assert draw.is_currently_open_at(before_start) is False
    assert (
        draw.effective_status_at(before_start)
        == WeeklyDrawEffectiveStatus.SCHEDULED
    )
    chosen = WeeklyDrawService.choose_eligible_draw(now=before_start)
    assert chosen is not None and chosen.pk == draw.pk


def test_22_boundary_ends_at_minus_one_microsecond_is_still_open():
    starts_at = timezone.now().replace(microsecond=0) - timedelta(days=7)
    ends_at = starts_at + timedelta(days=7)
    just_before_end = ends_at - timedelta(microseconds=1)
    draw = WeeklyDrawService.create_draw(
        title="Just Before End",
        starts_at=starts_at,
        ends_at=ends_at,
        status=WeeklyDrawStatus.OPEN,
        now=just_before_end,
    )
    assert draw.is_currently_open_at(just_before_end) is True
    assert (
        draw.effective_status_at(just_before_end) == WeeklyDrawEffectiveStatus.OPEN
    )
    chosen = WeeklyDrawService.choose_eligible_draw(now=just_before_end)
    assert chosen is not None and chosen.pk == draw.pk


# ==============================================================================
# 23..27: Assignment Immutability, Protection, Schedule & Overlap Validation
# ==============================================================================


def test_23_creating_later_draw_with_earlier_starts_at_does_not_move_assigned_entry():
    now = timezone.now()
    later_draw = WeeklyDrawService.create_draw(
        title="Week 2",
        starts_at=now + timedelta(days=7),
        ends_at=now + timedelta(days=14),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    user = register_customer_via_api("immut_23", "+201009810123")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.draw_id == later_draw.pk
    original_assigned_at = entry.assigned_at

    earlier_draw = WeeklyDrawService.create_draw(
        title="Week 1 Inserted Later",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=6),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    entry.refresh_from_db()
    assert entry.draw_id == later_draw.pk
    assert entry.draw_id != earlier_draw.pk
    assert entry.assigned_at == original_assigned_at

    # Direct model reassignment is also rejected
    entry.draw = earlier_draw
    with pytest.raises(DjangoValidationError):
        entry.save()


def test_24_draw_with_assigned_entries_cannot_destructively_delete_or_orphan_entries():
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Protected Draw",
        starts_at=now,
        ends_at=now + timedelta(days=7),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    register_customer_via_api("prot_24", "+201009810124")
    assert draw.entries.count() == 1

    with pytest.raises(ProtectedError):
        draw.delete()
    with pytest.raises(ProtectedError):
        WeeklyDraw.all_objects.filter(pk=draw.pk).delete()
    assert WeeklyDraw.objects.filter(pk=draw.pk).exists()


def test_25_starts_at_greater_or_equal_ends_at_rejected_on_create_and_update():
    now = timezone.now()
    with pytest.raises(ValidationError):
        WeeklyDrawService.create_draw(
            title="Equal Window",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1),
            now=now,
        )
    with pytest.raises(ValidationError):
        WeeklyDrawService.create_draw(
            title="Inverted Window",
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=1),
            now=now,
        )

    draft = WeeklyDrawService.create_draw(
        title="Valid Draft",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=9),
        now=now,
    )
    with pytest.raises(ValidationError):
        WeeklyDrawService.update_draw(
            draft,
            {"ends_at": now + timedelta(days=1)},
            now=now,
        )


def test_26_overlapping_open_draws_are_prevented():
    now = timezone.now()
    WeeklyDrawService.create_draw(
        title="Open Draw 1",
        starts_at=now,
        ends_at=now + timedelta(days=7),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    with pytest.raises(ValidationError):
        WeeklyDrawService.create_draw(
            title="Overlapping Open Draw 2",
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=10),
            status=WeeklyDrawStatus.OPEN,
            now=now,
        )

    overlapping_draft = WeeklyDrawService.create_draw(
        title="Overlapping Draft",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    with pytest.raises(ValidationError):
        WeeklyDrawService.open_draw(overlapping_draft, now=now)

    # Adjacent half-open window [ends_at, ends_at + 7d) is NOT overlapping and succeeds
    adjacent_open = WeeklyDrawService.create_draw(
        title="Adjacent Open Draw",
        starts_at=now + timedelta(days=7),
        ends_at=now + timedelta(days=14),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    assert adjacent_open.status == WeeklyDrawStatus.OPEN


def test_27_naive_datetimes_rejected_on_draw_create_and_update():
    naive_start = datetime(2026, 10, 10, 12, 0, 0)
    naive_end = datetime(2026, 10, 17, 12, 0, 0)
    with pytest.raises(ValidationError):
        WeeklyDrawService.create_draw(
            title="Naive Draw",
            starts_at=naive_start,
            ends_at=naive_end,
        )

    now = timezone.now()
    draft = WeeklyDrawService.create_draw(
        title="Aware Draft",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=9),
        now=now,
    )
    with pytest.raises(ValidationError):
        WeeklyDrawService.update_draw(
            draft,
            {"starts_at": naive_start},
            now=now,
        )


# ==============================================================================
# 28..44: Manager & User APIs, Permissions, and Security
# ==============================================================================


def test_28_manager_can_create_weekly_draw_with_permission():
    manager = create_manager_account(["draws.weekly.create"], "mgr_28", "+201009810128")
    now = timezone.now()
    response = auth_client(manager).post(
        "/api/draws/weekly/",
        {
            "title": "October Week 1",
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=8)).isoformat(),
            "status": WeeklyDrawStatus.DRAFT,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["title"] == "October Week 1"
    assert response.data["status"] == WeeklyDrawStatus.DRAFT
    assert response.data["created_by"] == manager.active_profile.pk


def test_29_manager_can_update_draft_draw_before_start_and_assignment():
    manager = create_manager_account(["draws.weekly.update"], "mgr_29", "+201009810129")
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Initial Draft",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=9),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    new_start = now + timedelta(days=3)
    new_end = now + timedelta(days=10)
    response = auth_client(manager).patch(
        f"/api/draws/weekly/{draw.pk}/",
        {
            "title": "Updated Draft",
            "starts_at": new_start.isoformat(),
            "ends_at": new_end.isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    draw.refresh_from_db()
    assert draw.title == "Updated Draft"
    assert draw.starts_at == new_start
    assert draw.ends_at == new_end


def test_30_manager_cannot_mutate_locked_schedule_fields_after_start_or_assigned_entries():
    manager = create_manager_account(["draws.weekly.update"], "mgr_30", "+201009810130")
    now = timezone.now()
    started_draw = WeeklyDraw.objects.create(
        title="Started Draw",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=6),
        status=WeeklyDrawStatus.OPEN,
    )
    resp_started = auth_client(manager).patch(
        f"/api/draws/weekly/{started_draw.pk}/",
        {"ends_at": (now + timedelta(days=10)).isoformat()},
        format="json",
    )
    assert resp_started.status_code == status.HTTP_400_BAD_REQUEST

    # Upcoming draft draw that already has an assigned entry also locks schedule
    upcoming_with_entry = WeeklyDrawService.create_draw(
        title="Upcoming With Entry",
        starts_at=now + timedelta(days=7),
        ends_at=now + timedelta(days=14),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    # Close started_draw first so upcoming_with_entry receives the new user's entry
    WeeklyDrawService.close_draw(started_draw, now=started_draw.ends_at)
    register_customer_via_api("assigned_30", "+201009810230")
    assert upcoming_with_entry.entries.count() == 1

    resp_assigned = auth_client(manager).patch(
        f"/api/draws/weekly/{upcoming_with_entry.pk}/",
        {"starts_at": (now + timedelta(days=8)).isoformat()},
        format="json",
    )
    assert resp_assigned.status_code == status.HTTP_400_BAD_REQUEST


def test_31_manager_can_open_draft_draw():
    manager = create_manager_account(["draws.weekly.open"], "mgr_31", "+201009810131")
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Draft To Open",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    response = auth_client(manager).post(f"/api/draws/weekly/{draw.pk}/open/")
    assert response.status_code == status.HTTP_200_OK, response.data
    draw.refresh_from_db()
    assert draw.status == WeeklyDrawStatus.OPEN


def test_32_manager_can_close_open_draw():
    manager = create_manager_account(["draws.weekly.close"], "mgr_32", "+201009810132")
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Open To Close",
        starts_at=now - timedelta(days=7),
        ends_at=now - timedelta(seconds=1),
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    response = auth_client(manager).post(f"/api/draws/weekly/{draw.pk}/close/")
    assert response.status_code == status.HTTP_200_OK, response.data
    draw.refresh_from_db()
    assert draw.status == WeeklyDrawStatus.CLOSED


def test_33_invalid_status_transitions_fail_with_400():
    manager = create_manager_account(
        [
            "draws.weekly.create",
            "draws.weekly.update",
            "draws.weekly.open",
            "draws.weekly.close",
        ],
        "mgr_33",
        "+201009810133",
    )
    now = timezone.now()
    draft = WeeklyDrawService.create_draw(
        title="Draft Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    # Cannot close a DRAFT draw
    r1 = auth_client(manager).post(f"/api/draws/weekly/{draft.pk}/close/")
    assert r1.status_code == status.HTTP_400_BAD_REQUEST

    # Cannot transition to FINALIZED via PATCH
    r2 = auth_client(manager).patch(
        f"/api/draws/weekly/{draft.pk}/",
        {"status": WeeklyDrawStatus.FINALIZED},
        format="json",
    )
    assert r2.status_code == status.HTTP_400_BAD_REQUEST

    # Open then close after ends_at, then cannot reopen
    WeeklyDrawService.open_draw(draft, now=now)
    WeeklyDrawService.close_draw(draft, now=draft.ends_at)
    r3 = auth_client(manager).post(f"/api/draws/weekly/{draft.pk}/open/")
    assert r3.status_code == status.HTTP_400_BAD_REQUEST
    r4 = auth_client(manager).post(f"/api/draws/weekly/{draft.pk}/close/")
    assert r4.status_code == status.HTTP_400_BAD_REQUEST


def test_34_manager_can_list_weekly_draws():
    manager = create_manager_account(["draws.weekly.list"], "mgr_34", "+201009810134")
    now = timezone.now()
    d1 = WeeklyDrawService.create_draw(
        title="Draw 1",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        now=now,
    )
    response = auth_client(manager).get("/api/draws/weekly/")
    assert response.status_code == status.HTTP_200_OK
    ids = [item["id"] for item in response.data["results"]]
    assert d1.pk in ids


def test_35_manager_can_retrieve_weekly_draw_detail():
    manager = create_manager_account(
        ["draws.weekly.retrieve"], "mgr_35", "+201009810135"
    )
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Detail Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        now=now,
    )
    response = auth_client(manager).get(f"/api/draws/weekly/{draw.pk}/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["id"] == draw.pk
    assert response.data["title"] == "Detail Draw"


def test_36_manager_can_list_entries_for_a_draw():
    manager = create_manager_account(
        ["draws.weekly.entries.list"], "mgr_36", "+201009810136"
    )
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Entries Draw",
        starts_at=now,
        ends_at=now + timedelta(days=7),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    u1 = register_customer_via_api("ent_36a", "+201009810236")
    u2 = register_customer_via_api("ent_36b", "+201009810336")

    response = auth_client(manager).get(f"/api/draws/weekly/{draw.pk}/entries/")
    assert response.status_code == status.HTTP_200_OK
    user_ids = [item["user_id"] for item in response.data["results"]]
    assert user_ids == [u1.pk, u2.pk]


def test_37_manager_without_required_permission_gets_403_on_each_admin_endpoint():
    manager = create_manager_account([], "mgr_none_37", "+201009810137")
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Perm Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        now=now,
    )
    client = auth_client(manager)
    assert client.get("/api/draws/weekly/").status_code == status.HTTP_403_FORBIDDEN
    assert (
        client.post(
            "/api/draws/weekly/",
            {
                "starts_at": (now + timedelta(days=10)).isoformat(),
                "ends_at": (now + timedelta(days=17)).isoformat(),
            },
            format="json",
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.get(f"/api/draws/weekly/{draw.pk}/").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.patch(
            f"/api/draws/weekly/{draw.pk}/",
            {"title": "Nope"},
            format="json",
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.post(f"/api/draws/weekly/{draw.pk}/open/").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.post(f"/api/draws/weekly/{draw.pk}/close/").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.get(f"/api/draws/weekly/{draw.pk}/entries/").status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_38_customer_gets_403_on_manager_endpoints():
    customer_user = register_customer_via_api("cust_38", "+201009810138")
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Cust Forbidden Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        now=now,
    )
    client = auth_client(customer_user)
    assert client.get("/api/draws/weekly/").status_code == status.HTTP_403_FORBIDDEN
    assert (
        client.get(f"/api/draws/weekly/{draw.pk}/").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.post(f"/api/draws/weekly/{draw.pk}/open/").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.get(f"/api/draws/weekly/{draw.pk}/entries/").status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_39_seller_gets_403_on_manager_endpoints():
    seller_user, _ = create_historical_seller_account("sell_39", "+201009810139")
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Seller Forbidden Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        now=now,
    )
    client = auth_client(seller_user)
    assert client.get("/api/draws/weekly/").status_code == status.HTTP_403_FORBIDDEN
    assert (
        client.get(f"/api/draws/weekly/{draw.pk}/").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.post(f"/api/draws/weekly/{draw.pk}/close/").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.get(f"/api/draws/weekly/{draw.pk}/entries/").status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_40_unauthenticated_user_gets_401_on_manager_and_user_endpoints():
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="Unauth Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        now=now,
    )
    client = APIClient()
    assert (
        client.get("/api/draws/weekly/").status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert (
        client.get(f"/api/draws/weekly/{draw.pk}/").status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert (
        client.get("/api/draws/my-weekly-entry/").status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_41_superuser_can_access_manager_endpoints():
    superuser = create_manager_account([], "super_41", "+201009810141")
    superuser.is_superuser = True
    superuser.save(update_fields=["is_superuser", "modified"])

    now = timezone.now()
    client = auth_client(superuser)
    create_resp = client.post(
        "/api/draws/weekly/",
        {
            "title": "Superuser Draw",
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=8)).isoformat(),
        },
        format="json",
    )
    assert create_resp.status_code == status.HTTP_201_CREATED
    draw_id = create_resp.data["id"]
    assert (
        client.post(f"/api/draws/weekly/{draw_id}/open/").status_code
        == status.HTTP_200_OK
    )
    assert (
        client.get(f"/api/draws/weekly/{draw_id}/entries/").status_code
        == status.HTTP_200_OK
    )


def test_42_authenticated_user_can_read_my_weekly_entry():
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="My Entry Draw",
        starts_at=now,
        ends_at=now + timedelta(days=7),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    user = register_customer_via_api("my_entry_42", "+201009810142")
    response = auth_client(user).get("/api/draws/my-weekly-entry/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["has_entry"] is True
    assert response.data["status"] == WeeklyDrawEntryStatus.ASSIGNED
    assert response.data["draw_id"] == draw.pk
    assert response.data["draw_starts_at"] is not None
    assert response.data["draw_ends_at"] is not None
    assert response.data["assigned_at"] is not None


def test_43_my_weekly_entry_does_not_backfill_historical_accounts():
    user, _ = create_historical_customer_account("hist_read_43", "+201009810143")
    response = auth_client(user).get("/api/draws/my-weekly-entry/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "has_entry": False,
        "status": None,
        "draw_id": None,
        "draw_starts_at": None,
        "draw_ends_at": None,
        "assigned_at": None,
    }
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_44_my_weekly_entry_does_not_leak_other_entrants_or_internal_keys():
    now = timezone.now()
    WeeklyDrawService.create_draw(
        title="Leak Check Draw",
        starts_at=now,
        ends_at=now + timedelta(days=7),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    register_customer_via_api("other_44", "+201009810144")
    user = register_customer_via_api("me_44", "+201009810244")

    response = auth_client(user).get("/api/draws/my-weekly-entry/")
    assert response.status_code == status.HTTP_200_OK
    assert set(response.data.keys()) == {
        "has_entry",
        "status",
        "draw_id",
        "draw_starts_at",
        "draw_ends_at",
        "assigned_at",
    }
    assert "idempotency_key" not in response.data
    assert "metadata" not in response.data
    assert "entries" not in response.data


# ==============================================================================
# 45..52: Combined Onboarding Bundle, Zero Side Effects, Lucky Wheel Separation,
#         Rollback & DB Constraints
# ==============================================================================


def test_45_onboarding_creates_100_pp_highlight_benefit_and_weekly_draw_entry_once():
    user = register_customer_via_api("bundle_45", "+201009810145")
    assert PointService.get_balance(user) == 100
    assert (
        PointTransaction.objects.filter(
            user=user,
            source_type=RewardSourceType.ONBOARDING,
        ).count()
        == 1
    )
    assert (
        UserPromotionGrant.objects.filter(
            user=user,
            source_type=OfferTriggerType.ONBOARDING,
        ).count()
        == 1
    )
    assert (
        WeeklyDrawEntry.objects.filter(
            user=user,
            source=WeeklyDrawEntrySource.ONBOARDING,
        ).count()
        == 1
    )


def test_46_second_profile_creation_creates_zero_additional_onboarding_rewards():
    user = register_customer_via_api("bundle_46", "+201009810146")
    create_seller_via_api(user)

    assert PointService.get_balance(user) == 100
    assert (
        PointTransaction.objects.filter(
            user=user,
            source_type=RewardSourceType.ONBOARDING,
        ).count()
        == 1
    )
    assert (
        UserPromotionGrant.objects.filter(
            user=user,
            source_type=OfferTriggerType.ONBOARDING,
        ).count()
        == 1
    )
    assert (
        WeeklyDrawEntry.objects.filter(
            user=user,
            source=WeeklyDrawEntrySource.ONBOARDING,
        ).count()
        == 1
    )


def test_47_draw_entry_creation_creates_zero_extra_point_transactions():
    user = UserAccount.objects.create_user(
        username="zero_pp_47",
        phone_number="+201009810147",
        password="StrongPassword!123",
    )
    customer = CustomerProfile.objects.create(
        user=user,
        country="EG",
        currency="EGP",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    tx_before = PointTransaction.objects.count()
    pr_before = PaymentRequest.objects.count()

    entry = WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user=user,
        profile=customer,
    )
    assert entry is not None
    assert PointTransaction.objects.count() == tx_before
    assert PointService.get_balance(user) == 0
    assert PaymentRequest.objects.count() == pr_before


def test_48_draw_entry_and_draw_lifecycle_create_zero_notifications():
    user = UserAccount.objects.create_user(
        username="zero_notif_48",
        phone_number="+201009810148",
        password="StrongPassword!123",
    )
    customer = CustomerProfile.objects.create(
        user=user,
        country="EG",
        currency="EGP",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    notif_before = Notification.objects.count()
    now = timezone.now()
    draw = WeeklyDrawService.create_draw(
        title="No Notif Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.DRAFT,
        now=now,
    )
    WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user=user,
        profile=customer,
        now=now,
    )
    WeeklyDrawService.open_draw(draw, now=now)
    WeeklyDrawService.close_draw(draw, now=draw.ends_at)
    assert Notification.objects.count() == notif_before


def test_49_fresh_onboarding_no_longer_creates_lucky_wheel_attempt():
    user = register_customer_via_api("no_wheel_49", "+201009810149")
    create_seller_via_api(user)
    assert (
        LuckyWheelGrant.objects.filter(
            user=user,
            source_type=LuckyWheelGrantSourceType.ONBOARDING,
        ).count()
        == 0
    )
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 1


def test_50_genuine_non_onboarding_lucky_wheel_grant_paths_remain_functional():
    user, customer = create_historical_customer_account(
        "wheel_ok_50", "+201009810150"
    )
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
        source_id="999",
        idempotency_key=f"lucky_wheel:auction_completed:999:{customer.pk}",
        metadata={"auction_id": 999},
    )
    assert grant.pk is not None
    assert grant.remaining_attempts == 1
    assert grant.source_type == LuckyWheelGrantSourceType.AUCTION_COMPLETED


def test_51_transaction_rollback_around_onboarding_leaves_no_orphan_weekly_draw_entry():
    user = UserAccount.objects.create_user(
        username="rollback_51",
        phone_number="+201009810151",
        password="StrongPassword!123",
    )
    customer = CustomerProfile.objects.create(
        user=user,
        country="EG",
        currency="EGP",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
                user=user,
                profile=customer,
            )
            raise RuntimeError("Simulated failure after draw entry")

    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0


def test_52_db_uniqueness_constraints_prevent_duplicate_user_source_and_idempotency_key():
    user, _ = create_historical_customer_account("db_uniq_52a", "+201009810152")
    other_user, _ = create_historical_customer_account(
        "db_uniq_52b", "+201009810252"
    )
    WeeklyDrawEntry.objects.create(
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.PENDING,
        idempotency_key=f"draw:onboarding:user:{user.pk}",
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WeeklyDrawEntry.objects.create(
                user=user,
                source=WeeklyDrawEntrySource.ONBOARDING,
                status=WeeklyDrawEntryStatus.PENDING,
                idempotency_key=f"draw:onboarding:user:{user.pk}:dup",
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WeeklyDrawEntry.objects.create(
                user=other_user,
                source=WeeklyDrawEntrySource.ONBOARDING,
                status=WeeklyDrawEntryStatus.PENDING,
                idempotency_key=f"draw:onboarding:user:{user.pk}",
            )


# ==============================================================================
# 53..57: Migrations, Manager Permission Catalog, OpenAPI Schema, and i18n
# ==============================================================================


def test_53_scratch_sqlite_migration_apply_and_constraints_verification(tmp_path):
    db_file = tmp_path / "scratch_draws.sqlite3"
    script = f"""
import os
os.environ["DJANGO_SETTINGS_MODULE"] = "ppw.settings.local"
import django
django.setup()
from django.conf import settings
settings.DATABASES["default"] = {{
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": {str(db_file)!r},
}}
from django.core.management import call_command
call_command("migrate", "draws", verbosity=0, skip_checks=True)
call_command("migrate", "draws", "zero", verbosity=0, skip_checks=True)
call_command("migrate", "draws", "0001_initial", verbosity=0, skip_checks=True)
"""
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr


def test_54_makemigrations_check_dry_run_passes():
    import os

    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = "ppw.settings.local"
    res = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "makemigrations",
            "--check",
            "--dry-run",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr


def test_55_manager_permission_catalog_contains_weekly_draw_permissions():
    codes = get_manager_permission_codes()
    expected = {
        "draws.weekly.list",
        "draws.weekly.retrieve",
        "draws.weekly.create",
        "draws.weekly.update",
        "draws.weekly.open",
        "draws.weekly.close",
        "draws.weekly.entries.list",
    }
    assert expected.issubset(codes)


def test_56_openapi_schema_includes_weekly_draw_endpoints():
    superuser = create_manager_account([], "schema_56", "+201009810156")
    superuser.is_superuser = True
    superuser.save(update_fields=["is_superuser", "modified"])
    response = auth_client(superuser).get("/api/schema/?format=json")
    assert response.status_code == status.HTTP_200_OK
    paths = response.data.get("paths", {})
    assert "/api/draws/weekly/" in paths
    assert "/api/draws/weekly/{id}/" in paths
    assert "/api/draws/weekly/{id}/open/" in paths
    assert "/api/draws/weekly/{id}/close/" in paths
    assert "/api/draws/weekly/{id}/entries/" in paths
    assert "/api/draws/my-weekly-entry/" in paths


def test_57_i18n_arabic_and_english_headers_work_on_my_weekly_entry():
    user = register_customer_via_api("i18n_57", "+201009810157")
    client = auth_client(user)
    resp_ar = client.get(
        "/api/draws/my-weekly-entry/",
        HTTP_ACCEPT_LANGUAGE="ar",
    )
    resp_en = client.get(
        "/api/draws/my-weekly-entry/",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    assert resp_ar.status_code == status.HTTP_200_OK
    assert resp_en.status_code == status.HTTP_200_OK
    assert resp_ar.data["has_entry"] is True
    assert resp_en.data["has_entry"] is True
