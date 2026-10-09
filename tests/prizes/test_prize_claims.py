import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models.deletion import ProtectedError
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from apps.authentication.services import (
    build_token_for_user,
    switch_user_profile,
)
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.notifications.models import Notification
from apps.payments.models import PaymentRequest
from apps.prizes.models import (
    PrizeAward,
    PrizeAwardFulfillmentType,
    PrizeAwardSourceType,
    PrizeAwardStatus,
    PrizeClaim,
    PrizeClaimStatus,
)
from apps.prizes.services import PrizeAwardService, PrizeClaimService
from apps.promotions.models import UserPromotionGrant
from apps.users.constants import ProfileType
from tests.factories import (
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def auth_client(user, lang="en") -> APIClient:
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE=lang,
    )
    return client


def make_manager(permissions=None):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager = user.active_profile
    if permissions is not None:
        manager.permissions = list(permissions)
        manager.save(update_fields=["permissions", "modified"])
    else:
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    return user, manager


def make_claim_required_award(
    user=None,
    *,
    source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
    source_id="ref-winner-1",
    title="iPhone 16",
    key_suffix="1",
    snapshot=None,
    metadata=None,
) -> PrizeAward:
    if user is None:
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    return PrizeAwardService.record_claim_required(
        user=user,
        profile=user.active_profile,
        source_type=source_type,
        source_id=source_id,
        title=title,
        description="Monthly referral challenge prize",
        prize_snapshot=snapshot or {"prize_code": "IPHONE", "rank": 1},
        metadata=metadata or {"challenge_id": 10},
        idempotency_key=f"prize:claim:test:{key_suffix}",
    )


def make_digital_award(user=None, *, key_suffix="dig-1") -> PrizeAward:
    if user is None:
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    return PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="spin-1",
        title="250 PP Coins",
        prize_snapshot={"points_amount": 250},
        idempotency_key=f"prize:claim:test:{key_suffix}",
    )


# ===========================================================================
# USER SUBMISSION (1 - 15)
# ===========================================================================


def test_01_owner_submits_claim_for_pending_claim_required_award():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="01")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "15 Nile Corniche, Cairo",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_201_CREATED
    claim = PrizeClaim.objects.get(award=award)
    assert res.data["id"] == claim.pk
    assert res.data["award_id"] == award.pk
    assert claim.full_name == "Ahmed Hassan"
    assert claim.address == "15 Nile Corniche, Cairo"
    assert claim.identity_number == "29501011234567"


def test_02_claim_created_with_submitted_status():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="02")

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )

    assert claim.status == PrizeClaimStatus.SUBMITTED
    assert claim.reviewed_at is None
    assert claim.reviewed_by is None
    assert claim.delivered_at is None
    assert claim.delivered_by is None


def test_03_submitted_at_timestamp_is_set_on_claim_submission():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="03")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "15 Nile Corniche, Cairo",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_201_CREATED
    claim = PrizeClaim.objects.get(award=award)
    assert claim.submitted_at is not None
    assert res.data["submitted_at"] is not None


def test_04_prize_award_remains_pending_after_claim_submission():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="04")

    PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )

    award.refresh_from_db()
    assert award.status == PrizeAwardStatus.PENDING


def test_05_prize_award_fulfilled_at_remains_null_after_claim_submission():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="05")

    PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )

    award.refresh_from_db()
    assert award.fulfilled_at is None


def test_06_unauthenticated_user_denied_claim_submission():
    award = make_claim_required_award(key_suffix="06")

    res = APIClient().post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "15 Nile Corniche, Cairo",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    assert PrizeClaim.objects.count() == 0


def test_07_another_user_cannot_claim_someone_elses_award():
    owner = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    intruder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(owner, key_suffix="07")

    res = auth_client(intruder).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Intruder",
            "address": "Intruder Address",
            "identity_number": "99999999",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert PrizeClaim.objects.count() == 0


