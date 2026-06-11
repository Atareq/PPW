from decimal import Decimal

import pytest
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.complaints.models import (
    Complaint,
    ComplaintEvent,
    ComplaintEventType,
    ComplaintStatus,
    ComplaintType,
)
from apps.complaints.serializers import ComplaintCreateSerializer
from apps.complaints.services import ComplaintService
from apps.payments.models import (
    PaymentRequest,
    PaymentRequestStatus,
    PaymentRequestType,
)
from apps.users.constants import ProfileType
from tests.factories import SupplyFactory, UserAccountFactory

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def create_payment_request(
    *,
    status=PaymentRequestStatus.APPROVED,
    buyer=None,
    seller=None,
):
    buyer = buyer or UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller = seller or UserAccountFactory(with_profile=ProfileType.SELLER)
    asset = SupplyFactory(owner=seller.active_profile)
    payment_request = PaymentRequest.objects.create(
        buyer=buyer.active_profile,
        seller=seller.active_profile,
        asset=asset,
        amount=Decimal("25.00"),
        status=status,
        request_type=PaymentRequestType.MARKET,
    )
    return payment_request, buyer, seller


def create_complaint(status=PaymentRequestStatus.APPROVED):
    payment_request, buyer, seller = create_payment_request(status=status)
    complaint = ComplaintService.create_complaint(
        payment_request.pk,
        buyer.active_profile,
        title="Problem title",
        description="Problem details",
    )
    return complaint, payment_request, buyer, seller


class TestComplaintCreation:
    def test_buyer_can_create_complaint_for_approved_payment_request(self):
        payment_request, buyer, _ = create_payment_request(
            status=PaymentRequestStatus.APPROVED,
        )

        complaint = ComplaintService.create_complaint(
            payment_request.pk,
            buyer.active_profile,
            title="Item issue",
            description="The item has a problem.",
        )

        assert complaint.status == ComplaintStatus.OPEN
        assert complaint.created_by_id == buyer.active_profile.pk
        assert complaint.complaint_type == ComplaintType.POST_SALE

    def test_seller_can_create_complaint_for_approved_payment_request(self):
        payment_request, _, seller = create_payment_request(
            status=PaymentRequestStatus.APPROVED,
        )

        complaint = ComplaintService.create_complaint(
            payment_request.pk,
            seller.active_profile,
            title="Buyer issue",
            description="The buyer has a problem.",
        )

        assert complaint.created_by_id == seller.active_profile.pk
        assert complaint.buyer_id == payment_request.buyer_id
        assert complaint.seller_id == payment_request.seller_id

    def test_buyer_can_create_complaint_for_rejected_payment_request(self):
        payment_request, buyer, _ = create_payment_request(
            status=PaymentRequestStatus.REJECTED,
        )

        complaint = ComplaintService.create_complaint(
            payment_request.pk,
            buyer.active_profile,
            title="Payment disagreement",
            description="The seller rejected the payment.",
        )

        assert complaint.complaint_type == ComplaintType.PAYMENT_REJECTED

    def test_seller_can_create_complaint_for_rejected_payment_request(self):
        payment_request, _, seller = create_payment_request(
            status=PaymentRequestStatus.REJECTED,
        )

        complaint = ComplaintService.create_complaint(
            payment_request.pk,
            seller.active_profile,
            title="Rejected payment disagreement",
            description="The buyer disagrees with the rejection.",
        )

        assert complaint.created_by_id == seller.active_profile.pk

    @pytest.mark.parametrize(
        "payment_status",
        [
            PaymentRequestStatus.PENDING,
            PaymentRequestStatus.CANCELLED,
        ],
    )
    def test_cannot_create_for_pending_or_cancelled_payment_request(
        self,
        payment_status,
    ):
        payment_request, buyer, _ = create_payment_request(status=payment_status)

        with pytest.raises(ValidationError):
            ComplaintService.create_complaint(
                payment_request.pk,
                buyer.active_profile,
                title="Not allowed",
                description="This should not be allowed.",
            )

    def test_unrelated_profile_cannot_create_complaint(self):
        payment_request, _, _ = create_payment_request()
        other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(NotFound):
            ComplaintService.create_complaint(
                payment_request.pk,
                other.active_profile,
                title="Unrelated",
                description="Not part of this payment request.",
            )

    def test_only_one_active_complaint_per_payment_request(self):
        payment_request, buyer, seller = create_payment_request()
        ComplaintService.create_complaint(
            payment_request.pk,
            buyer.active_profile,
            title="First",
            description="First active complaint.",
        )

        with pytest.raises(ValidationError):
            ComplaintService.create_complaint(
                payment_request.pk,
                seller.active_profile,
                title="Second",
                description="Second active complaint.",
            )

    def test_closed_complaint_allows_new_active_complaint(self):
        complaint, payment_request, buyer, _ = create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        ComplaintService.resolve(
            complaint.pk,
            manager.active_profile,
            admin_note="closed",
        )

        new_complaint = ComplaintService.create_complaint(
            payment_request.pk,
            buyer.active_profile,
            title="New issue",
            description="A new active complaint after closure.",
        )

        assert new_complaint.pk != complaint.pk
        assert new_complaint.status == ComplaintStatus.OPEN

    def test_created_complaint_copies_buyer_and_seller_from_payment_request(self):
        payment_request, buyer, _ = create_payment_request()

        complaint = ComplaintService.create_complaint(
            payment_request.pk,
            buyer.active_profile,
            title="Copied parties",
            description="Buyer and seller should be copied.",
            complaint_type=ComplaintType.OTHER,
        )

        assert complaint.buyer_id == payment_request.buyer_id
        assert complaint.seller_id == payment_request.seller_id
        assert complaint.complaint_type == ComplaintType.OTHER


