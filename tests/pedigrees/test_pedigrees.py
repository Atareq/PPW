from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.loft_subscriptions.models import (
    LoftSubscription,
    PedigreeTemplatePackage,
    SubscriptionDurationOption,
    SubscriptionInvoice,
    SubscriptionInvoiceStatus,
)
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdSex,
    PedigreeDocument,
    PedigreeDocumentStatus,
    RegisteredPedigree,
)
from apps.pedigrees.services import (
    PedigreeProviderDiagnosticService,
    PedigreeProviderSyntheticPayloadBuilder,
    PedigreeReportClient,
    PedigreeReportMapper,
    PedigreeTreeService,
)
from apps.users.constants import ProfileType
from tests.factories import (
    ManagerProfileFactory,
    PedigreeBirdFactory,
    PedigreeDocumentFactory,
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


class UpstreamResponse:
    def __init__(self, *, content, status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}


def test_upload_marks_document_failed_when_box_detection_raises(
    settings, tmp_path
):
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


def reviewed_birds_payload():
    return {
        "birds": [
            {
                "path": "",
                "box_id": "b1",
                "ring_number": "EGY 21-3033 H",
                "name": "ZOMORODA",
                "description": "Subject information",
            },
            {
                "path": "F",
                "box_id": "b2",
                "ring_number": "EGY 16-761 C",
                "description": "Father information",
                "sex": "male",
            },
            {
                "path": "M",
                "box_id": "b3",
                "ring_number": "KT 19-1035 H",
                "description": "Mother information",
                "sex": "female",
            },
            {
                "path": "FF",
                "box_id": "b4",
                "ring_number": "EGY 13-310 C",
            },
        ]
    }


def test_review_saves_full_pedigree_graph_and_reviewed_fields(
    settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    boxes = [{"box_id": "b1", "lines": ["OCR"], "ocr_error": ""}]
    document = PedigreeDocumentFactory(
        owner=user.active_profile,
        uploaded_by=user,
        extracted_boxes=boxes,
    )
    client = authenticated_client(user)

    response = client.patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        reviewed_birds_payload(),
        format="json",
    )

    assert response.status_code == 200
    document.refresh_from_db()
    assert document.status == PedigreeDocumentStatus.REVIEWED
    assert document.reviewed_at is not None
    assert document.reviewed_bird_ring_number == "EGY 21-3033 H"
    assert document.reviewed_description == "Subject information"
    assert document.extracted_boxes == boxes
    assert (
        response.data["subject_bird_id"]
        == response.data["path_to_bird_id"][""]
    )
    assert response.data["created_count"] == 4
    assert response.data["reused_count"] == 0
    assert response.data["updated_count"] == 0

    subject = PedigreeBird.objects.get(ring_number="EGY 21-3033 H")
    father = PedigreeBird.objects.get(ring_number="EGY 16-761 C")
    mother = PedigreeBird.objects.get(ring_number="KT 19-1035 H")
    paternal_grandfather = PedigreeBird.objects.get(ring_number="EGY 13-310 C")
    assert subject.owner_id == user.active_profile.pk
    assert subject.father_id == father.pk
    assert subject.mother_id == mother.pk
    assert father.father_id == paternal_grandfather.pk
    assert "box_id" not in subject.__dict__


@pytest.mark.parametrize(
    "payload",
    [
        {"birds": [{"path": "F", "ring_number": "F"}]},
        {
            "birds": [
                {"path": "", "ring_number": "S"},
                {"path": "", "ring_number": "S2"},
            ]
        },
        {
            "birds": [
                {"path": "", "ring_number": "S"},
                {"path": "FX", "ring_number": "X"},
            ]
        },
        {
            "birds": [
                {"path": "", "ring_number": "S"},
                {"path": "FFFFFF", "ring_number": "X"},
            ]
        },
        {
            "birds": [
                {"path": "", "ring_number": "S"},
                {"path": "FF", "ring_number": "X"},
            ]
        },
        {"bird_ring_number": "OLD", "description": "Old payload"},
    ],
)
def test_review_rejects_invalid_structured_payloads(payload):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    document = PedigreeDocumentFactory(
        owner=user.active_profile, uploaded_by=user
    )
    response = authenticated_client(user).patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        payload,
        format="json",
    )

    assert response.status_code == 400