def test_08_digital_automatic_award_cannot_be_claimed():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    digital_award = make_digital_award(user, key_suffix="08")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{digital_award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "15 Nile Corniche, Cairo",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert PrizeClaim.objects.count() == 0


def test_09_fulfilled_award_cannot_be_claimed():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    award = make_claim_required_award(user, key_suffix="09")

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    award.refresh_from_db()
    assert award.status == PrizeAwardStatus.FULFILLED

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Second Claim",
            "address": "Other Address",
            "identity_number": "11111111",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert PrizeClaim.objects.filter(award=award).count() == 1


def test_10_duplicate_claim_submission_rejected():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="10")
    client = auth_client(user)

    payload = {
        "full_name": "Ahmed Hassan",
        "address": "15 Nile Corniche, Cairo",
        "identity_number": "29501011234567",
    }
    res_1 = client.post(
        f"/api/prizes/my-awards/{award.pk}/claim/", payload, format="json"
    )
    res_2 = client.post(
        f"/api/prizes/my-awards/{award.pk}/claim/", payload, format="json"
    )

    assert res_1.status_code == status.HTTP_201_CREATED
    assert res_2.status_code == status.HTTP_400_BAD_REQUEST
    assert PrizeClaim.objects.filter(award=award).count() == 1


def test_11_blank_full_name_rejected():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="11")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "",
            "address": "15 Nile Corniche, Cairo",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "full_name" in res.data
    assert PrizeClaim.objects.count() == 0


def test_12_blank_address_rejected():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="12")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "address" in res.data
    assert PrizeClaim.objects.count() == 0


def test_13_blank_identity_number_rejected():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="13")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "15 Nile Corniche, Cairo",
            "identity_number": "",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "identity_number" in res.data
    assert PrizeClaim.objects.count() == 0


def test_14_whitespace_only_fields_rejected():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="14")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "   ",
            "address": "\t\n ",
            "identity_number": "   ",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert set(res.data.keys()) >= {"full_name", "address", "identity_number"}

    with pytest.raises(DRFValidationError) as exc_info:
        PrizeClaimService.submit_claim(
            user,
            award.pk,
            full_name="   ",
            address="   ",
            identity_number="   ",
        )
    assert set(exc_info.value.detail.keys()) == {
        "full_name",
        "address",
        "identity_number",
    }
    assert PrizeClaim.objects.count() == 0


def test_15_profile_switch_customer_seller_still_allows_owner_to_claim():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    SellerProfileFactory(user=user, is_active=False, activated=True)
    award = make_claim_required_award(user, key_suffix="15")

    switched, new_type = switch_user_profile(user, ProfileType.SELLER)
    assert switched is True
    assert new_type == ProfileType.SELLER

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Seller Mode",
            "address": "22 Alexandria Rd",
            "identity_number": "29501019999999",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_201_CREATED
    assert PrizeClaim.objects.filter(award=award).exists()


# ===========================================================================
# PII & SNAPSHOT ISOLATION (16 - 21)
# ===========================================================================


def test_16_identity_number_not_returned_in_user_claim_response():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="16")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "15 Nile Corniche, Cairo",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_201_CREATED
    assert "identity_number" not in res.data
    assert "29501011234567" not in str(res.data)
    assert set(res.data.keys()) == {"id", "award_id", "status", "submitted_at"}


def test_17_address_not_returned_in_user_claim_response():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="17")

    res = auth_client(user).post(
        f"/api/prizes/my-awards/{award.pk}/claim/",
        {
            "full_name": "Ahmed Hassan",
            "address": "Secret Home Street 99",
            "identity_number": "29501011234567",
        },
        format="json",
    )

    assert res.status_code == status.HTTP_201_CREATED
    assert "address" not in res.data
    assert "Secret Home Street 99" not in str(res.data)


def test_18_identity_number_not_returned_in_my_awards_list_or_detail():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="18")
    PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )

    client = auth_client(user)
    list_res = client.get("/api/prizes/my-awards/")
    detail_res = client.get(f"/api/prizes/my-awards/{award.pk}/")

    assert list_res.status_code == status.HTTP_200_OK
    assert detail_res.status_code == status.HTTP_200_OK
    assert "identity_number" not in detail_res.data
    assert "29501011234567" not in str(list_res.data)
    assert "29501011234567" not in str(detail_res.data)
    assert detail_res.data["claim_status"] == PrizeClaimStatus.SUBMITTED
    assert detail_res.data["claim_submitted_at"] is not None


