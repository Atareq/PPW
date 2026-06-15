from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.pedigrees.models import PedigreeDocument, PedigreeDocumentStatus
from apps.pedigrees.services import (
    PedigreeExtractionError,
    PedigreeExtractionService,
)
from apps.users.constants import ProfileType
from tests.factories import PedigreeDocumentFactory, UserAccountFactory

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def uploaded_file(name="pedigree.jpg"):
    return SimpleUploadedFile(
        name,
        b"fake image bytes",
        content_type="image/jpeg",
    )


def test_first_line_extraction_uses_remaining_lines_as_description():
    result = PedigreeExtractionService.extract(
        "EG-12345-2024\nFather: Blue Champion\nMother: Silver Star"
    )

    assert result["bird_ring_number"] == "EG-12345-2024"
    assert result["description"] == "Father: Blue Champion\nMother: Silver Star"


def test_empty_ocr_result_raises_clear_extraction_error():
    with pytest.raises(PedigreeExtractionError):
        PedigreeExtractionService.extract(" \n\n\t ")


def test_upload_marks_document_failed_when_ocr_raises(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    client = authenticated_client(user)

    with patch(
        "apps.pedigrees.services.LocalOCRService.extract_text",
        side_effect=RuntimeError("OCR unavailable"),
    ):
        response = client.post(
            "/api/pedigrees/documents/",
            {"original_file": uploaded_file()},
            format="multipart",
        )

    assert response.status_code == 400
    document = PedigreeDocument.objects.get()
    assert document.status == PedigreeDocumentStatus.FAILED
    assert "OCR unavailable" in document.processing_error
    assert response.data["document"]["id"] == document.pk


def test_upload_processes_mocked_ocr_text(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    client = authenticated_client(user)

    with patch(
        "apps.pedigrees.services.LocalOCRService.extract_text",
        return_value="EG-12345-2024\nFather: Blue Champion",
    ):
        response = client.post(
            "/api/pedigrees/documents/",
            {"original_file": uploaded_file()},
            format="multipart",
        )

    assert response.status_code == 201
    document = PedigreeDocument.objects.get()
    assert document.status == PedigreeDocumentStatus.PROCESSED
    assert document.raw_ocr_text == "EG-12345-2024\nFather: Blue Champion"
    assert document.extracted_bird_ring_number == "EG-12345-2024"
    assert document.extracted_description == "Father: Blue Champion"


def test_review_updates_status_and_reviewed_fields(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    document = PedigreeDocumentFactory(
        owner=user.active_profile,
        uploaded_by=user,
    )
    client = authenticated_client(user)

    response = client.patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        {
            "bird_ring_number": "EG-CORRECTED-2024",
            "description": "Corrected pedigree details.",
        },
        format="json",
    )

    assert response.status_code == 200
    document.refresh_from_db()
    assert document.status == PedigreeDocumentStatus.REVIEWED
    assert document.reviewed_at is not None
    assert document.reviewed_bird_ring_number == "EG-CORRECTED-2024"
    assert document.reviewed_description == "Corrected pedigree details."