def test_review_reuses_and_updates_same_owner_birds_without_clearing_omitted_fields():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    owner = user.active_profile
    subject = PedigreeBirdFactory(
        owner=owner,
        ring_number="EGY 21-3033 H",
        name="Old",
        description="Existing description",
    )
    father = PedigreeBirdFactory(owner=owner, ring_number="EGY 16-761 C")
    existing_mother = PedigreeBirdFactory(
        owner=owner, ring_number="OLD MOTHER"
    )
    subject.mother = existing_mother
    subject.save(update_fields=["mother", "modified"])
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PedigreeBirdFactory(owner=other.active_profile, ring_number="KT 19-1035 H")
    document = PedigreeDocumentFactory(owner=owner, uploaded_by=user)

    response = authenticated_client(user).patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        {
            "birds": [
                {
                    "path": "",
                    "ring_number": " egy   21-3033   h ",
                    "name": "",
                },
                {"path": "F", "ring_number": "EGY 16-761 C"},
                {"path": "M", "ring_number": "KT 19-1035 H"},
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    subject.refresh_from_db()
    assert subject.name == ""
    assert subject.description == "Existing description"
    assert subject.father_id == father.pk
    assert subject.mother.normalized_ring_number == "KT 19-1035 H"
    assert subject.mother.owner_id == owner.pk
    assert response.data["created_count"] == 1
    assert response.data["updated_count"] == 1


def test_review_repeated_compatible_ring_reuses_one_bird_and_conflicts_fail():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    document = PedigreeDocumentFactory(
        owner=user.active_profile, uploaded_by=user
    )
    client = authenticated_client(user)

    compatible = client.patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        {
            "birds": [
                {"path": "", "ring_number": "S"},
                {"path": "F", "ring_number": "P", "name": "Shared"},
                {"path": "M", "ring_number": "P", "name": "Shared"},
            ]
        },
        format="json",
    )

    assert compatible.status_code == 400

    document = PedigreeDocumentFactory(
        owner=user.active_profile, uploaded_by=user
    )
    conflict = client.patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        {
            "birds": [
                {"path": "", "ring_number": "S2"},
                {"path": "F", "ring_number": "P2", "name": "One"},
                {"path": "M", "ring_number": "P2", "name": "Two"},
            ]
        },
        format="json",
    )

    assert conflict.status_code == 400


