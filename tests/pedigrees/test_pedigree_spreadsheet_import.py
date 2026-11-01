from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.assets.models import Bird
from apps.deals.models import CompletedDeal
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdLifeStatus,
    PedigreeBirdSex,
)
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile
from tests.factories import (
    ManagerProfileFactory,
    PedigreeBirdFactory,
    UserAccountFactory,
)


pytestmark = pytest.mark.django_db

IMPORT_URL = "/api/pedigrees/birds/import/"
ROOT_HEADERS = ["Estok_Type", "Estok_Notes", "Estok_No"]
DIRECT_HEADERS = [
    header
    for index in range(1, 8)
    for header in (
        f"Estok_Father{index}No",
        f"Estok_Father{index}",
        f"Estok_Mother{index}No",
        f"Estok_Mother{index}",
    )
]
DEEP_HEADERS = [
    header
    for index in range(1, 9)
    for header in (
        f"Estok_Father_Father{index}_N",
        f"Estok_Father_Father{index}_D",
        f"Estok_Father_Mother{index}_N",
        f"Estok_Father_Mother{index}_D",
    )
]
HEADERS = [
    *ROOT_HEADERS,
    *DIRECT_HEADERS,
    *DEEP_HEADERS,
    "C2",
    "Estok_Date",
    "stat",
    "nots",
]


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def spreadsheet_file(*rows, name="pedigrees.xlsx"):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(HEADERS)
    for data in rows:
        worksheet.append([data.get(header) for header in HEADERS])
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


def import_file(manager, target, *rows, **extra):
    return authenticated_client(manager).post(
        IMPORT_URL,
        {"file": spreadsheet_file(*rows), "user": target.pk, **extra},
        format="multipart",
    )


def manager_user(*, permissions=None):
    user = UserAccountFactory(with_profile=False)
    ManagerProfileFactory(
        user=user,
        is_active=True,
        permissions=(
            permissions if permissions is not None else ["pedigrees.birds.create"]
        ),
    )
    return user


def row(ring, sex="مجهول", **values):
    return {"Estok_No": ring, "Estok_Type": sex, **values}


def test_import_requires_manager_permission_and_exact_input_fields():
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    denied_manager = manager_user(permissions=[])
    manager = manager_user()

    assert import_file(customer, target, row("ROOT")).status_code == 403
    assert import_file(denied_manager, target, row("ROOT")).status_code == 403
    response = import_file(manager, target, row("ROOT"), unexpected="no")
    assert response.status_code == 400
    assert response.data["unexpected"] == ["This field is not supported."]


def test_import_rejects_missing_or_ambiguous_target_users_and_non_xlsx_files():
    manager = manager_user()
    no_profile = UserAccountFactory(with_profile=False)
    dual_profile = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    SellerProfile.objects.create(
        user=dual_profile,
        country="EG",
        currency="EGP",
    )

    assert import_file(manager, no_profile, row("ROOT")).status_code == 400
    assert import_file(manager, dual_profile, row("ROOT")).status_code == 400
    missing_user = authenticated_client(manager).post(
        IMPORT_URL,
        {"file": spreadsheet_file(row("ROOT"))},
        format="multipart",
    )
    assert missing_user.status_code == 400
    assert "user" in missing_user.data
    response = authenticated_client(manager).post(
        IMPORT_URL,
        {
            "file": SimpleUploadedFile("pedigrees.txt", b"not xlsx"),
            "user": no_profile.pk,
        },
        format="multipart",
    )
    assert response.status_code == 400
    assert response.data["file"] == ["A valid XLSX file is required."]
    invalid_xlsx = authenticated_client(manager).post(
        IMPORT_URL,
        {
            "file": SimpleUploadedFile("pedigrees.xlsx", b"not an xlsx"),
            "user": no_profile.pk,
        },
        format="multipart",
    )
    assert invalid_xlsx.status_code == 400
    assert str(invalid_xlsx.data["file"]) == "A valid XLSX file is required."


