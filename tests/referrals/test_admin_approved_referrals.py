"""BE-REF-01: redemption creates a pending referral; only approval pays the inviter."""

import importlib
from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.auctions.services import BiddingService
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.loyalty.constants import (
    RewardEventType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import PointTransaction
from apps.loyalty.reward_engine import RewardEngine, RewardResult
from apps.loyalty.services import PointService, RewardRuleService
from apps.referrals.models import LinkSharingCode, Referral, ReferralStatus
from apps.referrals.services import ReferralService
from apps.users.constants import ProfileType
from apps.users.serializers import CustomerProfileMobileSerializer
from tests.factories import (
    ManagerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
    create_active_auction_with_item,
)

pytestmark = pytest.mark.django_db

APPROVED_SOURCE = RewardSourceType.REFERRAL_APPROVED
backfill_module = importlib.import_module(
    "apps.referrals.migrations.0003_backfill_legacy_referrals"
)


def client_for(user):
    user.refresh_from_db()
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {build_token_for_user(user).access_token}"
    )
    return client


def new_customer():
    return UserAccountFactory(with_profile=ProfileType.CUSTOMER)


def manager(permissions=None):
    user = UserAccountFactory(with_profile=False)
    kwargs = {"user": user, "is_active": True}
    if permissions is not None:
        kwargs["permissions"] = permissions
    ManagerProfileFactory(**kwargs)
    return user


def issue_code(inviter):
    return ReferralService.create_code(inviter.active_profile)


def redeem(user, code):
    return client_for(user).post("/api/referrals/redeem/", {"code": code.code})


def approve(referral, by=None):
    return client_for(by or manager()).post(
        f"/api/referrals/admin/{referral.pk}/approve/"
    )


def reject(referral, by=None, reason=""):
    return client_for(by or manager()).post(
        f"/api/referrals/admin/{referral.pk}/reject/",
        {"reason": reason},
    )


def pending_referral():
    inviter = new_customer()
    referred = new_customer()
    code = issue_code(inviter)
    redeem(referred, code)
    return Referral.objects.get(code=code), inviter, referred


def inviter_rows(inviter):
    return PointTransaction.objects.filter(user=inviter, source_type=APPROVED_SOURCE)


def set_rule(**changes):
    RewardRuleService.update_rule(
        RewardRuleCode.REFERRAL_APPROVED, updated_by=None, **changes
    )


# --- Redemption ---------------------------------------------------------------


def test_redemption_creates_pending_referral_and_pays_nothing():
    inviter = new_customer()
    referred = new_customer()
    code = issue_code(inviter)

    response = redeem(referred, code)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["is_used"] is True
    assert response.data["referral"]["status"] == ReferralStatus.PENDING
    referral = Referral.objects.get(code=code)
    assert referral.status == ReferralStatus.PENDING
    assert referral.inviter_user_id == inviter.pk
    assert referral.inviter_profile_id == inviter.active_profile.pk
    assert referral.referred_user_id == referred.pk
    assert referral.referred_profile_id == referred.active_profile.pk
    assert not PointTransaction.objects.exists()


def test_self_referral_is_rejected():
    inviter = new_customer()
    SellerProfileFactory(user=inviter, is_active=False)
    code = issue_code(inviter)

    assert redeem(inviter, code).status_code == status.HTTP_400_BAD_REQUEST
    switch_user_profile(inviter, ProfileType.SELLER)
    assert redeem(inviter, code).status_code == status.HTTP_400_BAD_REQUEST
    assert not Referral.objects.exists()


def test_expired_code_creates_no_referral():
    code = issue_code(new_customer())
    code.expires_at = timezone.now() - timedelta(minutes=1)
    code.save(update_fields=["expires_at", "modified"])

    assert redeem(new_customer(), code).status_code == status.HTTP_400_BAD_REQUEST
    assert not Referral.objects.exists()


def test_new_account_window_is_unchanged():
    code = issue_code(new_customer())
    old_account = new_customer()
    old_account.date_joined = timezone.now() - timedelta(hours=3)
    old_account.save(update_fields=["date_joined"])

    assert redeem(old_account, code).status_code == status.HTTP_400_BAD_REQUEST
    assert not Referral.objects.exists()


