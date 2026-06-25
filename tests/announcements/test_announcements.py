import pytest
from pathlib import Path
import tempfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from apps.announcements.models import Announcement
from apps.authentication.services import build_token_for_user
from apps.users.constants import ProfileType
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db
TEST_MEDIA_ROOT = str(Path(tempfile.gettempdir()) / "ppw-test-media")


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def tiny_gif(name="announcement.gif"):
    return SimpleUploadedFile(
        name,
        (
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
            b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
            b"\x00\x02\x02D\x01\x00;"
        ),
        content_type="image/gif",
    )


def create_announcement(title="Announcement", is_active=True):
    return Announcement.objects.create(
        title=title,
        subtitle="Short update",
        details="Full announcement details.",
        image=tiny_gif(f"{title.lower()}.gif"),
        target=ProfileType.CUSTOMER,
        is_active=is_active,
    )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
def test_authenticated_users_can_view_only_active_announcements():
    active = create_announcement("Active")
    create_announcement("Inactive", is_active=False)
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    response = authenticated_client(customer).get("/api/announcements/")

    assert response.status_code == 200
    results = response.data["results"]
    assert [item["id"] for item in results] == [active.pk]
    assert results[0]["title"] == "Active"
    assert results[0]["image_url"]


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
def test_manager_can_view_public_announcements():
    create_announcement("Manager Visible", is_active=True)
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

    response = authenticated_client(manager).get("/api/announcements/")

    assert response.status_code == 200
    assert response.data["results"] == []


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
def test_non_superadmin_cannot_create_admin_announcement():
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

    response = authenticated_client(manager).post(
        "/api/announcements/admin/",
        {
            "title": "New",
            "subtitle": "Short",
            "details": "Details",
            "target": ProfileType.CUSTOMER,
            "image": tiny_gif(),
        },
        format="multipart",
    )

    assert response.status_code == 403
    assert Announcement.objects.count() == 0


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
def test_superadmin_can_create_and_update_admin_announcement():
    superadmin = UserAccountFactory(
        with_profile=ProfileType.MANAGER,
        is_superuser=True,
        is_staff=True,
    )

    create_response = authenticated_client(superadmin).post(
        "/api/announcements/admin/",
        {
            "title": "Launch",
            "subtitle": "Short",
            "details": "Details",
            "target": ProfileType.SELLER,
            "image": tiny_gif(),
        },
        format="multipart",
    )

    assert create_response.status_code == 201
    announcement = Announcement.objects.get()
    assert announcement.created_by_id == superadmin.active_profile.pk
    assert announcement.target == ProfileType.SELLER

    update_response = authenticated_client(superadmin).patch(
        f"/api/announcements/admin/{announcement.pk}/",
        {"is_active": False},
        format="multipart",
    )

    assert update_response.status_code == 200
    announcement.refresh_from_db()
    assert announcement.is_active is False
