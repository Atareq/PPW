from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.draws.models import (
    WeeklyDraw,
    WeeklyDrawEffectiveStatus,
    WeeklyDrawEntry,
    WeeklyDrawEntrySource,
    WeeklyDrawEntryStatus,
    WeeklyDrawStatus,
)
from apps.draws.services import (
    WeeklyDrawEntryService,
    WeeklyDrawFinalizationService,
    WeeklyDrawService,
)
from apps.loyalty.constants import RewardSourceType
from apps.loyalty.models import PointTransaction
from apps.loyalty.services import PointService
from apps.lucky_wheel.models import (
    LuckyWheelGrant,
    LuckyWheelSpin,
)
from apps.notifications.models import Notification
from apps.payments.models import PaymentRequest
from apps.promotions.models import OfferTriggerType, UserPromotionGrant
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile

pytestmark = pytest.mark.django_db


def auth_client(user=None, role: str | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        token = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def register_customer_via_api(username: str, phone: str) -> UserAccount:
    payload = {
        "username": username,
        "phone_number": phone,
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


def create_seller_via_api(user: UserAccount, loft_name: str = "Finalization Loft") -> SellerProfile:
    response = auth_client(user).post(
        "/api/users/mobile/sellers/",
        {
            "nickname": f"{user.username}_seller",
            "country": "EG",
            "currency": "EGP",
            "loft_name": loft_name,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    seller = SellerProfile.objects.get(user=user, deleted__isnull=True)
    seller.activated = True
    seller.is_valid = True
    seller.save(update_fields=["activated", "is_valid", "modified"])
    return seller


def create_manager_account(permissions: list[str], username: str, phone: str) -> UserAccount:
    user = UserAccount.objects.create_user(
        username=username,
        phone_number=phone,
        password="StrongPassword!123",
        is_staff=True,
    )
    ManagerProfile.objects.create(
        user=user,
        is_active=True,
        activated=True,
        is_valid=True,
        permissions=list(permissions or []),
    )
    return user


# ==============================================================================
# 1..4: Execution Boundary & Post-Close / Post-Finalize Assignment Blocking
# ==============================================================================


def test_01_cannot_close_or_finalize_before_ends_at():
    now = timezone.now().replace(microsecond=0)
    starts_at = now - timedelta(days=6)
    ends_at = starts_at + timedelta(days=7)
    just_before_end = ends_at - timedelta(microseconds=1)

    draw = WeeklyDrawService.create_draw(
        title="Active Open Draw",
        starts_at=starts_at,
        ends_at=ends_at,
        status=WeeklyDrawStatus.OPEN,
        now=starts_at,
    )

    with pytest.raises(ValidationError) as exc_close:
        WeeklyDrawService.close_draw(draw, now=just_before_end)
    assert "ends_at" in exc_close.value.detail

    with pytest.raises(ValidationError) as exc_finalize:
        WeeklyDrawFinalizationService.finalize_draw(draw, now=just_before_end)
    assert "ends_at" in exc_finalize.value.detail

    with pytest.raises(ValidationError) as exc_patch:
        WeeklyDrawService.update_draw(
            draw,
            {"status": WeeklyDrawStatus.CLOSED},
            now=just_before_end,
        )
    assert "ends_at" in exc_patch.value.detail

    draw.refresh_from_db()
    assert draw.status == WeeklyDrawStatus.OPEN
    assert "execution_freeze" not in draw.metadata


def test_02_exact_boundary_now_equals_ends_at_is_execution_eligible():
    now = timezone.now().replace(microsecond=0)
    starts_at = now - timedelta(days=7)
    ends_at = now

    draw = WeeklyDrawService.create_draw(
        title="Exact End Boundary Draw",
        starts_at=starts_at,
        ends_at=ends_at,
        status=WeeklyDrawStatus.OPEN,
        now=starts_at,
    )
    user = register_customer_via_api("fin_02_user", "+201009820002")
    entry = WeeklyDrawEntry.objects.get(user=user)
    entry.draw = draw
    entry.status = WeeklyDrawEntryStatus.ASSIGNED
    entry.assigned_at = starts_at + timedelta(hours=1)
    entry.save()

    frozen = WeeklyDrawFinalizationService.finalize_draw(draw, now=ends_at)
    assert frozen.status == WeeklyDrawStatus.CLOSED
    assert frozen.effective_status_at(ends_at) == WeeklyDrawEffectiveStatus.CLOSED
    assert frozen.metadata["execution_freeze"]["eligible_entry_ids"] == [entry.pk]
    assert frozen.metadata["execution_freeze"]["eligible_entries_count"] == 1
    assert frozen.metadata["execution_freeze"]["frozen_at"] == ends_at.isoformat()


def test_03_closed_draw_no_longer_receives_pending_or_new_assignments():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Closed Draw No Late Entries",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    WeeklyDrawService.close_draw(draw, now=now)

    late_user = register_customer_via_api("fin_03_late", "+201009820003")
    late_entry = WeeklyDrawEntry.objects.get(user=late_user)
    assert late_entry.status == WeeklyDrawEntryStatus.PENDING
    assert late_entry.draw is None

    assigned = WeeklyDrawService.assign_pending_entries(draw=draw, now=now)
    assert assigned == []
    late_entry.refresh_from_db()
    assert late_entry.status == WeeklyDrawEntryStatus.PENDING
    assert late_entry.draw is None


def test_04_finalized_draw_no_longer_receives_assignments_and_routes_to_next_draw():
    now = timezone.now().replace(microsecond=0)
    finalized_draw = WeeklyDraw.objects.create(
        title="Finalized Historical Draw",
        starts_at=now - timedelta(days=14),
        ends_at=now - timedelta(days=7),
        status=WeeklyDrawStatus.FINALIZED,
    )
    next_draw = WeeklyDrawService.create_draw(
        title="Next Eligible Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )

    assert WeeklyDrawService.assign_pending_entries(draw=finalized_draw, now=now) == []

    new_user = register_customer_via_api("fin_04_next", "+201009820004")
    entry = WeeklyDrawEntry.objects.get(user=new_user)
    assert entry.status == WeeklyDrawEntryStatus.ASSIGNED
    assert entry.draw_id == next_draw.pk
    assert entry.draw_id != finalized_draw.pk


# ==============================================================================
# 5..8: Frozen Participant Set & Zero-Entrant Handling
# ==============================================================================


def test_05_entrant_set_includes_assigned_non_deleted_rows_only():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Entrant Filter Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    u1 = UserAccount.objects.create_user(
        username="fin_05_u1",
        phone_number="+201009820051",
        password="StrongPassword!123",
    )
    u2 = UserAccount.objects.create_user(
        username="fin_05_u2",
        phone_number="+201009820052",
        password="StrongPassword!123",
    )
    e1 = WeeklyDrawEntry.objects.create(
        draw=draw,
        user=u1,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{u1.pk}",
        assigned_at=now - timedelta(days=6),
    )
    e2 = WeeklyDrawEntry.objects.create(
        draw=draw,
        user=u2,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{u2.pk}",
        assigned_at=now - timedelta(days=5),
    )

    eligible_ids = list(
        WeeklyDrawFinalizationService.get_eligible_entries(draw).values_list("id", flat=True)
    )
    assert eligible_ids == [e1.pk, e2.pk]

    frozen = WeeklyDrawFinalizationService.finalize_draw(draw, now=now)
    assert frozen.metadata["execution_freeze"]["eligible_entry_ids"] == [e1.pk, e2.pk]
    assert frozen.metadata["execution_freeze"]["eligible_user_ids"] == [u1.pk, u2.pk]
    assert frozen.metadata["execution_freeze"]["eligible_entries_count"] == 2


def test_06_pending_entry_excluded_from_frozen_participant_set():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Draw With Pending Unassigned",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    assigned_user = UserAccount.objects.create_user(
        username="fin_06_assigned",
        phone_number="+201009820061",
        password="StrongPassword!123",
    )
    pending_user = UserAccount.objects.create_user(
        username="fin_06_pending",
        phone_number="+201009820062",
        password="StrongPassword!123",
    )
    assigned_entry = WeeklyDrawEntry.objects.create(
        draw=draw,
        user=assigned_user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{assigned_user.pk}",
        assigned_at=now - timedelta(days=3),
    )
    pending_entry = WeeklyDrawEntry.objects.create(
        draw=None,
        user=pending_user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.PENDING,
        idempotency_key=f"draw:onboarding:user:{pending_user.pk}",
        assigned_at=None,
    )

    frozen = WeeklyDrawFinalizationService.finalize_draw(draw, now=now)
    freeze_meta = frozen.metadata["execution_freeze"]
    assert freeze_meta["eligible_entry_ids"] == [assigned_entry.pk]
    assert pending_entry.pk not in freeze_meta["eligible_entry_ids"]


def test_07_entry_from_another_draw_excluded_from_frozen_participant_set():
    now = timezone.now().replace(microsecond=0)
    draw_a = WeeklyDrawService.create_draw(
        title="Draw A",
        starts_at=now - timedelta(days=14),
        ends_at=now - timedelta(days=7),
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=14),
    )
    draw_b = WeeklyDrawService.create_draw(
        title="Draw B",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    user_a = UserAccount.objects.create_user(
        username="fin_07_ua",
        phone_number="+201009820071",
        password="StrongPassword!123",
    )
    user_b = UserAccount.objects.create_user(
        username="fin_07_ub",
        phone_number="+201009820072",
        password="StrongPassword!123",
    )
    entry_a = WeeklyDrawEntry.objects.create(
        draw=draw_a,
        user=user_a,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user_a.pk}",
        assigned_at=now - timedelta(days=10),
    )
    entry_b = WeeklyDrawEntry.objects.create(
        draw=draw_b,
        user=user_b,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user_b.pk}",
        assigned_at=now - timedelta(days=3),
    )

    frozen_a = WeeklyDrawFinalizationService.finalize_draw(draw_a, now=now)
    assert frozen_a.metadata["execution_freeze"]["eligible_entry_ids"] == [entry_a.pk]
    assert entry_b.pk not in frozen_a.metadata["execution_freeze"]["eligible_entry_ids"]


def test_08_zero_entrant_draw_closes_and_freezes_safely_with_zero_winners():
    now = timezone.now().replace(microsecond=0)
    empty_draw = WeeklyDrawService.create_draw(
        title="Empty Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )

    frozen = WeeklyDrawFinalizationService.finalize_draw(empty_draw, now=now)
    assert frozen.status == WeeklyDrawStatus.CLOSED
    freeze_meta = frozen.metadata["execution_freeze"]
    assert freeze_meta["eligible_entry_ids"] == []
    assert freeze_meta["eligible_user_ids"] == []
    assert freeze_meta["eligible_entries_count"] == 0
    assert freeze_meta["winners_selected_count"] == 0
    assert (
        freeze_meta["winner_selection_status"]
        == WeeklyDrawService.WINNER_SELECTION_STATUS_BLOCKED
    )


# ==============================================================================
# 9..11: Schedule & Entry Immutability / Deletion Protection After Close/Freeze
# ==============================================================================


def test_09_schedule_and_draw_fields_immutable_after_close_or_freeze():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Immutable Closed Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    with pytest.raises(ValidationError):
        WeeklyDrawService.update_draw(
            draw,
            {"starts_at": now - timedelta(days=8)},
            now=now,
        )
    with pytest.raises(ValidationError):
        WeeklyDrawService.update_draw(
            draw,
            {"ends_at": now + timedelta(days=1)},
            now=now,
        )
    with pytest.raises(ValidationError):
        WeeklyDrawService.open_draw(draw, now=now)

    # Model-level reopening is also rejected
    draw.refresh_from_db()
    draw.status = WeeklyDrawStatus.OPEN
    with pytest.raises(DjangoValidationError):
        draw.save()


def test_10_assigned_entries_cannot_move_or_mutate_after_close():
    now = timezone.now().replace(microsecond=0)
    draw_1 = WeeklyDrawService.create_draw(
        title="Closed Draw 1",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    draw_2 = WeeklyDrawService.create_draw(
        title="Upcoming Draw 2",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    user = UserAccount.objects.create_user(
        username="fin_10_user",
        phone_number="+201009820101",
        password="StrongPassword!123",
    )
    entry = WeeklyDrawEntry.objects.create(
        draw=draw_1,
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user.pk}",
        assigned_at=now - timedelta(days=2),
    )
    WeeklyDrawFinalizationService.finalize_draw(draw_1, now=now)

    # Attempting to move entry to draw_2 fails
    entry.refresh_from_db()
    entry.draw = draw_2
    with pytest.raises(DjangoValidationError):
        entry.save()

    # Attempting to revert status to PENDING fails
    entry.refresh_from_db()
    entry.status = WeeklyDrawEntryStatus.PENDING
    with pytest.raises(DjangoValidationError):
        entry.save()

    # Attempting to mutate metadata after draw is CLOSED fails
    entry.refresh_from_db()
    entry.metadata = {"tampered": True}
    with pytest.raises(DjangoValidationError):
        entry.save()


def test_11_assigned_entries_and_closed_draws_cannot_be_destructively_deleted():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Protected Closed Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    user = UserAccount.objects.create_user(
        username="fin_11_user",
        phone_number="+201009820111",
        password="StrongPassword!123",
    )
    entry = WeeklyDrawEntry.objects.create(
        draw=draw,
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user.pk}",
        assigned_at=now - timedelta(days=2),
    )

    with pytest.raises(ProtectedError):
        entry.delete()

    WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    with pytest.raises(ProtectedError):
        entry.delete()
    with pytest.raises(ProtectedError):
        draw.delete()

    # Even an empty CLOSED draw is protected from deletion
    empty_closed = WeeklyDrawService.create_draw(
        title="Empty Closed Protected",
        starts_at=now - timedelta(days=14),
        ends_at=now - timedelta(days=7),
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=14),
    )
    WeeklyDrawFinalizationService.finalize_draw(empty_closed, now=now)
    with pytest.raises(ProtectedError):
        empty_closed.delete()


# ==============================================================================
# 12..14: Idempotency, Atomic Rollback, and select_for_update Locking
# ==============================================================================


def test_12_two_finalize_attempts_are_idempotent_and_preserve_frozen_snapshot():
    now = timezone.now().replace(microsecond=0)
    mgr_1 = create_manager_account(["draws.weekly.finalize"], "fin_12_m1", "+201009820121")
    mgr_2 = create_manager_account(["draws.weekly.finalize"], "fin_12_m2", "+201009820122")

    draw = WeeklyDrawService.create_draw(
        title="Idempotent Finalize Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    user = UserAccount.objects.create_user(
        username="fin_12_u1",
        phone_number="+201009820123",
        password="StrongPassword!123",
    )
    entry = WeeklyDrawEntry.objects.create(
        draw=draw,
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user.pk}",
        assigned_at=now - timedelta(days=3),
    )

    first = WeeklyDrawFinalizationService.finalize_draw(
        draw,
        finalized_by=mgr_1.active_profile,
        now=now,
    )
    first_freeze = dict(first.metadata["execution_freeze"])
    first_modified = first.modified

    later = now + timedelta(hours=2)
    second = WeeklyDrawFinalizationService.finalize_draw(
        draw,
        finalized_by=mgr_2.active_profile,
        now=later,
    )
    assert second.status == WeeklyDrawStatus.CLOSED
    assert second.metadata["execution_freeze"] == first_freeze
    assert second.metadata["execution_freeze"]["frozen_at"] == now.isoformat()
    assert (
        second.metadata["execution_freeze"]["frozen_by_profile_id"]
        == mgr_1.active_profile.pk
    )
    assert second.metadata["execution_freeze"]["eligible_entry_ids"] == [entry.pk]
    assert second.modified == first_modified


def test_13_finalize_uses_transaction_atomic_and_rolls_back_on_error():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Atomic Rollback Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )

    with patch.object(WeeklyDraw, "save", side_effect=RuntimeError("DB write failure")):
        with pytest.raises(RuntimeError, match="DB write failure"):
            WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    draw.refresh_from_db()
    assert draw.status == WeeklyDrawStatus.OPEN
    assert "execution_freeze" not in draw.metadata

    # Subsequent clean retry succeeds once
    frozen = WeeklyDrawFinalizationService.finalize_draw(draw, now=now)
    assert frozen.status == WeeklyDrawStatus.CLOSED
    assert "execution_freeze" in frozen.metadata