def test_review_repeated_ancestor_with_compatible_parents_reuses_one_bird():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    document = PedigreeDocumentFactory(
        owner=user.active_profile, uploaded_by=user
    )

    response = authenticated_client(user).patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        {
            "birds": [
                {"path": "", "ring_number": "S"},
                {"path": "F", "ring_number": "F"},
                {"path": "M", "ring_number": "M"},
                {"path": "FF", "ring_number": "SHARED"},
                {"path": "MF", "ring_number": "SHARED"},
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    assert (
        response.data["path_to_bird_id"]["FF"]
        == response.data["path_to_bird_id"]["MF"]
    )
    assert PedigreeBird.objects.filter(owner=user.active_profile).count() == 4


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
    foreign = PedigreeBirdFactory(
        owner=other.active_profile, ring_number="FOREIGN"
    )
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
    owner = UserAccountFactory(
        with_profile=ProfileType.CUSTOMER
    ).active_profile
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

    generation_data = PedigreeTreeService.build_generation_data(
        subject_bird=subject,
        generation_depth=3,
    )
    lookup = PedigreeTreeService.build_path_lookup(
        generation_data,
        max_generation=3,
    )

    assert (
        generation_data["subject"]["father"]["father"]["ring_number"] == "FF"
    )
    assert generation_data["subject"]["mother"] is None
    assert lookup["F"]["ring_number"] == "F"
    assert lookup["FF"]["ring_number"] == "FF"
    assert lookup["FM"] is None


def test_generation_depth_treats_subject_as_depth_one():
    owner = UserAccountFactory(
        with_profile=ProfileType.CUSTOMER
    ).active_profile
    grand_father = PedigreeBirdFactory(owner=owner, ring_number="FF")
    father = PedigreeBirdFactory(
        owner=owner, ring_number="F", father=grand_father
    )
    mother = PedigreeBirdFactory(owner=owner, ring_number="M")
    subject = PedigreeBirdFactory(
        owner=owner,
        ring_number="S",
        father=father,
        mother=mother,
    )

    depth_1 = PedigreeTreeService.build_generation_data(
        subject_bird=subject,
        generation_depth=1,
    )
    depth_2 = PedigreeTreeService.build_generation_data(
        subject_bird=subject,
        generation_depth=2,
    )
    depth_3 = PedigreeTreeService.build_generation_data(
        subject_bird=subject,
        generation_depth=3,
    )

    assert depth_1["subject"]["father"] is None
    assert depth_2["subject"]["father"]["ring_number"] == "F"
    assert depth_2["subject"]["father"]["father"] is None
    assert depth_3["subject"]["father"]["father"]["ring_number"] == "FF"


def test_mapper_merges_local_bird_data_and_preserves_frontend_report():
    owner = UserAccountFactory(
        with_profile=ProfileType.CUSTOMER
    ).active_profile
    deep = PedigreeBirdFactory(owner=owner, ring_number="FFFF")
    grand_father = PedigreeBirdFactory(
        owner=owner,
        ring_number="FF",
        name="Grand Father",
        father=deep,
    )
    father = PedigreeBirdFactory(
        owner=owner,
        ring_number="F",
        name="Father",
        description="Father",
        father=grand_father,
    )
    mother = PedigreeBirdFactory(
        owner=owner,
        ring_number="M",
        name="",
        description="Mother Desc",
    )
    subject = PedigreeBirdFactory(
        owner=owner,
        ring_number="S",
        name="Subject",
        description="Subject",
        father=father,
        mother=mother,
    )
    report = {
        "generation": 4,
        "fileName": "Pedigree_07_G4.pdf",
        "parameters": {
            "Farkh_No": "frontend subject",
            "Father1_NO": "frontend grand father",
            "Father1_Name": "frontend grand father name",
            "Father4_NO": "too deep",
            "Father1_NO_TextColor": "#FFFFFF",
            "Father1_NO_BackColor": "#1D4ED8",
            "Father1_NO_BoxBackColor": "#EFF6FF",
            "Father1_NO_BorderColor": "#1E3A8A",
            "Father1_TextColor": "#14532D",
            "Father1_BackColor": "#DCFCE7",
            "BoxShape": "RoundedRectangle",
            "CustomFutureField": "value",
        },
        "dataSources": [{"name": "future"}],
        "unknownTopLevel": True,
    }

    payload = PedigreeReportMapper.merge_payload(
        frontend_report=report,
        subject_bird=subject,
        generation_depth=3,
        owner={
            "name": "Owner",
            "loft_name": "Loft",
            "phone": "1",
            "whatsapp": "2",
            "email": "e@example.com",
        },
        customization={
            "background_image": "bg",
            "bird_image": "bird",
            "compuclub": "Club",
        },
    )
    params = payload["parameters"]

    assert payload["generation"] == 3
    assert payload["fileName"] == "Pedigree_07_G4.pdf"
    assert payload["dataSources"] == [{"name": "future"}]
    assert payload["unknownTopLevel"] is True
    assert params["Farkh_No"] == "S"
    assert params["Farth_No"] == "S"
    assert params["Esmah_FullNo"] == "Subject"
    assert params["OwnerLoftName"] == "Loft"
    assert params["Owner_LoftName"] == "Loft"
    assert params["Farth_FatherNo"] == "F"
    assert params["Father_Info"] == "Father"
    assert params["Farth_MotherNo"] == "M"
    assert params["Mother_Info"] == "Mother Desc"
    assert params["Father1_NO"] == "FF"
    assert params["Father1_Name"] == "Grand Father"
    assert params["Father4_NO"] == ""
    assert params["Father1_NO_TextColor"] == "#FFFFFF"
    assert params["Father1_NO_BackColor"] == "#1D4ED8"
    assert params["Father1_NO_BoxBackColor"] == "#EFF6FF"
    assert params["Father1_NO_BorderColor"] == "#1E3A8A"
    assert params["Father1_TextColor"] == "#14532D"
    assert params["Father1_BackColor"] == "#DCFCE7"
    assert params["BoxShape"] == "RoundedRectangle"
    assert params["CustomFutureField"] == "value"
    assert params["BackgroundImage"] == "bg"
    assert params["Farkh_Image"] == "bird"
    assert params["Compuclub"] == "Club"
    assert "PrintDate" in params


def test_reports_proxy_uses_settings_url_and_returns_upstream_response(
    settings,
):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test/"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    settings.PEDIGREE_REPORT_TIMEOUT = 7
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    client = authenticated_client(user)

    upstream = UpstreamResponse(
        content=b'{"reports":[{"id":7}]}',
        status_code=200,
        headers={
            "Content-Type": "application/json",
            "Content-Disposition": "inline",
        },
    )
    with patch("requests.get", return_value=upstream) as get:
        response = client.get("/api/pedigrees/templates/")

    get.assert_called_once_with(
        "http://provider.test/api/reports/fastreport/reports",
        headers={"Accept": "application/json", "X-Api-Key": "provider-key"},
        timeout=7,
        allow_redirects=False,
    )
    assert response.status_code == 200
    assert response.content == b'{"reports":[{"id":7}]}'
    assert response["Content-Type"] == "application/json"
    assert response["Content-Disposition"] == "inline"


@pytest.mark.parametrize(
    "content, status_code, content_type",
    [
        (b'{"error":"bad"}', 400, "application/json"),
        (b"Report failed", 500, "text/plain"),
    ],
)
def test_reports_proxy_preserves_upstream_error_response(
    settings, content, status_code, content_type
):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    upstream = UpstreamResponse(
        content=content,
        status_code=status_code,
        headers={"Content-Type": content_type},
    )
    with patch("requests.get", return_value=upstream):
        response = authenticated_client(user).get("/api/pedigrees/templates/")

    assert response.status_code == status_code
    assert response.content == content
    assert response["Content-Type"] == content_type


def test_schema_proxy_validates_generation_and_returns_upstream_response(
    settings,
):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test/"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    settings.PEDIGREE_REPORT_TIMEOUT = 8
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    RegisteredPedigree.objects.create(pedigree_id=4)
    upstream = UpstreamResponse(
        content=b'{"parameters":["Farth_No"]}',
        status_code=200,
        headers={
            "Content-Type": "application/json",
            "ETag": '"schema"',
        },
    )

    with patch("requests.get", return_value=upstream) as get:
        response = authenticated_client(user).get(
            "/api/pedigrees/templates/4/schema/?generation=3"
        )

    get.assert_called_once_with(
        "http://provider.test/api/reports/fastreport/schema/4",
        params={"generation": 3},
        headers={"Accept": "application/json", "X-Api-Key": "provider-key"},
        timeout=8,
        allow_redirects=False,
    )
    assert response.status_code == 200
    assert response.content == b'{"parameters":["Farth_No"]}'
    assert response["Content-Type"] == "application/json"
    assert response["ETag"] == '"schema"'

    invalid = authenticated_client(user).get(
        "/api/pedigrees/templates/4/schema/?generation=1"
    )
    assert invalid.status_code == 400


def test_style_parameters_proxy_uses_provider_query_names(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    RegisteredPedigree.objects.create(pedigree_id=13)
    upstream = UpstreamResponse(
        content=b'{"styleParameters":["TextColor"]}',
        status_code=200,
        headers={"Content-Type": "application/json"},
    )

    with patch("requests.get", return_value=upstream) as get:
        response = authenticated_client(user).get(
            "/api/pedigrees/templates/13/style-parameters/?generation=4"
        )

    get.assert_called_once_with(
        "http://provider.test/api/reports/fastreport/style-parameters",
        params={"pedigreeType": 13, "generation": 4},
        headers={"Accept": "application/json", "X-Api-Key": "provider-key"},
        timeout=120,
        allow_redirects=False,
    )
    assert response.status_code == 200
    assert response.content == b'{"styleParameters":["TextColor"]}'


def generation_request(subject_id, *, generation_depth=3):
    return {
        "subject_bird_id": subject_id,
        "pdf_id": 7,
        "generation_depth": generation_depth,
        "report": {
            "generation": 4,
            "fileName": "Pedigree_07_G4.pdf",
            "parameters": {
                "Farkh_No": "frontend",
                "BackgroundImage": "frontend-bg",
                "CustomFutureField": "value",
            },
            "dataSources": [{"name": "future"}],
        },
        "owner": {
            "name": "TAMER TOHAMY",
            "loft_name": "TAMER LOFT",
            "phone": "",
            "whatsapp": "",
            "email": "",
        },
        "customization": {
            "background_image": "",
            "bird_image": "bird.png",
            "compuclub": "Pigeon Planet",
        },
    }


def create_report_subscription(owner, *, template_ids=None, unlimited=False):
    duration, _created = SubscriptionDurationOption.objects.get_or_create(
        code=f"month_1_{owner.pk}_{unlimited}",
        defaults={
            "name": "One Month",
            "duration_months": 1,
            "price": "500.00",
        },
    )
    package, _created = PedigreeTemplatePackage.objects.get_or_create(
        code=f"test_package_{owner.pk}_{unlimited}",
        defaults={
            "name": "Test Package",
            "template_limit": None if unlimited else 1,
            "is_unlimited": unlimited,
            "price": "0.00",
        },
    )
    invoice = SubscriptionInvoice.objects.create(
        owner=owner,
        status=SubscriptionInvoiceStatus.PAID,
        currency="EGP",
        subtotal="500.00",
        total="500.00",
        duration=duration,
        pedigree_package=package,
        selected_template_ids=template_ids or [7],
        selection_snapshot={},
        pricing_snapshot={},
        idempotency_key=f"pedigree-test-{owner.pk}-{LoftSubscription.objects.count()}",
        paid_at=timezone.now(),
        activated_at=timezone.now(),
    )
    return LoftSubscription.objects.create(
        owner=owner,
        invoice=invoice,
        starts_at=timezone.now() - timedelta(minutes=1),
        ends_at=timezone.now() + timedelta(days=30),
        selected_template_ids=template_ids or [7],
        pedigree_template_limit=None if unlimited else 1,
        pedigree_unlimited=unlimited,
    )


def test_generate_requires_accessible_subject_bird_and_blocks_other_owner(
    settings,
):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    foreign_bird = PedigreeBirdFactory(owner=other.active_profile)
    create_report_subscription(user.active_profile)
    RegisteredPedigree.objects.create(pedigree_id=7)

    with patch("requests.post") as post:
        response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(foreign_bird.pk),
            format="json",
        )

    assert response.status_code == 404
    post.assert_not_called()


def test_generate_calls_provider_and_returns_pdf_unchanged(
    settings, tmp_path
):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test/"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    settings.PEDIGREE_REPORT_TIMEOUT = 9
    settings.MEDIA_ROOT = tmp_path
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    owner = user.active_profile
    create_report_subscription(owner)
    RegisteredPedigree.objects.create(pedigree_id=7)
    grand_father = PedigreeBirdFactory(
        owner=owner,
        ring_number="FF",
        name="Grand Father",
    )
    father = PedigreeBirdFactory(
        owner=owner,
        ring_number="F",
        name="Father",
        description="Father",
        father=grand_father,
    )
    subject = PedigreeBirdFactory(
        owner=owner,
        ring_number="S",
        name="Subject",
        description="Subject",
        father=father,
    )
    upstream = UpstreamResponse(
        content=b"%PDF-1.4 bytes",
        status_code=200,
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="report.pdf"',
        },
    )

    with patch("requests.post", return_value=upstream) as post:
        response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(subject.pk, generation_depth=3),
            format="json",
            HTTP_ACCEPT="image/png",
        )

    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "http://provider.test/api/reports/fastreport/pdf/7"
    assert kwargs["timeout"] == 9
    assert kwargs["headers"] == {
        "Content-Type": "application/json",
        "Accept": "image/png",
        "X-Api-Key": "provider-key",
    }
    payload = kwargs["json"]
    params = payload["parameters"]
    assert payload["generation"] == 3
    assert payload["fileName"] == "Pedigree_07_G4.pdf"
    assert payload["dataSources"] == [{"name": "future"}]
    assert params["Farkh_No"] == "S"
    assert params["Esmah_FullNo"] == "Subject"
    assert params["Farth_FatherNo"] == "F"
    assert params["Father_Info"] == "Father"
    assert params["Father1_NO"] == "FF"
    assert params["CustomFutureField"] == "value"
    assert params["OwnerLoftName"] == "TAMER LOFT"
    assert params["BackgroundImage"] == ""
    assert params["Farkh_Image"] == "bird.png"
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 bytes"
    assert response["Content-Type"] == "application/pdf"
    assert (
        response["Content-Disposition"] == 'attachment; filename="report.pdf"'
    )
    assert list(tmp_path.rglob("*")) == []


