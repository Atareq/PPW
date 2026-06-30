from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.loyalty.models import PointTransaction
from apps.seasons.models import Season, SeasonStatus
from apps.users.constants import ProfileType
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    return client


def create_season(status=SeasonStatus.DRAFT):
    now = timezone.now()
    return Season.objects.create(
        title="Reward Season",
        starts_at=now - timedelta(days=30),
        ends_at=now,
        status=status,
    )


def test_manager_can_finalize_calculated_season():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    season = create_season(status=SeasonStatus.CALCULATED)

    response = authenticated_client(manager).post(f"/api/seasons/{season.pk}/finalize/")

    assert response.status_code == 200
    season.refresh_from_db()
    assert season.status == SeasonStatus.FINALIZED
    assert season.finalized_by_id == manager.active_profile.pk
    assert response.data["status"] == SeasonStatus.FINALIZED
    assert response.data["finalized_by"] == manager.active_profile.pk


def test_draft_season_cannot_be_finalized():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    season = create_season(status=SeasonStatus.DRAFT)

    response = authenticated_client(manager).post(f"/api/seasons/{season.pk}/finalize/")

    assert response.status_code == 400
    season.refresh_from_db()
    assert season.status == SeasonStatus.DRAFT
    assert "Only calculated seasons can be finalized" in str(response.data)


def test_finalized_season_cannot_be_finalized_again():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    season = create_season(status=SeasonStatus.FINALIZED)

    response = authenticated_client(manager).post(f"/api/seasons/{season.pk}/finalize/")

    assert response.status_code == 400
    season.refresh_from_db()
    assert season.status == SeasonStatus.FINALIZED
    assert "Only calculated seasons can be finalized" in str(response.data)


def test_finalize_does_not_award_points_or_recalculate_winners():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    season = create_season(status=SeasonStatus.CALCULATED)

    response = authenticated_client(manager).post(f"/api/seasons/{season.pk}/finalize/")

    assert response.status_code == 200
    assert PointTransaction.objects.count() == 0
    assert season.seller_insights.count() == 0
