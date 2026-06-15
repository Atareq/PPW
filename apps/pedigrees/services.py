from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.assets.models import Bird
from apps.pedigrees.models import PedigreeDocument, PedigreeDocumentStatus
from apps.users.constants import ProfileType


class PedigreeExtractionError(ValueError):
    pass


class LocalOCRService:
    """Null OCR adapter; real local OCR is intentionally deferred."""

    def extract_text(self, file):
        return ""


class PedigreeExtractionService:
    @staticmethod
    def extract(raw_text):
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            raise PedigreeExtractionError(
                "OCR text did not contain any non-empty lines."
            )
        return {
            "bird_ring_number": lines[0],
            "description": "\n".join(lines[1:]),
        }


def pedigree_document_queryset():
    return PedigreeDocument.objects.select_related(
        "uploaded_by",
        "owner",
        "bird",
    ).order_by("-created")


class PedigreeDocumentService:
    @staticmethod
    def upload_and_process(
        *,
        uploaded_by,
        owner,
        original_file,
        bird_id=None,
        ocr_service=None,
    ):
        bird = PedigreeDocumentService._get_linked_bird(bird_id, owner)
        document = PedigreeDocument.objects.create(
            uploaded_by=uploaded_by,
            owner=owner,
            bird=bird,
            original_file=original_file,
            status=PedigreeDocumentStatus.UPLOADED,
        )

        ocr_service = ocr_service or LocalOCRService()
        try:
            raw_text = ocr_service.extract_text(document.original_file) or ""
        except Exception as exc:
            return PedigreeDocumentService._mark_failed(
                document,
                f"OCR processing failed: {exc}",
            )

        if not raw_text.strip():
            return PedigreeDocumentService._mark_failed(
                document,
                "OCR did not return any text.",
                raw_ocr_text=raw_text,
            )

        try:
            extracted = PedigreeExtractionService.extract(raw_text)
        except PedigreeExtractionError as exc:
            return PedigreeDocumentService._mark_failed(
                document,
                str(exc),
                raw_ocr_text=raw_text,
            )

        document.raw_ocr_text = raw_text
        document.extracted_bird_ring_number = extracted["bird_ring_number"]
        document.extracted_description = extracted["description"]
        document.status = PedigreeDocumentStatus.PROCESSED
        document.processing_error = ""
        document.save(
            update_fields=[
                "raw_ocr_text",
                "extracted_bird_ring_number",
                "extracted_description",
                "status",
                "processing_error",
                "modified",
            ]
        )
        return PedigreeDocumentService.get_document(document.pk)

    @staticmethod
    @transaction.atomic
    def review_document(
        *,
        document_id,
        actor_profile,
        bird_ring_number,
        description,
    ):
        document = PedigreeDocumentService._get_locked_document_for_actor(
            document_id,
            actor_profile,
        )
        document.reviewed_bird_ring_number = bird_ring_number
        document.reviewed_description = description
        document.status = PedigreeDocumentStatus.REVIEWED
        document.reviewed_at = timezone.now()
        document.save(
            update_fields=[
                "reviewed_bird_ring_number",
                "reviewed_description",
                "status",
                "reviewed_at",
                "modified",
            ]
        )
        # TODO: Update the linked Bird when a dedicated pedigree description
        # field exists; do not overwrite the generic Asset.description here.
        return PedigreeDocumentService.get_document(document.pk)

    @staticmethod
    def get_document(document_id):
        return pedigree_document_queryset().get(pk=document_id)

    @staticmethod
    def _mark_failed(document, message, raw_ocr_text=None):
        if raw_ocr_text is not None:
            document.raw_ocr_text = raw_ocr_text
        document.status = PedigreeDocumentStatus.FAILED
        document.processing_error = message
        update_fields = ["status", "processing_error", "modified"]
        if raw_ocr_text is not None:
            update_fields.append("raw_ocr_text")
        document.save(update_fields=update_fields)
        return PedigreeDocumentService.get_document(document.pk)

    @staticmethod
    def _get_linked_bird(bird_id, owner):
        if bird_id is None:
            return None

        queryset = Bird.objects.all()
        if owner.type != ProfileType.MANAGER:
            queryset = queryset.filter(owner_id=owner.pk)

        try:
            return queryset.get(pk=bird_id)
        except Bird.DoesNotExist as exc:
            raise NotFound("Bird was not found.") from exc

    @staticmethod
    def _get_locked_document_for_actor(document_id, actor_profile):
        queryset = PedigreeDocument.objects.select_for_update()
        if actor_profile.type != ProfileType.MANAGER:
            queryset = queryset.filter(owner_id=actor_profile.pk)

        try:
            return queryset.get(pk=document_id)
        except PedigreeDocument.DoesNotExist as exc:
            raise NotFound("Pedigree document was not found.") from exc