def test_generate_depth_excludes_deeper_ancestors(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    owner = user.active_profile
    create_report_subscription(owner)
    RegisteredPedigree.objects.create(pedigree_id=7)
    grand_father = PedigreeBirdFactory(owner=owner, ring_number="FF")
    father = PedigreeBirdFactory(
        owner=owner, ring_number="F", father=grand_father
    )
    subject = PedigreeBirdFactory(owner=owner, ring_number="S", father=father)
    upstream = UpstreamResponse(
        content=b"\x89PNG\r\n\x1a\npayload",
        status_code=200,
        headers={"Content-Type": "image/png"},
    )
    with patch("requests.post", return_value=upstream) as post:
        response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(subject.pk, generation_depth=2),
            format="json",
        )

    assert response.status_code == 200
    params = post.call_args.kwargs["json"]["parameters"]
    assert params["Farth_FatherNo"] == "F"
    assert params["Father1_NO"] == ""


@pytest.mark.parametrize(
    "content, status_code, content_type",
    [
        (b'{"error":"bad"}', 400, "application/json"),
        (b"Report failed", 500, "text/plain"),
    ],
)
def test_generate_preserves_provider_error_responses(
    settings, content, status_code, content_type
):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    create_report_subscription(user.active_profile)
    RegisteredPedigree.objects.create(pedigree_id=7)
    subject = PedigreeBirdFactory(owner=user.active_profile)
    upstream = UpstreamResponse(
        content=content,
        status_code=status_code,
        headers={"Content-Type": content_type},
    )
    with patch("requests.post", return_value=upstream):
        response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(subject.pk),
            format="json",
        )

    assert response.status_code == status_code
    assert response.content == content
    assert response["Content-Type"] == content_type