def test_14_finalize_and_close_lock_draw_row_with_select_for_update():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Locked Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )

    original_select_for_update = WeeklyDraw.objects.select_for_update
    calls = []

    def tracked_select_for_update(*args, **kwargs):
        calls.append(True)
        return original_select_for_update(*args, **kwargs)

    with patch.object(
        type(WeeklyDraw.objects),
        "select_for_update",
        side_effect=tracked_select_for_update,
    ):
        WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    assert len(calls) >= 1


# ==============================================================================
# 15..17: API Authorization & Manager Permissions
# ==============================================================================


def test_15_customer_cannot_finalize_weekly_draw():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Customer Forbidden Finalize",
        starts_at=now - timedelta(days=7),
        ends_at=now - timedelta(seconds=1),
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    customer = register_customer_via_api("fin_15_cust", "+201009820151")
    response = auth_client(customer, role="Customer").post(
        f"/api/draws/weekly/{draw.pk}/finalize/"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    draw.refresh_from_db()
    assert draw.status == WeeklyDrawStatus.OPEN


def test_16_seller_cannot_finalize_weekly_draw():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Seller Forbidden Finalize",
        starts_at=now - timedelta(days=7),
        ends_at=now - timedelta(seconds=1),
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    user = register_customer_via_api("fin_16_seller", "+201009820161")
    seller = create_seller_via_api(user)
    CustomerProfile.objects.filter(user=user).update(is_active=False)
    SellerProfile.objects.filter(pk=seller.pk).update(
        activated=True,
        is_valid=True,
        is_active=True,
    )

    response = auth_client(user, role="Seller").post(
        f"/api/draws/weekly/{draw.pk}/finalize/"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    draw.refresh_from_db()
    assert draw.status == WeeklyDrawStatus.OPEN


def test_17_manager_requires_exact_draws_weekly_finalize_permission():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Manager Finalize Permission Check",
        starts_at=now - timedelta(days=7),
        ends_at=now - timedelta(seconds=1),
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    wrong_mgr = create_manager_account(
        ["draws.weekly.list", "draws.weekly.close", "draws.weekly.open"],
        "fin_17_wrong",
        "+201009820171",
    )
    right_mgr = create_manager_account(
        ["draws.weekly.finalize"],
        "fin_17_right",
        "+201009820172",
    )

    denied = auth_client(wrong_mgr).post(f"/api/draws/weekly/{draw.pk}/finalize/")
    assert denied.status_code == status.HTTP_403_FORBIDDEN

    allowed = auth_client(right_mgr).post(f"/api/draws/weekly/{draw.pk}/finalize/")
    assert allowed.status_code == status.HTTP_200_OK, allowed.data
    assert allowed.data["status"] == WeeklyDrawStatus.CLOSED
    assert allowed.data["is_participant_set_frozen"] is True
    assert (
        allowed.data["winner_selection_status"]
        == WeeklyDrawService.WINNER_SELECTION_STATUS_BLOCKED
    )
    assert (
        allowed.data["metadata"]["execution_freeze"]["frozen_by_profile_id"]
        == right_mgr.active_profile.pk
    )


# ==============================================================================
# 18..21: Zero Economic / Notification / Lucky Wheel / Payment Side Effects
# ==============================================================================


def test_18_finalizing_draw_creates_zero_point_transactions():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="No PP On Finalize",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    user = UserAccount.objects.create_user(
        username="fin_18_u",
        phone_number="+201009820181",
        password="StrongPassword!123",
    )
    WeeklyDrawEntry.objects.create(
        draw=draw,
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user.pk}",
        assigned_at=now - timedelta(days=2),
    )
    tx_before = PointTransaction.objects.count()

    WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    assert PointTransaction.objects.count() == tx_before
    assert PointService.get_balance(user) == 0


def test_19_finalizing_draw_creates_zero_notifications():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="No Notification On Finalize",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    user = UserAccount.objects.create_user(
        username="fin_19_u",
        phone_number="+201009820191",
        password="StrongPassword!123",
    )
    WeeklyDrawEntry.objects.create(
        draw=draw,
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user.pk}",
        assigned_at=now - timedelta(days=2),
    )
    notif_before = Notification.objects.count()

    WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    assert Notification.objects.count() == notif_before


