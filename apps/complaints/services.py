from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.complaints.models import (
    Complaint,
    ComplaintEvent,
    ComplaintEventType,
    ComplaintStatus,
    ComplaintType,
)
from apps.payments.models import PaymentRequest, PaymentRequestStatus
from apps.users.constants import ProfileType


def complaint_queryset():
    """Optimized queryset for complaint list and detail responses."""

    return (
        Complaint.objects.select_related(
            "payment_request",
            "payment_request__asset",
            "created_by",
            "buyer",
            "seller",
            "reviewed_by",
        )
        .prefetch_related("events")
        .order_by("-created")
    )


class ComplaintService:
    """Manual complaint workflow for approved or rejected payment requests."""

    REVIEWABLE_STATUSES = [
        ComplaintStatus.OPEN,
        ComplaintStatus.IN_REVIEW,
    ]

    @staticmethod
    @transaction.atomic
    def create_complaint(
        payment_request_id,
        actor_profile,
        title,
        description,
        complaint_type=None,
    ):
        payment_request = ComplaintService._get_locked_payment_request(
            payment_request_id
        )

        if actor_profile.pk not in {
            payment_request.buyer_id,
            payment_request.seller_id,
        }:
            raise NotFound(_("Payment request was not found."))

        if payment_request.status not in {
            PaymentRequestStatus.APPROVED,
            PaymentRequestStatus.REJECTED,
        }:
            raise ValidationError(
                _("Complaints can only be created for approved or rejected payment requests.")
            )

        ComplaintService._validate_no_active_complaint(payment_request.pk)
        complaint_type = complaint_type or ComplaintService._default_type_for(
            payment_request
        )

        complaint = Complaint.objects.create(
            payment_request=payment_request,
            created_by=actor_profile,
            buyer=payment_request.buyer,
            seller=payment_request.seller,
            complaint_type=complaint_type,
            status=ComplaintStatus.OPEN,
            title=title,
            description=description,
        )
        ComplaintService._log_event(
            complaint,
            ComplaintEventType.CREATED,
            actor=actor_profile,
        )
        return ComplaintService.get_complaint(complaint.pk)

    @staticmethod
    @transaction.atomic
    def mark_in_review(complaint_id, manager_profile):
        ComplaintService._ensure_manager(manager_profile)
        complaint = ComplaintService._get_locked_complaint(complaint_id)

        if complaint.status != ComplaintStatus.OPEN:
            raise ValidationError(_("Only open complaints can move to review."))

        now = timezone.now()
        complaint.status = ComplaintStatus.IN_REVIEW
        complaint.reviewed_by = manager_profile
        complaint.reviewed_at = now
        complaint.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "modified",
            ]
        )
        ComplaintService._log_event(
            complaint,
            ComplaintEventType.IN_REVIEW,
            actor=manager_profile,
        )
        ComplaintService._schedule_status_notifications(
            complaint,
            actor=manager_profile,
            title=_("Complaint in review"),
            body=_("Your complaint is now in review."),
            idempotency_suffix=complaint.status,
        )
        return ComplaintService.get_complaint(complaint.pk)

    @staticmethod
    @transaction.atomic
    def resolve(complaint_id, manager_profile, admin_note=None):
        ComplaintService._ensure_manager(manager_profile)
        complaint = ComplaintService._get_locked_complaint(complaint_id)

        if complaint.status not in ComplaintService.REVIEWABLE_STATUSES:
            raise ValidationError(_("Only open or in-review complaints can be resolved."))

        now = timezone.now()
        update_fields = ["status", "resolved_at", "modified"]
        complaint.status = ComplaintStatus.RESOLVED
        complaint.resolved_at = now
        if complaint.reviewed_by_id is None:
            complaint.reviewed_by = manager_profile
            update_fields.append("reviewed_by")
        if complaint.reviewed_at is None:
            complaint.reviewed_at = now
            update_fields.append("reviewed_at")
        if admin_note is not None:
            complaint.admin_note = admin_note
            update_fields.append("admin_note")

        complaint.save(update_fields=update_fields)
        ComplaintService._log_event(
            complaint,
            ComplaintEventType.RESOLVED,
            actor=manager_profile,
        )
        ComplaintService._schedule_status_notifications(
            complaint,
            actor=manager_profile,
            title=_("Complaint resolved"),
            body=_("A manager resolved the complaint tied to your payment request."),
            idempotency_suffix=complaint.status,
        )
        return ComplaintService.get_complaint(complaint.pk)

    @staticmethod
    @transaction.atomic
    def reject(complaint_id, manager_profile, admin_note=None):
        ComplaintService._ensure_manager(manager_profile)
        complaint = ComplaintService._get_locked_complaint(complaint_id)

        if complaint.status not in ComplaintService.REVIEWABLE_STATUSES:
            raise ValidationError(_("Only open or in-review complaints can be rejected."))

        now = timezone.now()
        update_fields = ["status", "modified"]
        complaint.status = ComplaintStatus.REJECTED
        if complaint.reviewed_by_id is None:
            complaint.reviewed_by = manager_profile
            update_fields.append("reviewed_by")
        if complaint.reviewed_at is None:
            complaint.reviewed_at = now
            update_fields.append("reviewed_at")
        if admin_note is not None:
            complaint.admin_note = admin_note
            update_fields.append("admin_note")

        complaint.save(update_fields=update_fields)
        ComplaintService._log_event(
            complaint,
            ComplaintEventType.REJECTED,
            actor=manager_profile,
        )
        ComplaintService._schedule_status_notifications(
            complaint,
            actor=manager_profile,
            title=_("Complaint rejected"),
            body=_("A manager rejected the complaint tied to your payment request."),
            idempotency_suffix=complaint.status,
        )
        return ComplaintService.get_complaint(complaint.pk)

    @staticmethod
    @transaction.atomic
    def reopen_complaint(complaint_id, manager_profile):
        ComplaintService._ensure_manager(manager_profile)
        complaint = ComplaintService._get_locked_complaint(complaint_id)

        if complaint.status not in {
            ComplaintStatus.RESOLVED,
            ComplaintStatus.REJECTED,
        }:
            raise ValidationError(
                _("Only resolved or rejected complaints can be reopened.")
            )

        complaint.status = ComplaintStatus.OPEN
        complaint.admin_note = ""
        complaint.resolved_at = None
        complaint.save(
            update_fields=[
                "status",
                "admin_note",
                "resolved_at",
                "modified",
            ]
        )
        ComplaintService._log_event(
            complaint,
            ComplaintEventType.REOPENED,
            actor=manager_profile,
        )
        ComplaintService._schedule_status_notifications(
            complaint,
            actor=manager_profile,
            title=_("Complaint reopened"),
            body=_("A manager reopened the complaint tied to your payment request."),
            idempotency_suffix=f"reopened:{complaint.modified.isoformat()}",
        )
        return ComplaintService.get_complaint(complaint.pk)

    @staticmethod
    @transaction.atomic
    def cancel(complaint_id, actor_profile):
        complaint = ComplaintService._get_locked_complaint(complaint_id)

        if complaint.created_by_id != actor_profile.pk:
            raise NotFound(_("Complaint was not found."))

        if complaint.status != ComplaintStatus.OPEN:
            raise ValidationError(_("Only open complaints can be cancelled."))

        complaint.status = ComplaintStatus.CANCELLED
        complaint.cancelled_at = timezone.now()
        complaint.save(
            update_fields=[
                "status",
                "cancelled_at",
                "modified",
            ]
        )
        ComplaintService._log_event(
            complaint,
            ComplaintEventType.CANCELLED,
            actor=actor_profile,
        )
        return ComplaintService.get_complaint(complaint.pk)

    @staticmethod
    def get_complaint(complaint_id):
        return complaint_queryset().get(pk=complaint_id)

    @staticmethod
    def _get_locked_payment_request(payment_request_id):
        try:
            return (
                PaymentRequest.objects.select_for_update()
                .select_related("buyer", "seller", "asset")
                .get(pk=payment_request_id)
            )
        except PaymentRequest.DoesNotExist as exc:
            raise NotFound(_("Payment request was not found.")) from exc

    @staticmethod
    def _get_locked_complaint(complaint_id):
        try:
            return Complaint.objects.select_for_update().get(pk=complaint_id)
        except Complaint.DoesNotExist as exc:
            raise NotFound(_("Complaint was not found.")) from exc

    @staticmethod
    def _validate_no_active_complaint(payment_request_id):
        if Complaint.objects.filter(
            payment_request_id=payment_request_id,
            status__in=Complaint.ACTIVE_STATUSES,
        ).exists():
            raise ValidationError(
                _("An active complaint already exists for this payment request.")
            )

    @staticmethod
    def _default_type_for(payment_request):
        if payment_request.status == PaymentRequestStatus.APPROVED:
            return ComplaintType.POST_SALE
        if payment_request.status == PaymentRequestStatus.REJECTED:
            return ComplaintType.PAYMENT_REJECTED
        return ComplaintType.OTHER

    @staticmethod
    def _ensure_manager(profile):
        if getattr(profile, "type", None) != ProfileType.MANAGER:
            raise PermissionDenied(_("Only manager profiles can review complaints."))

    @staticmethod
    def _log_event(complaint, event_type, *, actor=None, metadata=None):
        ComplaintEvent.objects.create(
            complaint=complaint,
            event_type=event_type,
            actor=actor,
            metadata=metadata or {},
        )

    @staticmethod
    def _schedule_status_notifications(
        complaint,
        *,
        actor,
        title,
        body,
        idempotency_suffix,
    ):
        from apps.notifications.services import NotificationService

        def notify():
            for recipient in (complaint.buyer, complaint.seller):
                NotificationService.safe_create_notification(
                    recipient_profile=recipient,
                    actor_profile=actor,
                    notification_type="complaint_status_updated",
                    title=title,
                    body=body,
                    object_type="complaint",
                    object_id=complaint.pk,
                    metadata={
                        "complaint_id": complaint.pk,
                        "payment_request_id": complaint.payment_request_id,
                    },
                    idempotency_key=(
                        f"complaint_status_updated:{complaint.pk}:"
                        f"{idempotency_suffix}:{recipient.pk}"
                    ),
                )

        transaction.on_commit(notify)