def test_19_address_not_returned_in_my_awards_list_or_detail():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="19")
    PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="Private Villa 42, Giza",
        identity_number="29501011234567",
    )

    client = auth_client(user)
    list_res = client.get("/api/prizes/my-awards/")
    detail_res = client.get(f"/api/prizes/my-awards/{award.pk}/")

    assert "address" not in detail_res.data
    assert "Private Villa 42, Giza" not in str(list_res.data)
    assert "Private Villa 42, Giza" not in str(detail_res.data)


def test_20_prize_award_snapshot_unchanged_after_claim_review_and_delivery():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    original_snapshot = {"prize_code": "IPHONE", "rank": 1}
    award = make_claim_required_award(
        user, key_suffix="20", snapshot=original_snapshot
    )

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    award.refresh_from_db()
    assert award.prize_snapshot == original_snapshot


def test_21_prize_award_metadata_unchanged_after_claim_review_and_delivery():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    original_metadata = {"challenge_id": 77, "winner_rank": 1}
    award = make_claim_required_award(
        user, key_suffix="21", metadata=original_metadata
    )

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    award.refresh_from_db()
    assert award.metadata == original_metadata


# ===========================================================================
# ADMIN LIST / RETRIEVE (22 - 28)
# ===========================================================================


def test_22_manager_with_claims_list_permission_lists_claims():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="22")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.list"])

    res = auth_client(mgr_user).get("/api/prizes/admin/claims/")

    assert res.status_code == status.HTTP_200_OK
    assert len(res.data["results"]) == 1
    row = res.data["results"][0]
    assert row["id"] == claim.pk
    assert row["award_id"] == award.pk
    assert row["user_id"] == user.pk
    assert row["full_name"] == "Ahmed Hassan"
    assert row["address"] == "15 Nile Corniche, Cairo"
    assert row["identity_number"] == "29501011234567"


def test_23_manager_with_claims_retrieve_permission_views_claim_detail():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="23")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.retrieve"])

    res = auth_client(mgr_user).get(f"/api/prizes/admin/claims/{claim.pk}/")

    assert res.status_code == status.HTTP_200_OK
    assert res.data["id"] == claim.pk
    assert res.data["award_id"] == award.pk
    assert res.data["source_type"] == PrizeAwardSourceType.REFERRAL_CHALLENGE
    assert res.data["source_id"] == award.source_id
    assert res.data["title"] == award.title
    assert res.data["prize_snapshot"] == award.prize_snapshot
    assert res.data["full_name"] == "Ahmed Hassan"
    assert res.data["address"] == "15 Nile Corniche, Cairo"
    assert res.data["identity_number"] == "29501011234567"


def test_24_manager_without_permission_gets_403_on_claim_endpoints():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="24")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.awards.list", "prizes.awards.retrieve"])
    client = auth_client(mgr_user)

    assert client.get("/api/prizes/admin/claims/").status_code == 403
    assert client.get(f"/api/prizes/admin/claims/{claim.pk}/").status_code == 403
    assert (
        client.post(f"/api/prizes/admin/claims/{claim.pk}/review/").status_code
        == 403
    )
    assert (
        client.post(f"/api/prizes/admin/claims/{claim.pk}/deliver/").status_code
        == 403
    )