def test_20_finalizing_draw_creates_zero_lucky_wheel_grants_or_spins():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="No Lucky Wheel On Finalize",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    user = UserAccount.objects.create_user(
        username="fin_20_u",
        phone_number="+201009820201",
        password="StrongPassword!123",
    )
    WeeklyDrawEntry.objects.create(
        draw=draw,
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        idempotency_key=f"draw:onboarding:user:{user.pk}",
        assigned_at=now - timedelta(days=2),
    )
    grants_before = LuckyWheelGrant.objects.count()
    spins_before = LuckyWheelSpin.objects.count()

    WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    assert LuckyWheelGrant.objects.count() == grants_before
    assert LuckyWheelSpin.objects.count() == spins_before


def test_21_finalizing_draw_creates_zero_payment_requests():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="No PaymentRequest On Finalize",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    pr_before = PaymentRequest.objects.count()

    WeeklyDrawFinalizationService.finalize_draw(draw, now=now)

    assert PaymentRequest.objects.count() == pr_before


# ==============================================================================
# 22..26: Regression & Status Rule Verification
# ==============================================================================


def test_22_pending_entries_skip_closed_draw_and_assign_to_next_open_draw():
    now = timezone.now().replace(microsecond=0)
    ended_draw = WeeklyDrawService.create_draw(
        title="Ended Draw",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    WeeklyDrawFinalizationService.finalize_draw(ended_draw, now=now)

    user = register_customer_via_api("fin_22_pending", "+201009820221")
    entry = WeeklyDrawEntry.objects.get(user=user)
    assert entry.status == WeeklyDrawEntryStatus.PENDING
    assert entry.draw is None

    next_draw = WeeklyDrawService.create_draw(
        title="Next Open Draw",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=8),
        status=WeeklyDrawStatus.OPEN,
        now=now,
    )
    entry.refresh_from_db()
    assert entry.status == WeeklyDrawEntryStatus.ASSIGNED
    assert entry.draw_id == next_draw.pk


