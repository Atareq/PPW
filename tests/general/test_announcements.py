from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.utils import timezone, translation
import pytest
from rest_framework import status
from rest_framework.test import APIClient
import yaml

from apps.authentication.services import (
    build_token_for_user,
    switch_user_profile,
)
from apps.general.models import (
    AdminAnnouncement,
    AnnouncementDismissal,
    AnnouncementDurationUnit,
)
from apps.notifications.models import Notification
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def auth_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    return client


def create_manager_with_permissions(permissions):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager_profile = ManagerProfile.objects.get(pk=user.active_profile.pk)
    manager_profile.permissions = list(permissions)
    manager_profile.save(update_fields=["permissions"])
    return user


def create_dual_profile_user(initial_active=ProfileType.CUSTOMER):
    user = UserAccountFactory(with_profile=initial_active)
    if initial_active == ProfileType.CUSTOMER:
        SellerProfile.objects.create(
            user=user,
            nickname=f"{user.username}_seller",
            country=CountryOptions.EGYPT,
            currency=CurrencyOptions.EGP,
            is_active=False,
            activated=True,
            is_valid=True,
            recoded_voice="voice.mp3",
        )
    else:
        CustomerProfile.objects.create(
            user=user,
            nickname=f"{user.username}_customer",
            country=CountryOptions.EGYPT,
            currency=CurrencyOptions.EGP,
            is_active=False,
            activated=True,
            is_valid=True,
        )
    return user


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