def test_code_is_single_use():
    code = issue_code(new_customer())
    redeem(new_customer(), code)

    second = redeem(new_customer(), code)

    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert Referral.objects.count() == 1


def test_one_referral_per_account():
    referred = new_customer()
    first = issue_code(new_customer())
    second = issue_code(new_customer())
    redeem(referred, first)

    response = redeem(referred, second)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    second.refresh_from_db()
    assert second.is_used is False
    assert Referral.objects.filter(referred_user=referred).count() == 1


def test_another_mode_of_the_same_account_cannot_claim_again():
    referred = new_customer()
    SellerProfileFactory(user=referred, is_active=False)
    redeem(referred, issue_code(new_customer()))
    switch_user_profile(referred, ProfileType.SELLER)

    response = redeem(referred, issue_code(new_customer()))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Referral.objects.filter(referred_user=referred).count() == 1


def test_database_enforces_one_referral_per_referred_account():
    referral, inviter, referred = pending_referral()
    other_code = issue_code(new_customer())

    with pytest.raises(IntegrityError), transaction.atomic():
        Referral.objects.create(
            code=other_code,
            inviter_user_id=other_code.owner_profile.user_id,
            inviter_profile=other_code.owner_profile,
            referred_user=referred,
            referred_profile=referred.active_profile,
            redeemed_at=timezone.now(),
        )


def test_rejection_does_not_allow_referral_shopping():
    referral, _, referred = pending_referral()
    reject(referral)

    response = redeem(referred, issue_code(new_customer()))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Referral.objects.filter(referred_user=referred).count() == 1


# --- Admin review ---------------------------------------------------------------


def test_approval_pays_the_inviter_default_points():
    referral, inviter, referred = pending_referral()

    response = approve(referral)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] == ReferralStatus.APPROVED
    referral.refresh_from_db()
    assert referral.reviewed_at is not None
    assert referral.reviewed_by_id is not None
    assert list(inviter_rows(inviter).values_list("points", flat=True)) == [30]
    assert not PointTransaction.objects.filter(user=referred).exists()


def test_rejection_pays_nothing():
    referral, inviter, _ = pending_referral()

    response = reject(referral, reason="Looks automated")

    assert response.status_code == status.HTTP_200_OK
    referral.refresh_from_db()
    assert referral.status == ReferralStatus.REJECTED
    assert referral.rejection_reason == "Looks automated"
    assert not PointTransaction.objects.exists()


@pytest.mark.parametrize("profile_type", [ProfileType.CUSTOMER, ProfileType.SELLER])
def test_non_managers_cannot_review_or_list(profile_type):
    referral, _, _ = pending_referral()
    user = UserAccountFactory(with_profile=profile_type)

    assert approve(referral, by=user).status_code == status.HTTP_403_FORBIDDEN
    assert reject(referral, by=user).status_code == status.HTTP_403_FORBIDDEN
    assert client_for(user).get("/api/referrals/admin/").status_code == (
        status.HTTP_403_FORBIDDEN
    )
    referral.refresh_from_db()
    assert referral.status == ReferralStatus.PENDING


def test_manager_needs_exact_permissions():
    referral, _, _ = pending_referral()
    lister = manager(permissions=["referrals.list"])

    list_response = client_for(lister).get("/api/referrals/admin/")

    assert list_response.status_code == status.HTTP_200_OK
    assert approve(referral, by=lister).status_code == status.HTTP_403_FORBIDDEN
    assert reject(referral, by=lister).status_code == status.HTTP_403_FORBIDDEN


def test_repeated_approval_pays_once():
    referral, inviter, _ = pending_referral()
    reviewer = manager()

    first = approve(referral, by=reviewer)
    second = approve(referral, by=reviewer)

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert Referral.objects.get().status == ReferralStatus.APPROVED
    assert inviter_rows(inviter).count() == 1


def test_review_transitions_only_from_pending():
    approved, _, _ = pending_referral()
    approve(approved)
    rejected, _, _ = pending_referral()
    reject(rejected)

    assert reject(approved).status_code == status.HTTP_400_BAD_REQUEST
    assert approve(rejected).status_code == status.HTTP_400_BAD_REQUEST
    assert reject(rejected).status_code == status.HTTP_200_OK
    approved.refresh_from_db()
    rejected.refresh_from_db()
    assert approved.status == ReferralStatus.APPROVED
    assert rejected.status == ReferralStatus.REJECTED