def test_generate_network_errors_return_small_local_responses(settings):
    import requests

    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    create_report_subscription(user.active_profile)
    RegisteredPedigree.objects.create(pedigree_id=7)
    subject = PedigreeBirdFactory(owner=user.active_profile)

    with patch("requests.post", side_effect=requests.exceptions.Timeout):
        timeout_response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(subject.pk),
            format="json",
        )
    with patch(
        "requests.post", side_effect=requests.exceptions.ConnectionError
    ):
        gateway_response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(subject.pk),
            format="json",
        )

    assert timeout_response.status_code == 504
    assert gateway_response.status_code == 502


def test_no_provider_ip_is_hardcoded_in_application_python_files():
    from pathlib import Path

    python_files = list(Path("apps").rglob("*.py")) + list(
        Path("ppw").rglob("*.py")
    )
    assert not any(
        "157.245.92.15" in path.read_text(encoding="utf-8")
        for path in python_files
    )


def test_report_persistence_models_and_task_are_removed():
    import importlib.util

    from apps.pedigrees import models

    assert not hasattr(models, "PedigreeTemplate")
    assert not hasattr(models, "PedigreeCertificate")
    assert importlib.util.find_spec("apps.pedigrees.tasks") is None
    table_names = connection.introspection.table_names()
    assert "pedigrees_pedigreetemplate" not in table_names
    assert "pedigrees_pedigreecertificate" not in table_names