def test_01_authorized_manager_creates_announcement_with_expires_at_and_duration():
    manager = create_manager_with_permissions(["announcements.create"])
    client = auth_client(manager)
    now = timezone.now()
    expires_at = now + timedelta(hours=6)

    response = client.post(
        "/api/announcements/admin/",
        {
            "text": "Platform maintenance at 02:00 UTC.",
            "starts_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    ann = AdminAnnouncement.objects.get(pk=response.data["id"])
    assert ann.text == "Platform maintenance at 02:00 UTC."
    assert ann.starts_at == now
    assert ann.expires_at == expires_at
    assert ann.is_active is True
    assert ann.created_by_id == manager.active_profile.pk

    # Create using duration_value + duration_unit (hours and days)
    resp_hours = client.post(
        "/api/announcements/admin/",
        {
            "text": "Flash event for 12 hours!",
            "starts_at": now.isoformat(),
            "duration_value": 12,
            "duration_unit": AnnouncementDurationUnit.HOURS,
        },
        format="json",
    )
    assert resp_hours.status_code == status.HTTP_201_CREATED
    ann_hours = AdminAnnouncement.objects.get(pk=resp_hours.data["id"])
    assert ann_hours.expires_at == now + timedelta(hours=12)

    resp_days = client.post(
        "/api/announcements/admin/",
        {
            "text": "Weekend special for 3 days!",
            "starts_at": now.isoformat(),
            "duration_value": 3,
            "duration_unit": AnnouncementDurationUnit.DAYS,
        },
        format="json",
    )
    assert resp_days.status_code == status.HTTP_201_CREATED
    ann_days = AdminAnnouncement.objects.get(pk=resp_days.data["id"])
    assert ann_days.expires_at == now + timedelta(days=3)


def test_02_unauthorized_user_cannot_create_or_manage_announcements():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    unprivileged_manager = create_manager_with_permissions(["announcements.list"])
    anonymous = APIClient()

    now = timezone.now()
    announcement = AdminAnnouncement.objects.create(
        text="Existing announcement",
        starts_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=5),
    )

    payload = {
        "text": "Unauthorized announcement",
        "duration_value": 4,
        "duration_unit": "hours",
    }

    assert (
        anonymous.post("/api/announcements/admin/", payload, format="json").status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    for user in (customer, seller, unprivileged_manager):
        c = auth_client(user)
        assert (
            c.post("/api/announcements/admin/", payload, format="json").status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.patch(
                f"/api/announcements/admin/{announcement.pk}/",
                {"text": "Tampered"},
                format="json",
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.delete(f"/api/announcements/admin/{announcement.pk}/").status_code
            == status.HTTP_403_FORBIDDEN
        )

    for user in (customer, seller):
        c = auth_client(user)
        assert (
            c.get("/api/announcements/admin/").status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            c.get(f"/api/announcements/admin/{announcement.pk}/").status_code
            == status.HTTP_403_FORBIDDEN
        )


def test_03_active_announcement_returned_to_user():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    active_ann = AdminAnnouncement.objects.create(
        text="Live auction event now open!",
        starts_at=now - timedelta(minutes=10),
        expires_at=now + timedelta(hours=2),
        is_active=True,
    )

    response = auth_client(customer).get("/api/announcements/current/")
    assert response.status_code == status.HTTP_200_OK
    results = response_results(response)
    assert len(results) == 1
    assert results[0]["id"] == active_ann.id
    assert results[0]["text"] == "Live auction event now open!"
    assert "created_by" not in results[0]


def test_04_future_announcement_not_returned_before_start_time():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    AdminAnnouncement.objects.create(
        text="Future scheduled announcement",
        starts_at=now + timedelta(hours=2),
        expires_at=now + timedelta(hours=6),
        is_active=True,
    )

    response = auth_client(customer).get("/api/announcements/current/")
    assert response.status_code == status.HTTP_200_OK
    assert response_results(response) == []


def test_05_expired_and_inactive_announcements_not_returned_to_user():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    AdminAnnouncement.objects.create(
        text="Expired announcement",
        starts_at=now - timedelta(hours=5),
        expires_at=now - timedelta(seconds=1),
        is_active=True,
    )
    AdminAnnouncement.objects.create(
        text="Inactive announcement",
        starts_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=5),
        is_active=False,
    )

    response = auth_client(customer).get("/api/announcements/current/")
    assert response.status_code == status.HTTP_200_OK
    assert response_results(response) == []


def test_06_07_user_dismisses_active_announcement_and_it_disappears_for_that_user():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    ann = AdminAnnouncement.objects.create(
        text="Dismissable announcement",
        starts_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=4),
    )

    client = auth_client(customer)
    before = response_results(client.get("/api/announcements/current/"))
    assert [item["id"] for item in before] == [ann.id]

    dismiss_resp = client.post(f"/api/announcements/{ann.id}/dismiss/")
    assert dismiss_resp.status_code == status.HTTP_200_OK
    assert dismiss_resp.data["announcement_id"] == ann.id
    assert AnnouncementDismissal.objects.filter(
        announcement=ann,
        user=customer,
    ).exists()

    after = response_results(client.get("/api/announcements/current/"))
    assert after == []


def test_08_dismissed_announcement_remains_visible_to_another_user():
    user_a = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_b = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    ann = AdminAnnouncement.objects.create(
        text="Shared announcement",
        starts_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=4),
    )

    resp_dismiss = auth_client(user_a).post(f"/api/announcements/{ann.id}/dismiss/")
    assert resp_dismiss.status_code == status.HTTP_200_OK

    assert response_results(auth_client(user_a).get("/api/announcements/current/")) == []
    user_b_results = response_results(
        auth_client(user_b).get("/api/announcements/current/")
    )
    assert [item["id"] for item in user_b_results] == [ann.id]


def test_09_repeated_dismiss_is_idempotent():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    ann = AdminAnnouncement.objects.create(
        text="Idempotent dismiss announcement",
        starts_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=4),
    )

    client = auth_client(user)
    first = client.post(f"/api/announcements/{ann.id}/dismiss/")
    second = client.post(f"/api/announcements/{ann.id}/dismiss/")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.data["id"] == second.data["id"]
    assert first.data["dismissed_at"] == second.data["dismissed_at"]
    assert (
        AnnouncementDismissal.all_objects.filter(
            announcement=ann,
            user=user,
        ).count()
        == 1
    )


