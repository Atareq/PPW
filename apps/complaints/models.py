from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class ComplaintType(models.TextChoices):
    POST_SALE = "post_sale", _("Post Sale")
    PAYMENT_REJECTED = "payment_rejected", _("Payment Rejected")
    OTHER = "other", _("Other")


class ComplaintStatus(models.TextChoices):
    OPEN = "open", _("Open")
    IN_REVIEW = "in_review", _("In Review")
    RESOLVED = "resolved", _("Resolved")
    REJECTED = "rejected", _("Rejected")
    CANCELLED = "cancelled", _("Cancelled")


class ComplaintEventType(models.TextChoices):
    CREATED = "complaint_created", _("Complaint Created")
    IN_REVIEW = "complaint_in_review", _("Complaint In Review")
    RESOLVED = "complaint_resolved", _("Complaint Resolved")
    REJECTED = "complaint_rejected", _("Complaint Rejected")
    REOPENED = "complaint_reopened", _("Complaint Reopened")
    CANCELLED = "complaint_cancelled", _("Complaint Cancelled")


class Complaint(BaseModel):
    ACTIVE_STATUSES = [
        ComplaintStatus.OPEN,
        ComplaintStatus.IN_REVIEW,
    ]

    payment_request = models.ForeignKey(
        "payments.PaymentRequest",
        on_delete=models.PROTECT,
        related_name="complaints",
    )
    created_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="created_complaints",
    )
    buyer = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="complaints_as_buyer",
    )
    seller = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="complaints_as_seller",
    )
    complaint_type = models.CharField(
        max_length=30,
        choices=ComplaintType.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=ComplaintStatus.choices,
        default=ComplaintStatus.OPEN,
    )
    title = models.CharField(max_length=255)
    description = models.TextField()
    admin_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_complaints",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["payment_request"],
                name="complaint_payment_idx",
            ),
            models.Index(
                fields=["created_by"],
                name="complaint_creator_idx",
            ),
            models.Index(fields=["buyer"], name="complaint_buyer_idx"),
            models.Index(fields=["seller"], name="complaint_seller_idx"),
            models.Index(fields=["status"], name="complaint_status_idx"),
            models.Index(
                fields=["complaint_type"],
                name="complaint_type_idx",
            ),
            models.Index(fields=["created"], name="complaint_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["payment_request"],
                condition=models.Q(
                    status__in=[
                        ComplaintStatus.OPEN,
                        ComplaintStatus.IN_REVIEW,
                    ],
                    deleted__isnull=True,
                ),
                name="complaints_one_active_per_pr",
            ),
        ]

    def __str__(self):
        return f"Complaint #{self.pk} ({self.status})"


class ComplaintEvent(BaseModel):
    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(
        max_length=40,
        choices=ComplaintEventType.choices,
    )
    actor = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaint_events",
    )
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["complaint", "event_type"],
                name="complaint_event_type_idx",
            ),
            models.Index(
                fields=["actor"],
                name="complaint_event_actor_idx",
            ),
            models.Index(
                fields=["created"],
                name="complaint_event_created_idx",
            ),
        ]

    def __str__(self):
        return f"ComplaintEvent #{self.pk} ({self.event_type})"
