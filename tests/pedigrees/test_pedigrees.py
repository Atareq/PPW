from datetime import timedelta
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdSex,
    PedigreeCertificate,
    PedigreeCertificateStatus,
    PedigreeDocument,
    PedigreeDocumentStatus,
)
from apps.pedigrees.services import (
    PedigreeReportClient,
    PedigreeReportMapper,
    PedigreeReportResponse,
    PedigreeTreeService,
)
from apps.users.constants import ProfileType
from tests.factories import (
    PedigreeBirdFactory,
    PedigreeCertificateFactory,
    PedigreeDocumentFactory,
    PedigreeTemplateFactory,
    UserAccountFactory,
)

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


def test_upload_marks_document_failed_when_box_detection_raises(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    client = authenticated_client(user)

    with patch(
        "apps.pedigrees.services.PedigreeImageBoxDetectionService.detect_boxes",
        side_effect=RuntimeError("box detection unavailable"),
    ):
        response = client.post(
            "/api/pedigrees/documents/",
            {"original_file": uploaded_file()},
            format="multipart",
        )

    assert response.status_code == 400
    document = PedigreeDocument.objects.get()
    assert document.status == PedigreeDocumentStatus.FAILED
    assert "box detection unavailable" in document.processing_error
    assert response.data["document"]["id"] == document.pk


def test_upload_processes_mocked_box_ocr(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    client = authenticated_client(user)
    boxes = [{"box_id": "b1", "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}]
    boxes_with_lines = [{**boxes[0], "lines": ["EG-123"], "ocr_error": ""}]

    with patch(
        "apps.pedigrees.services.PedigreeImageBoxDetectionService.detect_boxes",
        return_value=boxes,
    ), patch(
        "apps.pedigrees.services.PedigreeBoxOCRService.extract_lines_for_boxes",
        return_value=boxes_with_lines,
    ):
        response = client.post(
            "/api/pedigrees/documents/",
            {"original_file": uploaded_file()},
            format="multipart",
        )

    assert response.status_code == 201
    document = PedigreeDocument.objects.get()
    assert document.status == PedigreeDocumentStatus.PROCESSED
    assert document.extracted_boxes == boxes_with_lines


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


def test_customer_can_create_pedigree_bird_and_owner_is_active_profile():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = authenticated_client(user)

    response = client.post(
        "/api/pedigrees/birds/",
        {"ring_number": " eg   2026  0007 ", "name": "Subject"},
        format="json",
    )

    assert response.status_code == 201
    bird = PedigreeBird.objects.get()
    assert bird.owner_id == user.active_profile.pk
    assert bird.normalized_ring_number == "EG 2026 0007"
    assert response.data["owner"] == user.active_profile.pk


def test_duplicate_live_ring_number_rejected_per_owner_but_allowed_for_other_owner():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PedigreeBirdFactory(owner=user.active_profile, ring_number="EG 1")

    duplicate = authenticated_client(user).post(
        "/api/pedigrees/birds/",
        {"ring_number": " eg   1 "},
        format="json",
    )
    allowed = authenticated_client(other).post(
        "/api/pedigrees/birds/",
        {"ring_number": "EG 1"},
        format="json",
    )

    assert duplicate.status_code == 400
    assert allowed.status_code == 201


def test_soft_deleted_ring_number_can_be_reused_for_same_owner():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    bird = PedigreeBirdFactory(owner=user.active_profile, ring_number="EG 2")
    bird.delete()

    response = authenticated_client(user).post(
        "/api/pedigrees/birds/",
        {"ring_number": "EG 2"},
        format="json",
    )

    assert response.status_code == 201


def test_parent_validation_rejects_wrong_sex_cross_owner_and_cycles():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    child = PedigreeBirdFactory(owner=user.active_profile, ring_number="CHILD")
    female = PedigreeBirdFactory(
        owner=user.active_profile,
        ring_number="FEMALE",
        sex=PedigreeBirdSex.FEMALE,
    )
    other_father = PedigreeBirdFactory(
        owner=other.active_profile,
        ring_number="OTHER",
        sex=PedigreeBirdSex.MALE,
    )
    client = authenticated_client(user)

    wrong_sex = client.patch(
        f"/api/pedigrees/birds/{child.pk}/",
        {"father": female.pk},
        format="json",
    )
    cross_owner = client.patch(
        f"/api/pedigrees/birds/{child.pk}/",
        {"father": other_father.pk},
        format="json",
    )

    parent = PedigreeBirdFactory(
        owner=user.active_profile,
        ring_number="PARENT",
        sex=PedigreeBirdSex.MALE,
    )
    child.father = parent
    child.save()
    cycle = client.patch(
        f"/api/pedigrees/birds/{parent.pk}/",
        {"father": child.pk},
        format="json",
    )

    assert wrong_sex.status_code == 400
    assert cross_owner.status_code == 400
    assert cycle.status_code == 400


def test_future_birthday_and_younger_parent_are_rejected():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    tomorrow = timezone.localdate() + timedelta(days=1)
    child_birthday = timezone.localdate()
    father = PedigreeBirdFactory(
        owner=user.active_profile,
        birthday=child_birthday,
        sex=PedigreeBirdSex.MALE,
    )
    client = authenticated_client(user)

    future = client.post(
        "/api/pedigrees/birds/",
        {"ring_number": "FUTURE", "birthday": tomorrow.isoformat()},
        format="json",
    )
    younger_parent = client.post(
        "/api/pedigrees/birds/",
        {
            "ring_number": "CHILD2",
            "birthday": child_birthday.isoformat(),
            "father": father.pk,
        },
        format="json",
    )

    assert future.status_code == 400
    assert younger_parent.status_code == 400


def test_owner_scoped_list_retrieve_update_and_delete():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    own = PedigreeBirdFactory(owner=user.active_profile, ring_number="OWN")
    foreign = PedigreeBirdFactory(owner=other.active_profile, ring_number="FOREIGN")
    client = authenticated_client(user)

    listed = client.get("/api/pedigrees/birds/")
    missing_detail = client.get(f"/api/pedigrees/birds/{foreign.pk}/")
    missing_update = client.patch(
        f"/api/pedigrees/birds/{foreign.pk}/",
        {"name": "Nope"},
        format="json",
    )
    own_delete = client.delete(f"/api/pedigrees/birds/{own.pk}/")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.data["results"]] == [own.pk]
    assert missing_detail.status_code == 404
    assert missing_update.status_code == 404
    assert own_delete.status_code == 204


def test_manager_pedigree_bird_list_requires_explicit_permission():
    owner = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PedigreeBirdFactory(owner=owner.active_profile)
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager_profile = manager.active_profile
    manager_profile.permissions = []
    manager_profile.save(update_fields=["permissions"])
    client = authenticated_client(manager)

    forbidden = client.get("/api/pedigrees/birds/")
    manager_profile.permissions = ["pedigrees.birds.list"]
    manager_profile.save(update_fields=["permissions"])
    allowed = client.get("/api/pedigrees/birds/")

    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert allowed.data["count"] == 1


def test_tree_builds_paths_and_null_missing_ancestors():
    owner = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile
    grand_father = PedigreeBirdFactory(
        owner=owner,
        ring_number="FF",
        sex=PedigreeBirdSex.MALE,
    )
    father = PedigreeBirdFactory(
        owner=owner,
        ring_number="F",
        sex=PedigreeBirdSex.MALE,
        father=grand_father,
    )
    subject = PedigreeBirdFactory(owner=owner, ring_number="S", father=father)
    template = PedigreeTemplateFactory(generation=3)

    editable = PedigreeTreeService.build_editable_data(
        subject_bird=subject,
        template=template,
    )
    lookup = PedigreeTreeService.build_path_lookup(editable, max_generation=3)

    assert editable["subject"]["father"]["father"]["ring_number"] == "FF"
    assert editable["subject"]["mother"] is None
    assert lookup["F"]["ring_number"] == "F"
    assert lookup["FF"]["ring_number"] == "FF"
    assert lookup["FM"] is None


def test_mapper_uses_backend_template_metadata_and_deepest_fields():
    certificate = PedigreeCertificateFactory(generation_revision=2)
    template = certificate.template
    subject = {
        "ring_number": "S",
        "name": "Subject",
        "father": {"ring_number": "F", "name": "Father"},
        "mother": {"ring_number": "M", "description": "Mother Desc"},
    }
    subject["father"]["father"] = {"ring_number": "FF", "name": "Grand Father"}
    current = subject["mother"]
    for _ in range(4):
        current["mother"] = {"ring_number": "MMMMM", "name": "Deep Mother"}
        current = current["mother"]
    snapshot = {
        "owner": {
            "name": "Owner",
            "loft_name": "Loft",
            "phone": "1",
            "whatsapp": "2",
            "email": "e@example.com",
        },
        "customization": {
            "background_image": "bg",
            "bird_image": "bird",
            "compuclub": "Club",
        },
        "subject": subject,
    }

    payload = PedigreeReportMapper.build_payload(
        certificate=certificate,
        template=template,
        tree_snapshot=snapshot,
    )
    params = payload["parameters"]

    assert payload["pedigreeType"] == template.pedigree_type
    assert payload["reportName"] == template.report_name
    assert payload["generation"] == str(template.generation)
    assert payload["dataSources"] == []
    assert params["Farkh_No"] == "S"
    assert params["Farth_No"] == "S"
    assert params["OwnerLoftName"] == "Loft"
    assert params["Owner_LoftName"] == "Loft"
    assert params["Farth_FatherNo"] == "F"
    assert params["Father_Info"] == "Father"
    assert params["Farth_MotherNo"] == "M"
    assert params["Mother_Info"] == "Mother Desc"
    assert params["Father1_NO"] == "FF"
    assert params["Mother6_16_NO"] == "MMMMM"
    assert params["Mother6_16"] == "Deep Mother"


def test_certificate_create_persists_editable_data_and_queues_task(
    settings,
    tmp_path,
    django_capture_on_commit_callbacks,
):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    bird = PedigreeBirdFactory(owner=user.active_profile, ring_number="EG-7")
    template = PedigreeTemplateFactory()
    client = authenticated_client(user)

    with patch("apps.pedigrees.tasks.generate_pedigree_certificate.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                "/api/pedigrees/certificates/",
                {
                    "subject_bird_id": bird.pk,
                    "template_id": template.pk,
                    "owner": {"name": "SAAD"},
                    "customization": {"compuclub": "Pigeon Planet"},
                },
                format="json",
            )

    certificate = PedigreeCertificate.objects.get()
    assert response.status_code == 202
    assert response.data["generation_revision"] == 1
    assert certificate.editable_data["owner"]["name"] == "SAAD"
    assert certificate.editable_data["subject"]["ring_number"] == "EG-7"
    delay.assert_called_once_with(certificate.pk, 1)


def test_certificate_retrieve_regenerate_and_download(
    settings,
    tmp_path,
    django_capture_on_commit_callbacks,
):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    certificate = PedigreeCertificateFactory(owner=user.active_profile)
    certificate.editable_data = PedigreeTreeService.build_editable_data(
        subject_bird=certificate.subject_bird,
        template=certificate.template,
    )
    certificate.save(update_fields=["editable_data"])
    client = authenticated_client(user)

    detail = client.get(f"/api/pedigrees/certificates/{certificate.pk}/")
    edited = detail.data["editable_data"]
    edited["subject"]["name"] = "Edited Subject"

    with patch("apps.pedigrees.tasks.generate_pedigree_certificate.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            regenerated = client.post(
                f"/api/pedigrees/certificates/{certificate.pk}/regenerate/",
                {"template_id": certificate.template_id, "editable_data": edited},
                format="json",
            )

    certificate.refresh_from_db()
    assert regenerated.status_code == 202
    assert certificate.generation_revision == 2
    assert certificate.editable_data["subject"]["name"] == "Edited Subject"
    delay.assert_called_once_with(certificate.pk, 2)

    unavailable = client.get(f"/api/pedigrees/certificates/{certificate.pk}/download/")
    assert unavailable.status_code == 400

    certificate.status = PedigreeCertificateStatus.COMPLETED
    certificate.mime_type = "application/pdf"
    certificate.generated_filename = "pedigree.pdf"
    certificate.generated_file.save("pedigree.pdf", ContentFile(b"%PDF-1.4"), save=True)

    downloaded = client.get(f"/api/pedigrees/certificates/{certificate.pk}/download/")
    assert downloaded.status_code == 200
    assert downloaded["Content-Type"] == "application/pdf"


def test_generation_success_failure_and_stale_revision(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    certificate = PedigreeCertificateFactory()
    certificate.editable_data = PedigreeTreeService.build_editable_data(
        subject_bird=certificate.subject_bird,
        template=certificate.template,
    )
    certificate.save(update_fields=["editable_data"])

    class FakeClient:
        def post_report(self, *, external_pdf_id, payload):
            assert external_pdf_id == certificate.template.external_pdf_id
            assert payload["dataSources"] == []
            return PedigreeReportResponse(
                content=b"%PDF-1.4",
                mime_type="application/pdf",
                status_code=200,
                extension=".pdf",
            )

    from apps.pedigrees.services import PedigreeCertificateService

    assert (
        PedigreeCertificateService.generate_certificate(
            certificate_id=certificate.pk,
            expected_revision=1,
            client=FakeClient(),
        )
        == "completed"
    )
    certificate.refresh_from_db()
    assert certificate.status == PedigreeCertificateStatus.COMPLETED
    assert certificate.generated_file
    assert certificate.tree_snapshot
    assert certificate.external_request_payload

    stale = PedigreeCertificateService.generate_certificate(
        certificate_id=certificate.pk,
        expected_revision=0,
        client=FakeClient(),
    )
    assert stale == "stale"

    failed = PedigreeCertificateFactory()
    failed.editable_data = PedigreeTreeService.build_editable_data(
        subject_bird=failed.subject_bird,
        template=failed.template,
    )
    failed.save(update_fields=["editable_data"])

    class BadClient:
        def post_report(self, *, external_pdf_id, payload):
            raise ValueError("bad response")

    assert (
        PedigreeCertificateService.generate_certificate(
            certificate_id=failed.pk,
            expected_revision=1,
            client=BadClient(),
        )
        == "failed"
    )
    failed.refresh_from_db()
    assert failed.status == PedigreeCertificateStatus.FAILED
    assert failed.editable_data
    assert failed.tree_snapshot
    assert failed.external_request_payload


def test_report_response_validation_accepts_pdf_png_jpeg_and_rejects_bad_content():
    assert (
        PedigreeReportClient.validate_response(
            content=b"%PDF-1.4",
            content_type="application/pdf",
        ).extension
        == ".pdf"
    )
    assert (
        PedigreeReportClient.validate_response(
            content=b"\x89PNG\r\n\x1a\npayload",
            content_type="image/png",
        ).extension
        == ".png"
    )
    assert (
        PedigreeReportClient.validate_response(
            content=b"\xff\xd8payload",
            content_type="image/jpeg",
        ).extension
        == ".jpg"
    )
    with pytest.raises(Exception):
        PedigreeReportClient.validate_response(
            content=b"<html></html>",
            content_type="text/html",
        )