def test_25_customer_gets_403_on_admin_claim_endpoints():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(customer, key_suffix="25")
    claim = PrizeClaimService.submit_claim(
        customer,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    client = auth_client(customer)

    assert client.get("/api/prizes/admin/claims/").status_code == 403
    assert client.get(f"/api/prizes/admin/claims/{claim.pk}/").status_code == 403
    assert (
        client.post(f"/api/prizes/admin/claims/{claim.pk}/review/").status_code
        == 403
    )
    assert (
        client.post(f"/api/prizes/admin/claims/{claim.pk}/deliver/").status_code
        == 403
    )


def test_26_seller_gets_403_on_admin_claim_endpoints():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    award = make_claim_required_award(seller, key_suffix="26")
    claim = PrizeClaimService.submit_claim(
        seller,
        award.pk,
        full_name="Ahmed Seller",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    client = auth_client(seller)

    assert client.get("/api/prizes/admin/claims/").status_code == 403
    assert client.get(f"/api/prizes/admin/claims/{claim.pk}/").status_code == 403
    assert (
        client.post(f"/api/prizes/admin/claims/{claim.pk}/review/").status_code
        == 403
    )
    assert (
        client.post(f"/api/prizes/admin/claims/{claim.pk}/deliver/").status_code
        == 403
    )


def test_27_filter_admin_claims_by_status_works():
    u1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    u2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    a1 = make_claim_required_award(u1, key_suffix="27-1")
    a2 = make_claim_required_award(u2, key_suffix="27-2")

    c1 = PrizeClaimService.submit_claim(
        u1,
        a1.pk,
        full_name="User One",
        address="Address 1",
        identity_number="111",
    )
    c2 = PrizeClaimService.submit_claim(
        u2,
        a2.pk,
        full_name="User Two",
        address="Address 2",
        identity_number="222",
    )
    mgr_user, _ = make_manager(["prizes.claims.list", "prizes.claims.review"])
    PrizeClaimService.review_claim(c2.pk, mgr_user)

    client = auth_client(mgr_user)
    sub_res = client.get("/api/prizes/admin/claims/?status=SUBMITTED")
    rev_res = client.get("/api/prizes/admin/claims/?status=REVIEWED")

    assert [r["id"] for r in sub_res.data["results"]] == [c1.pk]
    assert [r["id"] for r in rev_res.data["results"]] == [c2.pk]


def test_28_filter_admin_claims_by_source_type_works():
    u1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    u2 = UserAccountFactory(with_profile=ProfileType.SELLER)
    a_ref = make_claim_required_award(
        u1,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        key_suffix="28-ref",
    )
    a_sea = make_claim_required_award(
        u2,
        source_type=PrizeAwardSourceType.SEASON,
        key_suffix="28-sea",
    )
    c_ref = PrizeClaimService.submit_claim(
        u1,
        a_ref.pk,
        full_name="Ref Winner",
        address="Address Ref",
        identity_number="111",
    )
    c_sea = PrizeClaimService.submit_claim(
        u2,
        a_sea.pk,
        full_name="Season Winner",
        address="Address Sea",
        identity_number="222",
    )
    mgr_user, _ = make_manager(["prizes.claims.list"])
    client = auth_client(mgr_user)

    ref_res = client.get(
        "/api/prizes/admin/claims/?source_type=REFERRAL_CHALLENGE"
    )
    sea_res = client.get(
        "/api/prizes/admin/claims/?award__source_type=SEASON"
    )

    assert [r["id"] for r in ref_res.data["results"]] == [c_ref.pk]
    assert [r["id"] for r in sea_res.data["results"]] == [c_sea.pk]


# ===========================================================================
# ADMIN REVIEW (29 - 35)
# ===========================================================================


def test_29_manager_with_claims_review_permission_reviews_submitted_claim():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="29")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review"])

    res = auth_client(mgr_user).post(
        f"/api/prizes/admin/claims/{claim.pk}/review/"
    )

    assert res.status_code == status.HTTP_200_OK
    assert res.data["id"] == claim.pk
    assert res.data["status"] == PrizeClaimStatus.REVIEWED


def test_30_claim_status_becomes_reviewed_after_review():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="30")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review"])

    reviewed = PrizeClaimService.review_claim(claim.pk, mgr_user)
    claim.refresh_from_db()

    assert reviewed.status == PrizeClaimStatus.REVIEWED
    assert claim.status == PrizeClaimStatus.REVIEWED


def test_31_reviewed_at_and_reviewed_by_set_on_review():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="31")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review"])

    res = auth_client(mgr_user).post(
        f"/api/prizes/admin/claims/{claim.pk}/review/"
    )
    claim.refresh_from_db()

    assert claim.reviewed_at is not None
    assert claim.reviewed_by_id == mgr_user.pk
    assert res.data["reviewed_at"] is not None
    assert res.data["reviewed_by_id"] == mgr_user.pk


def test_32_prize_award_remains_pending_after_review():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="32")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review"])

    PrizeClaimService.review_claim(claim.pk, mgr_user)
    award.refresh_from_db()

    assert award.status == PrizeAwardStatus.PENDING


def test_33_prize_award_fulfilled_at_remains_null_after_review():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="33")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review"])

    PrizeClaimService.review_claim(claim.pk, mgr_user)
    award.refresh_from_db()

    assert award.fulfilled_at is None