def test_synthetic_provider_payload_is_complete_deterministic_and_query_free():
    with CaptureQueriesContext(connection) as queries:
        payload = PedigreeProviderSyntheticPayloadBuilder.build()

    parameters = payload["parameters"]
    assert len(queries) == 0
    assert payload["generation"] == 6
    assert payload["fileName"] == "PPW_Provider_Test_S04_G6.pdf"
    assert payload["dataSources"] == []
    assert isinstance(parameters, dict)

    assert parameters["Farkh_No"] == "PPW-TEST-ROOT"
    assert parameters["Esmah_Name"] == "PPW Synthetic Subject"
    assert parameters["Esmah_FullNo"].startswith(
        "PPW synthetic provider diagnostic record."
    )
    assert parameters["Farth_FatherNo"] == "PPW-TEST-F"
    assert parameters["Father_Name"] == "PPW Synthetic Father"
    assert parameters["Father_Info"].startswith(
        "PPW synthetic provider diagnostic record."
    )
    assert parameters["Farth_MotherNo"] == "PPW-TEST-M"
    assert parameters["Mother_Name"] == "PPW Synthetic Mother"
    assert parameters["Mother_Info"].startswith(
        "PPW synthetic provider diagnostic record."
    )
    assert parameters["Father1_NO"] == "PPW-TEST-FF"
    assert parameters["Father1_Name"] == "PPW Synthetic Ancestor FF"
    assert parameters["Father1"].startswith(
        "PPW synthetic provider diagnostic record."
    )
    assert parameters["Father6_01_NO"] == "PPW-TEST-FFFFF"
    assert parameters["Father6_01_Name"] == (
        "PPW Synthetic Ancestor FFFFF"
    )
    assert parameters["Father6_01"].startswith(
        "PPW synthetic provider diagnostic record."
    )
    assert parameters["Mother6_16_NO"] == "PPW-TEST-MMMMM"
    assert parameters["Mother6_16_Name"] == (
        "PPW Synthetic Ancestor MMMMM"
    )
    assert parameters["Mother6_16"].startswith(
        "PPW synthetic provider diagnostic record."
    )

    assert parameters["Father1_NO_TextColor"] == "#FFFFFF"
    assert parameters["Father1_NO_BackColor"] == "#1D4ED8"
    assert parameters["Father1_NO_BoxBackColor"] == "#EFF6FF"
    assert parameters["Father1_NO_BorderColor"] == "#1E3A8A"
    assert parameters["Father1_NO_BoxShape"] == ""
    assert parameters["BackgroundImage"] == ""
    assert parameters["Farkh_Image"] == ""
    assert parameters["Details_Pic"] == ""
    assert "subject_bird_id" not in payload
    assert "owner_id" not in parameters
    assert not any(key.endswith("_id") for key in parameters)