def test_10_11_dismissal_belongs_to_user_account_and_survives_customer_seller_profile_switch():
    user = create_dual_profile_user(initial_active=ProfileType.CUSTOMER)
    now = timezone.now()
    ann = AdminAnnouncement.objects.create(
        text="Cross-profile announcement",
        starts_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=4),
    )

    customer_client = auth_client(user)
    assert [
        item["id"]
        for item in response_results(
            customer_client.get("/api/announcements/current/")
        )
    ] == [ann.id]

    # Dismiss while in Customer mode
    dismiss_resp = customer_client.post(f"/api/announcements/{ann.id}/dismiss/")
    assert dismiss_resp.status_code == status.HTTP_200_OK

    dismissal = AnnouncementDismissal.objects.get(announcement=ann, user=user)
    assert dismissal.user_id == user.id
    assert not hasattr(dismissal, "profile_id")

    # Switch active profile from Customer to Seller
    switched, new_role = switch_user_profile(
        user,
        new_profile_type=ProfileType.SELLER,
    )
    assert switched is True
    assert new_role == ProfileType.SELLER
    user.refresh_from_db()
    assert user.active_profile.type == ProfileType.SELLER

    seller_client = auth_client(user)
    seller_results = response_results(
        seller_client.get("/api/announcements/current/")
    )
    assert seller_results == []


def test_12_13_republished_new_announcement_appears_while_old_announcement_remains_dismissed():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    manager = create_manager_with_permissions(["announcements.create"])
    now = timezone.now()

    old_ann = AdminAnnouncement.objects.create(
        text="Important notice v1",
        starts_at=now - timedelta(hours=2),
        expires_at=now + timedelta(hours=2),
    )

    user_client = auth_client(user)
    assert (
        user_client.post(f"/api/announcements/{old_ann.id}/dismiss/").status_code
        == status.HTTP_200_OK
    )
    assert response_results(user_client.get("/api/announcements/current/")) == []

    # Admin republishes by creating a new announcement publication
    republish_resp = auth_client(manager).post(
        "/api/announcements/admin/",
        {
            "text": "Important notice v1",
            "duration_value": 24,
            "duration_unit": "hours",
        },
        format="json",
    )
    assert republish_resp.status_code == status.HTTP_201_CREATED
    new_ann_id = republish_resp.data["id"]
    assert new_ann_id != old_ann.id

    current_results = response_results(
        user_client.get("/api/announcements/current/")
    )
    assert [item["id"] for item in current_results] == [new_ann_id]
    assert AnnouncementDismissal.objects.filter(
        announcement=old_ann,
        user=user,
    ).exists()
    assert not AnnouncementDismissal.objects.filter(
        announcement_id=new_ann_id,
        user=user,
    ).exists()


def test_14_admin_can_see_expired_and_scheduled_records_in_management_api():
    manager = create_manager_with_permissions(
        [
            "announcements.list",
            "announcements.retrieve",
            "announcements.update",
            "announcements.delete",
        ]
    )
    now = timezone.now()
    expired_ann = AdminAnnouncement.objects.create(
        text="Past announcement",
        starts_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )
    active_ann = AdminAnnouncement.objects.create(
        text="Current announcement",
        starts_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=5),
    )
    future_ann = AdminAnnouncement.objects.create(
        text="Future announcement",
        starts_at=now + timedelta(hours=2),
        expires_at=now + timedelta(hours=8),
    )

    client = auth_client(manager)
    list_resp = client.get("/api/announcements/admin/")
    assert list_resp.status_code == status.HTTP_200_OK
    returned_ids = {item["id"] for item in response_results(list_resp)}
    assert {expired_ann.id, active_ann.id, future_ann.id}.issubset(returned_ids)

    detail_resp = client.get(f"/api/announcements/admin/{expired_ann.id}/")
    assert detail_resp.status_code == status.HTTP_200_OK
    assert detail_resp.data["id"] == expired_ann.id

    patch_resp = client.patch(
        f"/api/announcements/admin/{active_ann.id}/",
        {"text": "Updated announcement text", "is_active": False},
        format="json",
    )
    assert patch_resp.status_code == status.HTTP_200_OK
    active_ann.refresh_from_db()
    assert active_ann.text == "Updated announcement text"
    assert active_ann.is_active is False

    delete_resp = client.delete(f"/api/announcements/admin/{active_ann.id}/")
    assert delete_resp.status_code == status.HTTP_204_NO_CONTENT
    assert not AdminAnnouncement.objects.filter(pk=active_ann.id).exists()
    assert (
        AdminAnnouncement.all_objects.get(pk=active_ann.id).deleted is not None
    )


