from django.conf import settings
from django.db import models

from apps.general.models import OwnerBaseModel


class PedigreeDocumentStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    PROCESSED = "processed", "Processed"
    REVIEWED = "reviewed", "Reviewed"
    FAILED = "failed", "Failed"


class PedigreeDocument(OwnerBaseModel):
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pedigree_documents",
    )
    bird = models.ForeignKey(
        "assets.Bird",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedigree_documents",
    )
    original_file = models.FileField(upload_to="pedigree_documents/")
    status = models.CharField(
        max_length=20,
        choices=PedigreeDocumentStatus.choices,
        default=PedigreeDocumentStatus.UPLOADED,
    )
    raw_ocr_text = models.TextField(blank=True)
    extracted_bird_ring_number = models.CharField(max_length=100, blank=True)
    extracted_description = models.TextField(blank=True)
    reviewed_bird_ring_number = models.CharField(max_length=100, blank=True)
    reviewed_description = models.TextField(blank=True)
    processing_error = models.TextField(blank=True)
    extracted_boxes = models.JSONField(default=list, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)


    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["owner"], name="pedigree_doc_owner_idx"),
            models.Index(
                fields=["uploaded_by"],
                name="pedigree_doc_user_idx",
            ),
            models.Index(fields=["bird"], name="pedigree_doc_bird_idx"),
            models.Index(fields=["status"], name="pedigree_doc_status_idx"),
            models.Index(fields=["created"], name="pedigree_doc_created_idx"),
        ]

    def __str__(self):
        return f"PedigreeDocument #{self.pk} ({self.status})"