def test_34_repeat_review_is_idempotent():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="34")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_1, _ = make_manager(["prizes.claims.review"])
    mgr_2, _ = make_manager(["prizes.claims.review"])

    first = PrizeClaimService.review_claim(claim.pk, mgr_1)
    first_reviewed_at = first.reviewed_at

    res = auth_client(mgr_2).post(
        f"/api/prizes/admin/claims/{claim.pk}/review/"
    )
    claim.refresh_from_db()

    assert res.status_code == status.HTTP_200_OK
    assert claim.status == PrizeClaimStatus.REVIEWED
    assert claim.reviewed_at == first_reviewed_at
    assert claim.reviewed_by_id == mgr_1.pk


def test_35_manager_without_claims_review_permission_gets_403():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="35")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_no_review, _ = make_manager(
        ["prizes.claims.list", "prizes.claims.retrieve", "prizes.claims.deliver"]
    )

    res = auth_client(mgr_no_review).post(
        f"/api/prizes/admin/claims/{claim.pk}/review/"
    )
    claim.refresh_from_db()

    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert claim.status == PrizeClaimStatus.SUBMITTED


# ===========================================================================
# ADMIN DELIVERY (36 - 43)
# ===========================================================================


def test_36_manager_with_claims_deliver_permission_delivers_reviewed_claim():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="36")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, mgr_user)

    res = auth_client(mgr_user).post(
        f"/api/prizes/admin/claims/{claim.pk}/deliver/"
    )

    assert res.status_code == status.HTTP_200_OK
    assert res.data["id"] == claim.pk
    assert res.data["status"] == PrizeClaimStatus.DELIVERED
    assert res.data["award_status"] == PrizeAwardStatus.FULFILLED


def test_37_claim_status_becomes_delivered_after_delivery():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="37")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, mgr_user)

    delivered = PrizeClaimService.deliver_claim(claim.pk, mgr_user)
    claim.refresh_from_db()

    assert delivered.status == PrizeClaimStatus.DELIVERED
    assert claim.status == PrizeClaimStatus.DELIVERED


def test_38_delivered_at_and_delivered_by_set_on_delivery():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="38")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    reviewer, _ = make_manager(["prizes.claims.review"])
    deliverer, _ = make_manager(["prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, reviewer)

    res = auth_client(deliverer).post(
        f"/api/prizes/admin/claims/{claim.pk}/deliver/"
    )
    claim.refresh_from_db()

    assert claim.delivered_at is not None
    assert claim.delivered_by_id == deliverer.pk
    assert claim.reviewed_by_id == reviewer.pk
    assert res.data["delivered_at"] is not None
    assert res.data["delivered_by_id"] == deliverer.pk


def test_39_prize_award_status_becomes_fulfilled_on_delivery():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="39")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    award.refresh_from_db()
    assert award.status == PrizeAwardStatus.FULFILLED


def test_40_prize_award_fulfilled_at_matches_delivered_at_on_delivery():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="40")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    claim.refresh_from_db()
    award.refresh_from_db()
    assert award.fulfilled_at is not None
    assert award.fulfilled_at == claim.delivered_at


def test_41_repeat_deliver_is_idempotent():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="41")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_1, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    mgr_2, _ = make_manager(["prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, mgr_1)

    first = PrizeClaimService.deliver_claim(claim.pk, mgr_1)
    first_delivered_at = first.delivered_at

    res = auth_client(mgr_2).post(
        f"/api/prizes/admin/claims/{claim.pk}/deliver/"
    )
    claim.refresh_from_db()
    award.refresh_from_db()

    assert res.status_code == status.HTTP_200_OK
    assert claim.status == PrizeClaimStatus.DELIVERED
    assert claim.delivered_at == first_delivered_at
    assert claim.delivered_by_id == mgr_1.pk
    assert award.status == PrizeAwardStatus.FULFILLED
    assert award.fulfilled_at == first_delivered_at


def test_42_delivering_submitted_claim_before_review_fails_with_400():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="42")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.deliver"])

    res = auth_client(mgr_user).post(
        f"/api/prizes/admin/claims/{claim.pk}/deliver/"
    )
    claim.refresh_from_db()
    award.refresh_from_db()

    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert claim.status == PrizeClaimStatus.SUBMITTED
    assert claim.delivered_at is None
    assert award.status == PrizeAwardStatus.PENDING
    assert award.fulfilled_at is None


def test_43_manager_without_claims_deliver_permission_gets_403():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="43")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_review_only, _ = make_manager(
        ["prizes.claims.list", "prizes.claims.retrieve", "prizes.claims.review"]
    )
    PrizeClaimService.review_claim(claim.pk, mgr_review_only)

    res = auth_client(mgr_review_only).post(
        f"/api/prizes/admin/claims/{claim.pk}/deliver/"
    )
    claim.refresh_from_db()
    award.refresh_from_db()

    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert claim.status == PrizeClaimStatus.REVIEWED
    assert award.status == PrizeAwardStatus.PENDING