def test_15_client_cannot_submit_another_user_id_or_profile_id_for_dismissal():
    attacker = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    victim = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    ann = AdminAnnouncement.objects.create(
        text="Target announcement",
        starts_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=4),
    )

    client = auth_client(attacker)
    for forbidden_payload in (
        {"user_id": victim.id},
        {"user": victim.id},
        {"profile_id": victim.active_profile.id},
        {"profile": victim.active_profile.id},
    ):
        resp = client.post(
            f"/api/announcements/{ann.id}/dismiss/",
            forbidden_payload,
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    assert not AnnouncementDismissal.objects.filter(
        announcement=ann,
        user=victim,
    ).exists()
    assert not AnnouncementDismissal.objects.filter(
        announcement=ann,
        user=attacker,
    ).exists()


def test_16_17_no_notification_row_or_push_or_websocket_task_triggered():
    manager = create_manager_with_permissions(["announcements.create"])
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    with (
        patch("apps.notifications.tasks.deliver_notification_push.delay") as mock_push,
        patch("apps.notifications.websocket.broadcast_notification") as mock_ws,
    ):
        create_resp = auth_client(manager).post(
            "/api/announcements/admin/",
            {
                "text": "Silent banner announcement",
                "duration_value": 6,
                "duration_unit": "hours",
            },
            format="json",
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        ann_id = create_resp.data["id"]

        current_resp = auth_client(customer).get("/api/announcements/current/")
        assert current_resp.status_code == status.HTTP_200_OK

        dismiss_resp = auth_client(customer).post(
            f"/api/announcements/{ann_id}/dismiss/"
        )
        assert dismiss_resp.status_code == status.HTTP_200_OK

        assert Notification.objects.count() == 0
        mock_push.assert_not_called()
        mock_ws.assert_not_called()


def test_18_expiry_requires_no_scheduled_job_and_happens_strictly_by_time_passing():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    t0 = timezone.now()
    ann = AdminAnnouncement.objects.create(
        text="Time-bound announcement",
        starts_at=t0 - timedelta(hours=1),
        expires_at=t0 + timedelta(minutes=10),
        is_active=True,
    )

    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert "announcement" not in entry["task"].lower()

    client = auth_client(customer)
    with patch("apps.general.services.timezone.now", return_value=t0):
        visible = response_results(client.get("/api/announcements/current/"))
        assert [item["id"] for item in visible] == [ann.id]

    after_expiry = t0 + timedelta(minutes=10)
    with patch("apps.general.services.timezone.now", return_value=after_expiry):
        expired = response_results(client.get("/api/announcements/current/"))
        assert expired == []

    # Row itself was not mutated by any background task
    ann.refresh_from_db()
    assert ann.is_active is True


def test_duration_and_schedule_validation_rejects_invalid_inputs():
    manager = create_manager_with_permissions(["announcements.create"])
    client = auth_client(manager)
    now = timezone.now()

    # Unsupported duration unit (e.g. weeks)
    resp_weeks = client.post(
        "/api/announcements/admin/",
        {
            "text": "Invalid unit",
            "duration_value": 1,
            "duration_unit": "weeks",
        },
        format="json",
    )
    assert resp_weeks.status_code == status.HTTP_400_BAD_REQUEST
    assert "duration_unit" in resp_weeks.data

    # Both expires_at and duration supplied
    resp_both = client.post(
        "/api/announcements/admin/",
        {
            "text": "Ambiguous expiry",
            "expires_at": (now + timedelta(hours=2)).isoformat(),
            "duration_value": 2,
            "duration_unit": "hours",
        },
        format="json",
    )
    assert resp_both.status_code == status.HTTP_400_BAD_REQUEST
    assert "expires_at" in resp_both.data

    # Neither expires_at nor duration supplied on create
    resp_neither = client.post(
        "/api/announcements/admin/",
        {"text": "Missing expiry"},
        format="json",
    )
    assert resp_neither.status_code == status.HTTP_400_BAD_REQUEST
    assert "expires_at" in resp_neither.data

    # expires_at <= starts_at
    resp_inverted = client.post(
        "/api/announcements/admin/",
        {
            "text": "Inverted window",
            "starts_at": now.isoformat(),
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
        },
        format="json",
    )
    assert resp_inverted.status_code == status.HTTP_400_BAD_REQUEST
    assert "expires_at" in resp_inverted.data


def test_dismiss_nonexistent_or_deleted_announcement_returns_404():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    ann = AdminAnnouncement.objects.create(
        text="Soon deleted",
        starts_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=2),
    )
    ann.delete()

    client = auth_client(customer)
    assert (
        client.post("/api/announcements/999999/dismiss/").status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert (
        client.post(f"/api/announcements/{ann.id}/dismiss/").status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_openapi_schema_documents_announcement_endpoints_without_public_dismissal_list(
    authenticated_client,
    settings,
):
    settings.DEBUG = False
    response = authenticated_client.get("/api/schema/")
    assert response.status_code == status.HTTP_200_OK
    schema = yaml.safe_load(response.content.decode())

    assert "/api/announcements/current/" in schema["paths"]
    assert "/api/announcements/{id}/dismiss/" in schema["paths"]
    assert "/api/announcements/admin/" in schema["paths"]
    assert "/api/announcements/admin/{id}/" in schema["paths"]

    current_op = schema["paths"]["/api/announcements/current/"]["get"]
    dismiss_op = schema["paths"]["/api/announcements/{id}/dismiss/"]["post"]
    admin_op = schema["paths"]["/api/announcements/admin/"]["post"]

    assert current_op["tags"] == ["Announcements"]
    assert dismiss_op["tags"] == ["Announcements"]
    assert admin_op["tags"] == ["Announcements"]
    assert "requestBody" not in dismiss_op

    current_schema = schema["components"]["schemas"]["CurrentAnnouncement"]
    assert "dismissals" not in current_schema["properties"]


@pytest.mark.django_db(transaction=True)
@override_settings(MIGRATION_MODULES={})
def test_migrations_apply_rollback_reapply_and_check():
    with translation.override("en"):
        call_command("makemigrations", "general", "--check", "--dry-run")

    alias = "general_announcement_migration_test"
    database_config = deepcopy(settings.DATABASES["default"])
    database_config["NAME"] = ":memory:"
    database_config["TEST"] = {"NAME": ":memory:"}
    connections.databases[alias] = database_config
    conn = connections[alias]

    try:
        target_after = [("general", "0002_admin_announcements")]
        target_before = [("general", "0001_initial")]

        executor = MigrationExecutor(conn)
        executor.migrate(target_after)
        tables_applied = conn.introspection.table_names()
        assert "general_adminannouncement" in tables_applied
        assert "general_announcementdismissal" in tables_applied

        executor = MigrationExecutor(conn)
        executor.migrate(target_before)
        tables_rolled_back = conn.introspection.table_names()
        assert "general_adminannouncement" not in tables_rolled_back
        assert "general_announcementdismissal" not in tables_rolled_back

        executor = MigrationExecutor(conn)
        executor.migrate(target_after)
        tables_reapplied = conn.introspection.table_names()
        assert "general_adminannouncement" in tables_reapplied
        assert "general_announcementdismissal" in tables_reapplied
    finally:
        conn.close()
        connections.databases.pop(alias, None)


def test_i18n_arabic_and_english_localization_for_announcements():
    superuser = UserAccountFactory(with_profile=ProfileType.MANAGER)
    superuser.is_staff = True
    superuser.is_superuser = True
    superuser.save(update_fields=["is_staff", "is_superuser"])

    client = APIClient()
    refresh = build_token_for_user(superuser)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    ar_catalog = client.get("/api/users/admin/manager-permissions/?lang=ar")
    assert ar_catalog.status_code == status.HTTP_200_OK
    ann_group_ar = next(
        group
        for group in ar_catalog.data["groups"]
        if group["key"] == "announcements"
    )
    assert ann_group_ar["label"] == "الإعلانات"

    en_catalog = client.get("/api/users/admin/manager-permissions/?lang=en")
    assert en_catalog.status_code == status.HTTP_200_OK
    ann_group_en = next(
        group
        for group in en_catalog.data["groups"]
        if group["key"] == "announcements"
    )
    assert ann_group_en["label"] == "Announcements"

    ar_err = client.post(
        "/api/announcements/admin/?lang=ar",
        {"text": "إعلان"},
        format="json",
    )
    assert ar_err.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        str(ar_err.data["expires_at"][0])
        == "مطلوب إما وقت الانتهاء أو قيمة المدة مع وحدتها."
    )