def test_unknown_referral_is_not_found():
    response = client_for(manager()).post("/api/referrals/admin/999999/approve/")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_admin_list_filters_by_status():
    approve(pending_referral()[0])
    pending_referral()
    client = client_for(manager())

    response = client.get("/api/referrals/admin/", {"status": "pending"})

    assert response.status_code == status.HTTP_200_OK
    assert [item["status"] for item in response.data["results"]] == ["pending"]
    item = response.data["results"][0]
    expected = {"inviter_user_id", "referred_user_id", "redeemed_at", "reviewed_by_id"}
    assert expected <= set(item)


def test_my_referrals_shows_status_without_review_details():
    referral, inviter, _ = pending_referral()

    response = client_for(inviter).get("/api/referrals/my/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["results"][0]["status"] == ReferralStatus.PENDING
    assert "reviewed_by_id" not in response.data["results"][0]
    assert "rejection_reason" not in response.data["results"][0]


# --- Reward ---------------------------------------------------------------------


def test_replayed_approval_event_pays_once():
    referral, inviter, _ = pending_referral()
    approve(referral)
    referral.refresh_from_db()

    for _ in range(3):
        RewardEngine.handle_event(
            RewardEventType.REFERRAL_APPROVED,
            actor=referral.inviter_profile,
            context={"referral": referral},
        )

    assert inviter_rows(inviter).count() == 1


def test_configured_points_are_used():
    set_rule(points=50)
    referral, inviter, _ = pending_referral()

    approve(referral)

    assert inviter_rows(inviter).get().points == 50


def test_disabled_rule_still_approves_and_is_not_topped_up():
    set_rule(enabled=False)
    referral, inviter, _ = pending_referral()

    assert approve(referral).status_code == status.HTTP_200_OK
    referral.refresh_from_db()
    assert referral.status == ReferralStatus.APPROVED
    assert not inviter_rows(inviter).exists()

    set_rule(enabled=True)
    approve(referral)

    assert not inviter_rows(inviter).exists()


def test_reward_ledger_attribution():
    referral, inviter, referred = pending_referral()
    approve(referral)

    row = inviter_rows(inviter).get()
    assert row.user_id == inviter.pk
    assert row.profile_id == inviter.active_profile.pk
    assert row.source_id == str(referral.pk)
    assert row.reason == "Referral approved reward"
    expected_key = f"reward:referral_approved:referral:{referral.pk}:inviter"
    assert row.idempotency_key == expected_key
    assert row.metadata == {
        "reward_code": "referral_approved",
        "referral_id": referral.pk,
        "referred_user_id": referred.pk,
    }


def test_concurrent_duplicate_insert_keeps_one_row(monkeypatch):
    referral, inviter, _ = pending_referral()
    approve(referral)
    referral.refresh_from_db()
    balance = PointService.get_balance(inviter)

    monkeypatch.setattr(RewardEngine, "_paid_keys", staticmethod(lambda keys: set()))
    monkeypatch.setattr(
        PointService,
        "_get_idempotent_transaction",
        staticmethod(lambda key: None),
    )
    result = RewardResult()
    for _ in range(2):
        RewardEngine._handle_referral_approved({"referral": referral}, result)

    assert result.points_awarded == []
    assert inviter_rows(inviter).count() == 1
    assert PointService.get_balance(inviter) == balance


def test_pending_referral_event_pays_nothing():
    referral, inviter, _ = pending_referral()

    RewardEngine.handle_event(
        RewardEventType.REFERRAL_APPROVED,
        actor=referral.inviter_profile,
        context={"referral": referral},
    )

    assert not inviter_rows(inviter).exists()


def test_approval_and_first_bid_create_no_challenge_rewards():
    referral, inviter, referred = pending_referral()
    approve(referral)
    _, item, _ = create_active_auction_with_item()

    BiddingService.place_bid(item, referred.active_profile, Decimal("101.00"))

    # Approval pays REFERRAL_APPROVED; the first bid pays REFERRAL_FIRST_BID
    # (BE-REF-02). Nothing else, such as a challenge reward, is created.
    inviter_sources = sorted(
        PointTransaction.objects.filter(user=inviter).values_list(
            "source_type", flat=True
        )
    )
    assert inviter_sources == [
        APPROVED_SOURCE,
        RewardSourceType.REFERRAL_FIRST_BID,
    ]


# --- Successful invites -----------------------------------------------------------


def test_successful_invites_count_only_counts_approved():
    inviter = new_customer()
    referrals = []
    for _ in range(3):
        code = issue_code(inviter)
        redeem(new_customer(), code)
        referrals.append(Referral.objects.get(code=code))
    approve(referrals[0])
    reject(referrals[1])

    data = CustomerProfileMobileSerializer(inviter.active_profile).data

    assert data["successful_invites_count"] == 1
    consumed = LinkSharingCode.objects.filter(
        owner_profile=inviter.active_profile, is_used=True
    )
    assert consumed.count() == 3


# --- Legacy data ------------------------------------------------------------------


def _legacy_redeemed_code(inviter, referred, used_at=None):
    code = LinkSharingCode.objects.create(
        owner_profile=inviter.active_profile,
        code=f"LEG{LinkSharingCode.objects.count()}",
        expires_at=timezone.now() + timedelta(hours=1),
        is_used=True,
        used_by_profile=referred.active_profile,
        used_at=used_at or timezone.now(),
    )
    PointService.earn_points(
        user=inviter,
        profile=inviter.active_profile,
        points=30,
        reason="Referral accepted",
        source_type=RewardSourceType.REFERRAL_ACCEPTED,
        source_id=str(code.pk),
        idempotency_key=(
            f"points:referral:{code.pk}:inviter:{inviter.active_profile.pk}"
        ),
        metadata={
            "referral_code": code.code,
            "used_by_profile_id": referred.active_profile.pk,
        },
    )
    return code


def test_legacy_redeemed_codes_backfill_as_approved_without_paying_again():
    inviter = new_customer()
    referred = new_customer()
    code = _legacy_redeemed_code(inviter, referred)
    second_inviter = new_customer()
    # Pre-BE-REF-01 bug: the same account redeemed a second code.
    second = _legacy_redeemed_code(
        second_inviter, referred, used_at=timezone.now() + timedelta(minutes=5)
    )
    orphan = LinkSharingCode.objects.create(
        owner_profile=inviter.active_profile,
        code="ORPHAN",
        expires_at=timezone.now(),
        is_used=True,
        used_by_profile=None,
    )
    ledger_before = set(
        PointTransaction.objects.values_list("idempotency_key", "points")
    )

    backfill_module.backfill_legacy_referrals(django_apps, None)

    referral = Referral.objects.get(code=code)
    assert referral.status == ReferralStatus.APPROVED
    assert referral.referred_user_id == referred.pk
    assert not Referral.objects.filter(code__in=[second, orphan]).exists()
    assert ReferralService.approved_count(inviter.active_profile) == 1

    ReferralService.approve(referral.pk, manager())
    RewardEngine.handle_event(
        RewardEventType.REFERRAL_APPROVED,
        actor=referral.inviter_profile,
        context={"referral": referral},
    )

    assert set(PointTransaction.objects.values_list("idempotency_key", "points")) == (
        ledger_before
    )
    assert PointService.get_balance(inviter) == 30


def test_backfill_is_idempotent():
    _legacy_redeemed_code(new_customer(), new_customer())

    backfill_module.backfill_legacy_referrals(django_apps, None)
    backfill_module.backfill_legacy_referrals(django_apps, None)

    assert Referral.objects.count() == 1


def test_service_rejects_a_blank_code():
    with pytest.raises(ValidationError):
        ReferralService.redeem_code(new_customer().active_profile, "  ")


@override_settings(MIGRATION_MODULES={})
def test_backfill_runs_with_historical_migration_models():
    """Test settings disable migrations, so load the real ones for this check."""

    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    inviter = new_customer()
    referred = new_customer()
    code = _legacy_redeemed_code(inviter, referred)
    historical_apps = MigrationLoader(connection).project_state(
        ("referrals", "0002_referral")
    ).apps

    backfill_module.backfill_legacy_referrals(historical_apps, None)

    referral = Referral.objects.get(code=code)
    assert referral.status == ReferralStatus.APPROVED
    assert referral.inviter_user_id == inviter.pk
    assert referral.redeemed_at == code.used_at
    assert PointService.get_balance(inviter) == 30