# ===========================================================================
# IMMUTABILITY & SIDE EFFECTS (44 - 50)
# ===========================================================================


def test_44_delivered_claim_cannot_be_modified_or_reviewed_again():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="44")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    # Reviewing an already-delivered claim is rejected with 400
    review_res = auth_client(mgr_user).post(
        f"/api/prizes/admin/claims/{claim.pk}/review/"
    )
    assert review_res.status_code == status.HTTP_400_BAD_REQUEST

    # Mutating claimant fields or reverting status at model level is rejected
    claim.refresh_from_db()
    claim.address = "Tampered Address"
    with pytest.raises(DjangoValidationError) as exc_addr:
        claim.save()
    assert "address" in exc_addr.value.message_dict

    claim.refresh_from_db()
    claim.identity_number = "000000"
    with pytest.raises(DjangoValidationError) as exc_id:
        claim.save()
    assert "identity_number" in exc_id.value.message_dict

    claim.refresh_from_db()
    claim.status = PrizeClaimStatus.REVIEWED
    with pytest.raises(DjangoValidationError) as exc_stat:
        claim.save()
    assert "status" in exc_stat.value.message_dict

    claim.refresh_from_db()
    with pytest.raises(ProtectedError):
        claim.delete()


def test_45_fulfilled_prize_award_cannot_be_reverted():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = make_claim_required_award(user, key_suffix="45")
    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    award.refresh_from_db()
    assert award.status == PrizeAwardStatus.FULFILLED

    award.status = PrizeAwardStatus.PENDING
    award.fulfilled_at = None
    with pytest.raises(DjangoValidationError) as exc_info:
        award.save()
    assert "status" in exc_info.value.message_dict


def test_46_claim_lifecycle_creates_zero_point_transactions():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    award = make_claim_required_award(user, key_suffix="46")
    before = PointTransaction.objects.count()

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    assert PointTransaction.objects.count() == before == 0


def test_47_claim_lifecycle_creates_zero_user_promotion_grants():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    award = make_claim_required_award(user, key_suffix="47")
    before = UserPromotionGrant.objects.count()

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    assert UserPromotionGrant.objects.count() == before == 0


def test_48_claim_lifecycle_creates_zero_badge_awards():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    award = make_claim_required_award(user, key_suffix="48")
    before = BadgeAward.objects.count()

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    assert BadgeAward.objects.count() == before == 0


def test_49_claim_lifecycle_creates_zero_payment_requests():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    award = make_claim_required_award(user, key_suffix="49")
    before = PaymentRequest.objects.count()

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    assert PaymentRequest.objects.count() == before == 0


def test_50_claim_submission_review_and_delivery_create_zero_notifications():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    mgr_user, _ = make_manager(["prizes.claims.review", "prizes.claims.deliver"])
    award = make_claim_required_award(user, key_suffix="50")
    before = Notification.objects.count()

    claim = PrizeClaimService.submit_claim(
        user,
        award.pk,
        full_name="Ahmed Hassan",
        address="15 Nile Corniche, Cairo",
        identity_number="29501011234567",
    )
    PrizeClaimService.review_claim(claim.pk, mgr_user)
    PrizeClaimService.deliver_claim(claim.pk, mgr_user)

    assert Notification.objects.count() == before == 1


def test_51_migration_0002_has_no_historical_backfill_operations():
    from importlib import import_module

    migration_0002 = import_module("apps.prizes.migrations.0002_prizeclaim")
    op_names = [
        type(op).__name__ for op in migration_0002.Migration.operations
    ]
    assert "RunPython" not in op_names
    assert "RunSQL" not in op_names
    assert PrizeClaim.objects.count() == 0