class TestComplaintCreateSerializer:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("title", ""),
            ("description", ""),
            ("complaint_type", "unsupported"),
        ],
    )
    def test_required_fields_and_complaint_type_are_validated(
        self,
        field,
        value,
    ):
        data = {
            "payment_request_id": 1,
            "title": "Problem title",
            "description": "Problem details",
            "complaint_type": ComplaintType.OTHER,
        }
        data[field] = value

        serializer = ComplaintCreateSerializer(data=data)

        assert not serializer.is_valid()
        assert field in serializer.errors


class TestComplaintAdminActions:
    def test_manager_can_mark_complaint_in_review(self):
        complaint, _, _, _ = create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

        reviewed = ComplaintService.mark_in_review(
            complaint.pk,
            manager.active_profile,
        )

        assert reviewed.status == ComplaintStatus.IN_REVIEW
        assert reviewed.reviewed_by_id == manager.active_profile.pk
        assert reviewed.reviewed_at is not None

    def test_manager_can_resolve_complaint(self):
        complaint, _, _, _ = create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

        resolved = ComplaintService.resolve(
            complaint.pk,
            manager.active_profile,
            admin_note="Resolved manually.",
        )

        assert resolved.status == ComplaintStatus.RESOLVED
        assert resolved.admin_note == "Resolved manually."
        assert resolved.resolved_at is not None

    def test_manager_can_reject_complaint(self):
        complaint, _, _, _ = create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

        rejected = ComplaintService.reject(
            complaint.pk,
            manager.active_profile,
            admin_note="Rejected manually.",
        )

        assert rejected.status == ComplaintStatus.REJECTED
        assert rejected.admin_note == "Rejected manually."
        assert rejected.reviewed_by_id == manager.active_profile.pk

    def test_non_manager_cannot_review_resolve_or_reject(self):
        complaint, _, buyer, _ = create_complaint()

        with pytest.raises(PermissionDenied):
            ComplaintService.mark_in_review(
                complaint.pk,
                buyer.active_profile,
            )
        with pytest.raises(PermissionDenied):
            ComplaintService.resolve(complaint.pk, buyer.active_profile)
        with pytest.raises(PermissionDenied):
            ComplaintService.reject(complaint.pk, buyer.active_profile)

    def test_resolved_or_rejected_complaint_cannot_change_again(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        resolved, _, _, _ = create_complaint()
        rejected, _, _, _ = create_complaint()
        ComplaintService.resolve(resolved.pk, manager.active_profile)
        ComplaintService.reject(rejected.pk, manager.active_profile)

        with pytest.raises(ValidationError):
            ComplaintService.reject(resolved.pk, manager.active_profile)
        with pytest.raises(ValidationError):
            ComplaintService.resolve(rejected.pk, manager.active_profile)


class TestComplaintCancellation:
    def test_creator_can_cancel_open_complaint(self):
        complaint, _, buyer, _ = create_complaint()

        cancelled = ComplaintService.cancel(
            complaint.pk,
            buyer.active_profile,
        )

        assert cancelled.status == ComplaintStatus.CANCELLED
        assert cancelled.cancelled_at is not None

    @pytest.mark.parametrize(
        "status",
        [
            ComplaintStatus.IN_REVIEW,
            ComplaintStatus.RESOLVED,
            ComplaintStatus.REJECTED,
        ],
    )
    def test_creator_cannot_cancel_non_open_complaint(self, status):
        complaint, _, buyer, _ = create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        if status == ComplaintStatus.IN_REVIEW:
            ComplaintService.mark_in_review(complaint.pk, manager.active_profile)
        elif status == ComplaintStatus.RESOLVED:
            ComplaintService.resolve(complaint.pk, manager.active_profile)
        else:
            ComplaintService.reject(complaint.pk, manager.active_profile)

        with pytest.raises(ValidationError):
            ComplaintService.cancel(complaint.pk, buyer.active_profile)

    def test_unrelated_user_cannot_cancel_complaint(self):
        complaint, _, _, _ = create_complaint()
        other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(NotFound):
            ComplaintService.cancel(complaint.pk, other.active_profile)


class TestComplaintApi:
    def test_buyer_can_create_complaint_through_api(self):
        payment_request, buyer, _ = create_payment_request()

        response = authenticated_client(buyer).post(
            "/api/complaints/",
            {
                "payment_request_id": payment_request.pk,
                "title": "API complaint",
                "description": "Created through API.",
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["status"] == ComplaintStatus.OPEN
        assert response.data["payment_request_id"] == payment_request.pk

    def test_seller_can_create_complaint_through_api(self):
        payment_request, _, seller = create_payment_request()

        response = authenticated_client(seller).post(
            "/api/complaints/",
            {
                "payment_request_id": payment_request.pk,
                "title": "Seller API complaint",
                "description": "Created by seller through API.",
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["created_by"]["id"] == seller.active_profile.pk

    def test_customer_my_list_only_returns_related_complaints(self):
        own, _, buyer, _ = create_complaint()
        other, _, _, _ = create_complaint()

        response = authenticated_client(buyer).get("/api/complaints/my/")

        assert response.status_code == 200
        ids = {row["id"] for row in response.data["results"]}
        assert own.pk in ids
        assert other.pk not in ids

    def test_seller_my_list_only_returns_related_complaints(self):
        own, _, _, seller = create_complaint()
        other, _, _, _ = create_complaint()

        response = authenticated_client(seller).get("/api/complaints/my/")

        assert response.status_code == 200
        ids = {row["id"] for row in response.data["results"]}
        assert own.pk in ids
        assert other.pk not in ids

    def test_manager_list_returns_all_complaints(self):
        create_complaint()
        create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

        response = authenticated_client(manager).get("/api/complaints/")

        assert response.status_code == 200
        assert response.data["count"] == 2

    def test_manager_can_resolve_complaint_through_api(self):
        complaint, _, _, _ = create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

        response = authenticated_client(manager).post(
            f"/api/complaints/{complaint.pk}/resolve/",
            {"admin_note": "Resolved by API."},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["status"] == ComplaintStatus.RESOLVED
        assert response.data["admin_note"] == "Resolved by API."

    def test_unrelated_user_cannot_retrieve_complaint(self):
        complaint, _, _, _ = create_complaint()
        other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        response = authenticated_client(other).get(f"/api/complaints/{complaint.pk}/")

        assert response.status_code == 404

    def test_unauthenticated_user_is_denied(self):
        response = APIClient().get("/api/complaints/my/")

        assert response.status_code == 401


class TestComplaintEvents:
    def test_complaint_state_changes_record_events(self):
        complaint, _, buyer, _ = create_complaint()
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

        ComplaintService.mark_in_review(complaint.pk, manager.active_profile)
        ComplaintService.resolve(complaint.pk, manager.active_profile)

        event_types = list(
            ComplaintEvent.objects.filter(complaint_id=complaint.pk)
            .order_by("created")
            .values_list("event_type", flat=True)
        )

        assert event_types == [
            ComplaintEventType.CREATED,
            ComplaintEventType.IN_REVIEW,
            ComplaintEventType.RESOLVED,
        ]
        assert Complaint.objects.get(pk=complaint.pk).created_by_id == (
            buyer.active_profile.pk
        )