def test_health_client_calls_provider_health_without_api_key(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test/"
    settings.PEDIGREE_REPORT_TIMEOUT = 5
    upstream = UpstreamResponse(
        content=b'{"status":"Healthy"}',
        status_code=200,
        headers={"Content-Type": "application/json"},
    )

    with patch("requests.get", return_value=upstream) as get:
        response = PedigreeReportClient().get_health()

    get.assert_called_once_with(
        "http://provider.test/health",
        headers={"Accept": "application/json"},
        timeout=5,
        allow_redirects=False,
    )
    assert response.status_code == 200
    assert response.content == b'{"status":"Healthy"}'


def provider_pdf_bytes():
    return b"%PDF-1.4\n" + (b"x" * 1200)


def test_provider_diagnostic_endpoint_success(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    health = UpstreamResponse(
        content=b'{"status":"Healthy"}',
        status_code=200,
        headers={"Content-Type": "application/json"},
    )
    pdf = UpstreamResponse(
        content=provider_pdf_bytes(),
        status_code=200,
        headers={"Content-Type": "application/pdf"},
    )

    with patch("requests.get", return_value=health), patch(
        "requests.post", return_value=pdf
    ) as post:
        response = authenticated_client(manager).post(
            "/api/pedigrees/provider-test/",
            {},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["status"] == "healthy"
    assert response.data["health"]["ok"] is True
    assert response.data["health"]["provider_status"] == "Healthy"
    assert response.data["pedigree_generation"]["ok"] is True
    assert response.data["pedigree_generation"]["valid_pdf"] is True
    assert response.data["pedigree_generation"]["shape"] == 4
    assert response.data["pedigree_generation"]["generation"] == 6
    assert isinstance(
        response.data["health"]["response_time_seconds"], float
    )
    assert isinstance(
        response.data["pedigree_generation"]["response_time_seconds"], float
    )
    assert isinstance(response.data["total_response_time_seconds"], float)
    payload = post.call_args.kwargs["json"]
    assert payload["generation"] == 6
    assert payload["parameters"]["Father6_01_NO"] == "PPW-TEST-FFFFF"


def test_provider_diagnostic_is_manager_only(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    manager = UserAccountFactory(with_profile=False)

    ManagerProfileFactory(user=manager, is_active=True, permissions=[])

    customer_response = authenticated_client(customer).post(
        "/api/pedigrees/provider-test/",
        {},
        format="json",
    )
    manager_response = authenticated_client(manager).post(
        "/api/pedigrees/provider-test/",
        {},
        format="json",
    )

    assert customer_response.status_code == 403
    assert manager_response.status_code == 403


def test_provider_diagnostic_rejects_input_payload(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)

    with patch("requests.get") as get, patch("requests.post") as post:
        response = authenticated_client(manager).post(
            "/api/pedigrees/provider-test/",
            {"subject_bird_id": 1},
            format="json",
        )

    assert response.status_code == 400
    get.assert_not_called()
    post.assert_not_called()


def test_provider_diagnostic_degraded_when_generation_http_fails(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    health = UpstreamResponse(
        content=b'{"status":"Healthy"}',
        status_code=200,
        headers={"Content-Type": "application/json"},
    )
    failed_pdf = UpstreamResponse(
        content=b'{"error":"failed"}',
        status_code=500,
        headers={"Content-Type": "application/json"},
    )

    with patch("requests.get", return_value=health), patch(
        "requests.post", return_value=failed_pdf
    ):
        result = PedigreeProviderDiagnosticService.run()

    assert result["status"] == "degraded"
    assert result["health"]["ok"] is True
    assert result["pedigree_generation"]["ok"] is False
    assert result["pedigree_generation"]["error"] == (
        "provider_generation_failed"
    )


def test_provider_diagnostic_degraded_when_generation_pdf_is_invalid(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    health = UpstreamResponse(
        content=b'{"status":"Healthy"}',
        status_code=200,
        headers={"Content-Type": "application/json"},
    )
    invalid_pdf = UpstreamResponse(
        content=b"not a pdf",
        status_code=200,
        headers={"Content-Type": "text/plain"},
    )

    with patch("requests.get", return_value=health), patch(
        "requests.post", return_value=invalid_pdf
    ):
        result = PedigreeProviderDiagnosticService.run()

    assert result["status"] == "degraded"
    assert result["pedigree_generation"]["valid_pdf"] is False
    assert result["pedigree_generation"]["error"] == "invalid_pdf"


def test_provider_diagnostic_degraded_when_health_fails_but_generation_succeeds(
    settings,
):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    health = UpstreamResponse(
        content=b'{"status":"Down"}',
        status_code=503,
        headers={"Content-Type": "application/json"},
    )
    pdf = UpstreamResponse(
        content=provider_pdf_bytes(),
        status_code=200,
        headers={"Content-Type": "application/pdf"},
    )

    with patch("requests.get", return_value=health), patch(
        "requests.post", return_value=pdf
    ):
        result = PedigreeProviderDiagnosticService.run()

    assert result["status"] == "degraded"
    assert result["health"]["ok"] is False
    assert result["health"]["provider_status"] == "Down"
    assert result["pedigree_generation"]["ok"] is True


def test_provider_diagnostic_unhealthy_when_both_calls_connection_error(
    settings,
):
    import requests

    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"

    with patch(
        "requests.get", side_effect=requests.exceptions.ConnectionError
    ), patch(
        "requests.post", side_effect=requests.exceptions.ConnectionError
    ):
        result = PedigreeProviderDiagnosticService.run()

    assert result["status"] == "unhealthy"
    assert result["health"]["error"] == "connection_error"
    assert result["pedigree_generation"]["error"] == "connection_error"


def test_provider_diagnostic_unhealthy_when_both_calls_timeout(settings):
    import requests

    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"

    with patch("requests.get", side_effect=requests.exceptions.Timeout), patch(
        "requests.post", side_effect=requests.exceptions.Timeout
    ):
        result = PedigreeProviderDiagnosticService.run()

    assert result["status"] == "unhealthy"
    assert result["health"]["error"] == "timeout"
    assert result["pedigree_generation"]["error"] == "timeout"


def test_provider_diagnostic_unhealthy_when_provider_config_missing(settings):
    settings.PEDIGREE_REPORT_BASE_URL = ""
    settings.PEDIGREE_REPORT_API_KEY = ""

    with patch("requests.get") as get, patch("requests.post") as post:
        result = PedigreeProviderDiagnosticService.run()

    assert result["status"] == "unhealthy"
    assert result["health"]["error"] == "configuration_error"
    assert result["pedigree_generation"]["error"] == "configuration_error"
    get.assert_not_called()
    post.assert_not_called()


@pytest.mark.parametrize(
    "response, valid",
    [
        (
            UpstreamResponse(
                content=provider_pdf_bytes(),
                status_code=200,
                headers={"Content-Type": "application/pdf"},
            ),
            True,
        ),
        (
            UpstreamResponse(
                content=provider_pdf_bytes(),
                status_code=200,
                headers={"Content-Type": "text/plain"},
            ),
            False,
        ),
        (
            UpstreamResponse(
                content=b"",
                status_code=200,
                headers={"Content-Type": "application/pdf"},
            ),
            False,
        ),
    ],
)
def test_provider_diagnostic_pdf_validation(response, valid):
    assert (
        PedigreeProviderDiagnosticService.is_valid_pdf_response(response)
        is valid
    )