def test_import_creates_root_sex_descriptions_and_recursive_relationships():
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = import_file(
        manager,
        target,
        row(
            "child",
            "مجهول",
            Estok_Notes="Root description",
            Estok_Father1No="father",
            Estok_Father1="Father description",
            Estok_Mother1No="mother",
            Estok_Mother1="Mother description",
            Estok_Father2No="father-father",
            Estok_Mother2No="father-mother",
            Estok_Father3No="mother-father",
            Estok_Mother3No="mother-mother",
        ),
        row("father", "ذكر"),
        row("mother", "أنثى"),
    )

    assert response.status_code == 200
    assert response.data["rows_processed"] == 3
    assert response.data["birds_created"] == 7
    owner = target.profiles.get(type=ProfileType.CUSTOMER)
    child = PedigreeBird.objects.get(
        registered_by=owner, normalized_ring_number="CHILD"
    )
    father = PedigreeBird.objects.get(
        registered_by=owner, normalized_ring_number="FATHER"
    )
    mother = PedigreeBird.objects.get(
        registered_by=owner, normalized_ring_number="MOTHER"
    )
    assert child.description == "Root description"
    assert child.sex == PedigreeBirdSex.UNKNOWN
    assert father.sex == PedigreeBirdSex.MALE
    assert mother.sex == PedigreeBirdSex.FEMALE
    assert child.father_id == father.pk
    assert child.mother_id == mother.pk
    assert father.father.normalized_ring_number == "FATHER-FATHER"
    assert father.mother.normalized_ring_number == "FATHER-MOTHER"
    assert mother.father.normalized_ring_number == "MOTHER-FATHER"
    assert mother.mother.normalized_ring_number == "MOTHER-MOTHER"


def test_import_uses_binary_pairing_for_deep_generation_columns():
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = import_file(
        manager,
        target,
        row(
            "root",
            Estok_Father1No="n2",
            Estok_Mother1No="n3",
            Estok_Father2No="n4",
            Estok_Father3No="n6",
            Estok_Father4No="n8",
            Estok_Father_Father1_N="n16",
            Estok_Father_Mother1_N="n17",
        ),
    )

    assert response.status_code == 200
    owner = target.profiles.get(type=ProfileType.CUSTOMER)
    node_8 = PedigreeBird.objects.get(registered_by=owner, normalized_ring_number="N8")
    assert node_8.father.normalized_ring_number == "N16"
    assert node_8.mother.normalized_ring_number == "N17"


def test_import_reuses_owner_scoped_rings_sentinels_and_ignored_columns():
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other_bird = PedigreeBirdFactory(owner=other.active_profile, ring_number="SHARED")
    response = import_file(
        manager,
        target,
        row(
            "shared",
            Estok_Father1No="102",
            Estok_Mother1No="ffffffff",
            Estok_Father2No=0,
            Estok_Mother2No="00",
            Estok_Father3No="000",
            Estok_Mother3No="DV ",
            Estok_Father4No="NL",
            Estok_Mother4No="EGY",
            Estok_Father5No="ابيض",
            Estok_Mother5No="ازازيه",
            Estok_Father6No="مفصصه مخوص",
            C2="bad-formatting",
            Estok_Date="2025-01-01",
            stat=999,
            nots="Ignored duplicate note",
        ),
    )

    assert response.status_code == 200
    owner = target.profiles.get(type=ProfileType.CUSTOMER)
    imported = PedigreeBird.objects.get(
        registered_by=owner, normalized_ring_number="SHARED"
    )
    assert imported.father_id is None
    assert imported.mother_id is None
    assert imported.pk != other_bird.pk
    assert PedigreeBird.objects.filter(registered_by=owner).count() == 1
    assert response.data["ignored_legacy_values"] >= 14