def test_23_100_pp_onboarding_reward_remains_unchanged():
    user = register_customer_via_api("fin_23_pp", "+201009820231")
    assert PointService.get_balance(user) == 100
    assert (
        PointTransaction.objects.filter(
            user=user,
            source_type=RewardSourceType.ONBOARDING,
        ).count()
        == 1
    )


def test_24_20_percent_onboarding_highlight_benefit_remains_unchanged():
    from decimal import Decimal

    user = register_customer_via_api("fin_24_hl", "+201009820241")
    grant = UserPromotionGrant.objects.select_related("discount_offer").get(
        user=user,
        source_type=OfferTriggerType.ONBOARDING,
    )
    assert grant.discount_offer is not None
    assert grant.discount_offer.value == Decimal("20.00")


def test_25_historical_account_no_backfill_remains_unchanged():
    hist_user = UserAccount.objects.create_user(
        username="fin_25_hist",
        phone_number="+201009820251",
        password="StrongPassword!123",
    )
    CustomerProfile.objects.create(
        user=hist_user,
        country="EG",
        currency="EGP",
        is_active=True,
        activated=True,
        is_valid=True,
    )
    assert WeeklyDrawEntry.objects.filter(user=hist_user).count() == 0

    create_seller_via_api(hist_user)
    assert WeeklyDrawEntry.objects.filter(user=hist_user).count() == 0
    assert UserPromotionGrant.objects.filter(user=hist_user).count() == 0


def test_26_status_rule_does_not_misuse_finalized_while_winner_selection_is_blocked():
    now = timezone.now().replace(microsecond=0)
    draw = WeeklyDrawService.create_draw(
        title="Blocked Winner Selection Gate",
        starts_at=now - timedelta(days=7),
        ends_at=now,
        status=WeeklyDrawStatus.OPEN,
        now=now - timedelta(days=7),
    )
    result = WeeklyDrawFinalizationService.finalize_draw(draw, now=now)
    assert result.status == WeeklyDrawStatus.CLOSED
    assert result.status != WeeklyDrawStatus.FINALIZED
    assert (
        result.metadata["execution_freeze"]["winner_selection_status"]
        == "BLOCKED_PENDING_PRODUCT_DECISION"
    )
    assert result.metadata["execution_freeze"]["winners_selected_count"] == 0
