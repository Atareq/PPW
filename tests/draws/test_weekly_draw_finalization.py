"""Tests verifying BE-DRW-CANCEL-01: BE-DRW-02 weekly draw finalization and winner selection are cancelled."""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.draws.models import WeeklyDrawEntry
from apps.draws.services import (
    WeeklyDrawEntryService,
    WeeklyDrawFinalizationService,
    WeeklyDrawService,
)
from apps.notifications.models import Notification
from apps.prizes.models import PrizeAward
from apps.users.constants import ProfileType
from apps.users.models import ManagerProfile
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def test_be_drw_02_is_cancelled_by_product_decision_and_creates_no_entries_winners_prizes_or_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    Notification.objects.all().delete()

    assert (
        WeeklyDrawService.WINNER_SELECTION_STATUS_CANCELLED
        == "CANCELLED_BY_PRODUCT_DECISION"
    )
    assert not hasattr(WeeklyDrawService, "finalize_draw")
    assert not hasattr(WeeklyDrawFinalizationService, "finalize_draw")

    entry = WeeklyDrawEntryService.ensure_for_first_profile_onboarding(
        user, profile=user.active_profile
    )
    assert entry is None
    assert WeeklyDrawEntry.objects.count() == 0
    assert PrizeAward.objects.count() == 0
    assert Notification.objects.count() == 0


def test_cancelled_weekly_draw_finalize_route_returns_404():
    superuser = UserAccountFactory(with_profile=False)
    superuser.is_superuser = True
    superuser.is_staff = True
    superuser.save(update_fields=["is_superuser", "is_staff"])
    ManagerProfile.objects.create(
        user=superuser,
        is_active=True,
        activated=True,
        is_valid=True,
        permissions=[],
    )

    client = APIClient()
    refresh = build_token_for_user(superuser)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE="en",
    )

    response = client.post("/api/draws/weekly/1/finalize/", format="json")
    assert response.status_code == status.HTTP_404_NOT_FOUND
