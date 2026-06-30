from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.seasons.models import Season, SeasonStatus
from apps.users.constants import ProfileType
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def auth_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def create_draft_season():
    now = timezone.now()
    return Season.objects.create(
        title="Reward Season",
        starts_at=now - timedelta(days=30),
        ends_at=now,
        status=SeasonStatus.DRAFT,
    )


@pytest.mark.parametrize(
    ("headers", "path", "expected_message"),
    [
        ({}, "/api/orders/cart/items/", "لم يتم العثور على الأصل."),
        ({"HTTP_ACCEPT_LANGUAGE": "ar"}, "/api/orders/cart/items/", "لم يتم العثور على الأصل."),
        ({"HTTP_ACCEPT_LANGUAGE": "en"}, "/api/orders/cart/items/", "Asset was not found."),
        (
            {"HTTP_ACCEPT_LANGUAGE": "ar"},
            "/api/orders/cart/items/?lang=en",
            "Asset was not found.",
        ),
        (
            {"HTTP_ACCEPT_LANGUAGE": "fr"},
            "/api/orders/cart/items/",
            "لم يتم العثور على الأصل.",
        ),
    ],
)
def test_api_locale_selection_for_custom_validation(headers, path, expected_message):
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = auth_client(customer).post(
        path,
        {"asset_id": 999999, "quantity": 1},
        format="json",
        **headers,
    )

    assert response.status_code == 400
    assert str(response.data["asset_id"]) == expected_message


def test_response_detail_uses_active_locale():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    season = create_draft_season()

    response = auth_client(manager).post(
        f"/api/seasons/{season.pk}/finalize/",
        HTTP_ACCEPT_LANGUAGE="ar",
    )

    assert response.status_code == 400
    assert str(response.data["detail"]) == "يمكن إنهاء المواسم المحسوبة فقط."

    response = auth_client(manager).post(
        f"/api/seasons/{season.pk}/finalize/",
        HTTP_ACCEPT_LANGUAGE="en",
    )

    assert response.status_code == 400
    assert str(response.data["detail"]) == "Only calculated seasons can be finalized."


def test_manager_permission_catalog_localizes_labels_without_changing_codes():
    superuser = UserAccountFactory(with_profile=ProfileType.MANAGER)
    superuser.is_staff = True
    superuser.is_superuser = True
    superuser.save(update_fields=["is_staff", "is_superuser"])

    response = auth_client(superuser).get(
        "/api/users/admin/manager-permissions/?lang=ar"
    )

    assert response.status_code == 200
    comments_group = next(
        group for group in response.data["groups"] if group["key"] == "comments"
    )
    delete_permission = next(
        item
        for item in comments_group["permissions"]
        if item["code"] == "comments.delete"
    )

    assert comments_group["key"] == "comments"
    assert comments_group["label"] == "التعليقات"
    assert delete_permission["code"] == "comments.delete"
    assert delete_permission["label"] == "حذف التعليقات"
    assert delete_permission["description"] == "حذف سجلات التعليقات."

    response = auth_client(superuser).get(
        "/api/users/admin/manager-permissions/",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    comments_group = next(
        group for group in response.data["groups"] if group["key"] == "comments"
    )

    assert comments_group["label"] == "Comments"