def test_import_prefers_root_description_and_tolerates_ancestor_note_variations():
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = import_file(
        manager,
        target,
        row(
            "child-1",
            Estok_Father1No="EG 16-2284",
            Estok_Father1="أب السوبر",
            Estok_Mother1No="EG 17-3399",
            Estok_Father2No="NLD 05-1412614",
            Estok_Father2="تاشا 614",
            Estok_Father3No="NLD 05-1412614",
            Estok_Father3="تاشا  614 نسخة ثانية",
        ),
        row(
            "child-2",
            Estok_Father1No="EG 16-2284",
            Estok_Father1="أب  السوبر",
        ),
        row(
            "EG 16-2284",
            "ذكر",
            Estok_Notes="الوصف الرسمي للأب",
        ),
    )

    assert response.status_code == 200
    owner = target.profiles.get(type=ProfileType.CUSTOMER)
    sire = PedigreeBird.objects.get(
        registered_by=owner, normalized_ring_number="EG 16-2284"
    )
    ancestor = PedigreeBird.objects.get(
        registered_by=owner,
        normalized_ring_number="NLD 05-1412614",
    )
    assert sire.sex == PedigreeBirdSex.MALE
    assert sire.description == "الوصف الرسمي للأب"
    assert ancestor.description == "تاشا 614"


def test_import_reuses_existing_birds_is_idempotent_and_has_no_commerce_side_effects():
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    first = import_file(
        manager,
        target,
        row("root", Estok_Father1No="parent"),
        row("parent", "ذكر"),
    )
    second = import_file(
        manager,
        target,
        row("root", Estok_Father1No="parent"),
        row("parent", "ذكر"),
    )

    assert first.status_code == second.status_code == 200
    assert second.data["birds_created"] == 0
    assert second.data["birds_reused"] == 2
    assert second.data["relationships_created"] == 0
    assert second.data["relationships_unchanged"] == 1
    assert PedigreeBird.objects.filter(registered_by__user=target).count() == 2
    assert Bird.objects.count() == 0
    assert CompletedDeal.objects.count() == 0


def test_import_rejects_unknown_supported_sex_atomically():
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = import_file(manager, target, row("root", sex="legacy-code"))

    assert response.status_code == 400
    assert PedigreeBird.objects.filter(registered_by__user=target).count() == 0


@pytest.mark.parametrize(
    "first_parent, second_parent",
    [
        ("Estok_Father1No", "Estok_Father1No"),
        ("Estok_Mother1No", "Estok_Mother1No"),
    ],
)
def test_import_rejects_conflicting_parents_atomically(first_parent, second_parent):
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = import_file(
        manager,
        target,
        row("root", **{first_parent: "one"}),
        row("root", **{second_parent: "two"}),
    )

    assert response.status_code == 400
    assert PedigreeBird.objects.filter(registered_by__user=target).count() == 0


@pytest.mark.parametrize(
    "field",
    ["Estok_Father1No", "Estok_Mother1No"],
)
def test_import_rejects_self_parent_and_rolls_back(field):
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = import_file(manager, target, row("root", **{field: "root"}))

    assert response.status_code == 400
    assert PedigreeBird.objects.filter(registered_by__user=target).count() == 0


def test_import_rejects_cycles_using_existing_pedigree_validator():
    manager = manager_user()
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    response = import_file(
        manager,
        target,
        row("a", Estok_Father1No="b"),
        row("b", Estok_Father1No="a"),
    )

    assert response.status_code == 400
    assert PedigreeBird.objects.filter(registered_by__user=target).count() == 0


def test_import_creates_unidentified_birds_and_keeps_reused_life_status():
    target = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    owner = target.active_profile
    reused = PedigreeBirdFactory(
        owner=owner,
        ring_number="REUSED",
        life_status=PedigreeBirdLifeStatus.DECEASED,
    )
    untouched = PedigreeBirdFactory(
        owner=owner,
        ring_number="UNTOUCHED",
        life_status=PedigreeBirdLifeStatus.ALIVE,
    )

    response = import_file(
        manager_user(),
        target,
        row("NEW", Estok_Father1No="REUSED"),
    )

    assert response.status_code == 200
    assert response.data["birds_created"] == 1
    assert response.data["birds_reused"] == 1
    new_bird = PedigreeBird.objects.get(
        registered_by=owner, normalized_ring_number="NEW"
    )
    reused.refresh_from_db()
    untouched.refresh_from_db()
    assert new_bird.life_status == PedigreeBirdLifeStatus.UNIDENTIFIED
    assert reused.life_status == PedigreeBirdLifeStatus.DECEASED
    assert untouched.life_status == PedigreeBirdLifeStatus.ALIVE
