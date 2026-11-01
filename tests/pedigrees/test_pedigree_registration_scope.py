from unittest.mock import patch
import uuid

from django.db import IntegrityError, transaction
import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdOwnershipStatus,
    PedigreeBirdSex,
    RegisteredPedigree,
)
from apps.pedigrees.services import PedigreeBirdValidator
from apps.users.constants import ProfileType
from tests.factories import (
    PedigreeBirdFactory,
    PedigreeDocumentFactory,
    UserAccountFactory,
)
from tests.pedigrees.test_pedigree_spreadsheet_import import (
    import_file,
    manager_user,
    row,
)
from tests.pedigrees.test_pedigrees import (
    UpstreamResponse,
    create_report_subscription,
    generation_request,
)

pytestmark = pytest.mark.django_db

OWNED = PedigreeBirdOwnershipStatus.OWNED
UNKNOWN = PedigreeBirdOwnershipStatus.UNKNOWN
BIRDS_URL = "/api/pedigrees/birds/"


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def customer():
    return UserAccountFactory(with_profile=ProfileType.CUSTOMER)


def manager(*permissions):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    profile = user.active_profile
    profile.permissions = list(permissions)
    profile.save(update_fields=["permissions"])
    return user


def unowned_bird(registered_by, **kwargs):
    return PedigreeBirdFactory(owner=None, registered_by=registered_by, **kwargs)


def bird_url(bird):
    return f"{BIRDS_URL}{bird.pk}/"


def ownership_url(bird, command):
    return f"{BIRDS_URL}public/{bird.public_id}/{command}/"


def tree_nodes(generations, prefix):
    """Review nodes for a full tree: subject, parents, grandparents, ..."""
    nodes = [{"path": "", "ring_number": f"{prefix} S"}]
    frontier = [""]
    for _ in range(generations - 1):
        frontier = [path + edge for path in frontier for edge in "FM"]
        nodes.extend(
            {
                "path": path,
                "ring_number": f"{prefix} {path}",
                "sex": "male" if path.endswith("F") else "female",
            }
            for path in frontier
        )
    return nodes


def review(user, document, nodes):
    return authenticated_client(user).patch(
        f"/api/pedigrees/documents/{document.pk}/review/",
        {"birds": nodes},
        format="json",
    )


def assert_owned_by(bird, profile):
    assert bird.owner_id == profile.pk
    assert bird.ownership_status == OWNED


def assert_unowned(bird):
    assert bird.owner_id is None
    assert bird.ownership_status == UNKNOWN


# Registration


def test_registered_by_is_required_and_owner_is_nullable():
    profile = customer().active_profile

    assert PedigreeBird._meta.get_field("owner").null is True
    assert PedigreeBird._meta.get_field("registered_by").null is False
    with pytest.raises(IntegrityError), transaction.atomic():
        PedigreeBird.objects.create(owner=profile, ring_number="NO REGISTRANT")


def test_manual_create_without_owner_is_unowned_and_registered_by_active_profile():
    user = customer()

    response = authenticated_client(user).post(
        BIRDS_URL, {"ring_number": "S"}, format="json"
    )

    assert response.status_code == 201
    bird = PedigreeBird.objects.get()
    assert bird.registered_by_id == user.active_profile.pk
    assert_unowned(bird)
    assert response.data["owner"] is None
    assert response.data["registered_by"] == user.active_profile.pk
    assert response.data["ownership_status"] == UNKNOWN


def test_manual_create_with_own_profile_as_owner_is_owned():
    user = customer()
    profile = user.active_profile

    response = authenticated_client(user).post(
        BIRDS_URL, {"ring_number": "S", "owner": profile.pk}, format="json"
    )

    assert response.status_code == 201
    assert_owned_by(PedigreeBird.objects.get(), profile)


def test_manual_create_rejects_other_owner_and_owned_status_without_owner():
    user = customer()
    other = customer()
    client = authenticated_client(user)

    other_owner = client.post(
        BIRDS_URL,
        {"ring_number": "S1", "owner": other.active_profile.pk},
        format="json",
    )
    owned_without_owner = client.post(
        BIRDS_URL,
        {"ring_number": "S2", "ownership_status": OWNED},
        format="json",
    )

    assert other_owner.status_code == 400
    assert "owner" in other_owner.data
    assert owned_without_owner.status_code == 400
    assert "ownership_status" in owned_without_owner.data
    assert not PedigreeBird.objects.exists()


def test_client_cannot_inject_registered_by():
    user = customer()
    other = customer()
    client = authenticated_client(user)

    created = client.post(
        BIRDS_URL,
        {"ring_number": "S", "registered_by": other.active_profile.pk},
        format="json",
    )
    bird = PedigreeBird.objects.get()
    updated = client.patch(
        bird_url(bird),
        {"registered_by": other.active_profile.pk},
        format="json",
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    bird.refresh_from_db()
    assert bird.registered_by_id == user.active_profile.pk


# Visibility


def test_registered_by_controls_visibility_including_unowned_ancestors():
    user = customer()
    other = customer()
    profile = user.active_profile
    subject = PedigreeBirdFactory(owner=profile, ring_number="S")
    ancestor = unowned_bird(profile, ring_number="F")
    foreign = PedigreeBirdFactory(owner=other.active_profile, ring_number="X")
    owned_elsewhere = PedigreeBirdFactory(
        owner=profile,
        registered_by=other.active_profile,
        ring_number="Y",
    )
    client = authenticated_client(user)

    listed = client.get(BIRDS_URL)
    ancestor_detail = client.get(bird_url(ancestor))
    ancestor_update = client.patch(
        bird_url(ancestor), {"name": "Sire"}, format="json"
    )

    assert {row["id"] for row in listed.data["results"]} == {
        subject.pk,
        ancestor.pk,
    }
    assert ancestor_detail.status_code == 200
    assert ancestor_detail.data["owner"] is None
    assert ancestor_detail.data["registered_by"] == profile.pk
    assert ancestor_detail.data["ownership_status"] == UNKNOWN
    assert ancestor_update.status_code == 200
    assert client.get(bird_url(foreign)).status_code == 404
    assert client.get(bird_url(owned_elsewhere)).status_code == 404
    assert authenticated_client(other).get(bird_url(ancestor)).status_code == 404
    assert client.delete(bird_url(ancestor)).status_code == 204


def test_owner_id_filter_only_narrows_the_registration_scope():
    user = customer()
    other = customer()
    profile = user.active_profile
    subject = PedigreeBirdFactory(owner=profile, ring_number="S")
    unowned_bird(profile, ring_number="F")
    PedigreeBirdFactory(
        owner=profile,
        registered_by=other.active_profile,
        ring_number="ELSEWHERE",
    )
    PedigreeBirdFactory(owner=other.active_profile, ring_number="FOREIGN")
    client = authenticated_client(user)

    owned = client.get(BIRDS_URL, {"owner_id": profile.pk})
    other_owner = client.get(BIRDS_URL, {"owner_id": other.active_profile.pk})

    assert [row["id"] for row in owned.data["results"]] == [subject.pk]
    assert other_owner.data["results"] == []


def test_ring_uniqueness_follows_registered_by_not_owner():
    user = customer()
    other = customer()
    unowned_bird(user.active_profile, ring_number="EG 1")

    duplicate = authenticated_client(user).post(
        BIRDS_URL, {"ring_number": " eg  1 "}, format="json"
    )
    other_registrant = authenticated_client(other).post(
        BIRDS_URL, {"ring_number": "EG 1"}, format="json"
    )

    assert duplicate.status_code == 400
    assert other_registrant.status_code == 201


# Parent relationships


def test_same_scope_unowned_parent_links_without_changing_its_ownership():
    user = customer()
    profile = user.active_profile
    child = PedigreeBirdFactory(owner=profile, ring_number="CHILD")
    father = unowned_bird(
        profile, ring_number="FATHER", sex=PedigreeBirdSex.MALE
    )

    response = authenticated_client(user).patch(
        bird_url(child), {"father": father.pk}, format="json"
    )

    assert response.status_code == 200
    father.refresh_from_db()
    assert_unowned(father)
    assert father.registered_by_id == profile.pk


def test_cross_registrant_parent_is_rejected_like_a_missing_bird():
    user = customer()
    other = customer()
    profile = user.active_profile
    child = PedigreeBirdFactory(owner=profile, ring_number="CHILD")
    # Owned by the user but registered by someone else: still out of scope.
    foreign_parent = PedigreeBirdFactory(
        owner=profile,
        registered_by=other.active_profile,
        ring_number="FOREIGN",
    )
    client = authenticated_client(user)

    foreign = client.patch(
        bird_url(child), {"father": foreign_parent.pk}, format="json"
    )
    missing = client.patch(bird_url(child), {"father": 999999}, format="json")
    nested = client.patch(
        bird_url(child), {"father": {"id": foreign_parent.pk}}, format="json"
    )
    created = client.post(
        BIRDS_URL,
        {"ring_number": "NEW", "mother": foreign_parent.pk},
        format="json",
    )

    assert foreign.status_code == missing.status_code == 400
    assert foreign.data["father"][0].code == "does_not_exist"
    assert missing.data["father"][0].code == "does_not_exist"
    assert nested.status_code == 400
    assert nested.data["father"][0].code == "incorrect_type"
    assert created.status_code == 400
    assert created.data["mother"][0].code == "does_not_exist"
    child.refresh_from_db()
    assert child.father_id is None
    assert not PedigreeBird.objects.filter(ring_number="NEW").exists()


def test_validator_rejects_parent_registered_by_another_profile():
    profile = customer().active_profile
    foreign_parent = PedigreeBirdFactory(owner=customer().active_profile)

    with pytest.raises(ValidationError) as error:
        PedigreeBirdValidator.validate(
            attrs={"ring_number": "CHILD", "father": foreign_parent},
            registered_by=profile,
        )

    assert "father" in error.value.detail


# Ownership changes


def test_customer_can_take_and_clear_ownership_of_own_registered_bird():
    user = customer()
    profile = user.active_profile
    bird = unowned_bird(profile, ring_number="F")
    client = authenticated_client(user)

    taken = client.patch(bird_url(bird), {"owner": profile.pk}, format="json")
    bird.refresh_from_db()
    assert taken.status_code == 200
    assert_owned_by(bird, profile)

    cleared = client.patch(bird_url(bird), {"owner": None}, format="json")
    bird.refresh_from_db()
    assert cleared.status_code == 200
    assert_unowned(bird)
    assert bird.registered_by_id == profile.pk


def test_customer_cannot_assign_third_party_or_displace_another_owner():
    user = customer()
    other = customer()
    profile = user.active_profile
    unowned = unowned_bird(profile, ring_number="F")
    owned_by_other = PedigreeBirdFactory(
        owner=other.active_profile,
        registered_by=profile,
        ring_number="M",
    )
    client = authenticated_client(user)

    third_party = client.patch(
        bird_url(unowned), {"owner": other.active_profile.pk}, format="json"
    )
    overwrite = client.patch(
        bird_url(owned_by_other), {"owner": profile.pk}, format="json"
    )
    clear = client.patch(bird_url(owned_by_other), {"owner": None}, format="json")

    assert third_party.status_code == overwrite.status_code == 400
    assert clear.status_code == 400
    unowned.refresh_from_db()
    owned_by_other.refresh_from_db()
    assert_unowned(unowned)
    assert_owned_by(owned_by_other, other.active_profile)


def test_manager_assigns_and_reassigns_owner_without_touching_registered_by():
    registrant = customer().active_profile
    buyer = UserAccountFactory(with_profile=ProfileType.SELLER).active_profile
    bird = unowned_bird(registrant, ring_number="F")
    staff = manager("pedigrees.birds.update")
    client = authenticated_client(staff)

    assigned = client.patch(bird_url(bird), {"owner": buyer.pk}, format="json")
    bird.refresh_from_db()
    assert assigned.status_code == 200
    assert_owned_by(bird, buyer)

    reassigned = client.patch(
        bird_url(bird), {"owner": registrant.pk}, format="json"
    )
    bird.refresh_from_db()
    assert reassigned.status_code == 200
    assert_owned_by(bird, registrant)

    manager_owner = client.patch(
        bird_url(bird), {"owner": staff.active_profile.pk}, format="json"
    )
    cleared = client.patch(bird_url(bird), {"owner": None}, format="json")
    bird.refresh_from_db()
    assert manager_owner.status_code == 400
    assert cleared.status_code == 200
    assert_unowned(bird)
    assert bird.registered_by_id == registrant.pk


def test_manager_can_update_bird_with_parents_in_its_registration_scope():
    registrant = customer().active_profile
    father = unowned_bird(registrant, ring_number="F", sex=PedigreeBirdSex.MALE)
    mother = unowned_bird(
        registrant, ring_number="M", sex=PedigreeBirdSex.FEMALE
    )
    child = PedigreeBirdFactory(owner=registrant, ring_number="C", father=father)
    client = authenticated_client(manager("pedigrees.birds.update"))

    response = client.patch(
        bird_url(child),
        {"name": "Updated", "father": father.pk, "mother": mother.pk},
        format="json",
    )

    assert response.status_code == 200
    child.refresh_from_db()
    assert child.mother_id == mother.pk
    assert child.registered_by_id == registrant.pk
    assert_owned_by(child, registrant)


# Claim / release


def test_claim_sets_owner_only_and_grants_no_pedigree_visibility():
    registrant = customer()
    claimer = customer()
    ancestor = unowned_bird(registrant.active_profile, ring_number="F")
    client = authenticated_client(claimer)

    response = client.post(ownership_url(ancestor, "claim"))

    assert response.status_code == 200
    assert set(response.data) == {
        "id",
        "public_id",
        "owner",
        "registered_by",
        "ownership_status",
    }
    ancestor.refresh_from_db()
    assert_owned_by(ancestor, claimer.active_profile)
    assert ancestor.registered_by_id == registrant.active_profile.pk
    assert client.get(bird_url(ancestor)).status_code == 404
    assert client.get(BIRDS_URL).data["results"] == []


def test_claim_is_idempotent_for_the_owner_and_hides_birds_owned_by_others():
    claimer = customer()
    latecomer = customer()
    bird = unowned_bird(customer().active_profile, ring_number="F")
    authenticated_client(claimer).post(ownership_url(bird, "claim"))

    repeated = authenticated_client(claimer).post(ownership_url(bird, "claim"))
    taken = authenticated_client(latecomer).post(ownership_url(bird, "claim"))
    missing = authenticated_client(latecomer).post(
        f"{BIRDS_URL}public/{uuid.uuid4()}/claim/"
    )

    assert repeated.status_code == 200
    assert taken.status_code == missing.status_code == 404
    bird.refresh_from_db()
    assert_owned_by(bird, claimer.active_profile)


def test_release_clears_ownership_for_the_current_owner_only():
    user = customer()
    profile = user.active_profile
    bird = PedigreeBirdFactory(owner=profile, ring_number="S")

    foreign = authenticated_client(customer()).post(ownership_url(bird, "release"))
    released = authenticated_client(user).post(ownership_url(bird, "release"))
    repeated = authenticated_client(user).post(ownership_url(bird, "release"))

    assert foreign.status_code == 404
    assert released.status_code == 200
    assert repeated.status_code == 404
    bird.refresh_from_db()
    assert_unowned(bird)
    assert bird.registered_by_id == profile.pk


def test_managers_cannot_claim_or_release():
    bird = unowned_bird(customer().active_profile, ring_number="F")
    client = authenticated_client(manager("pedigrees.birds.update"))

    assert client.post(ownership_url(bird, "claim")).status_code == 403
    assert client.post(ownership_url(bird, "release")).status_code == 403


# OCR review


def test_ocr_review_owns_only_the_subject_across_a_deep_pedigree():
    user = customer()
    profile = user.active_profile
    document = PedigreeDocumentFactory(owner=profile, uploaded_by=user)

    response = review(user, document, tree_nodes(4, "D"))

    assert response.status_code == 200
    assert response.data["created_count"] == 15
    birds = {
        path: PedigreeBird.objects.get(pk=pk)
        for path, pk in response.data["path_to_bird_id"].items()
    }
    assert_owned_by(birds.pop(""), profile)
    # Parents, grandparents and great-grandparents.
    assert {len(path) for path in birds} == {1, 2, 3}
    for ancestor in birds.values():
        assert_unowned(ancestor)
    assert set(PedigreeBird.objects.values_list("registered_by_id", flat=True)) == {
        profile.pk
    }


def test_ocr_review_reuses_scope_birds_without_claiming_or_overwriting_owners():
    user = customer()
    other = customer()
    profile = user.active_profile
    unowned_father = unowned_bird(profile, ring_number="F")
    owned_mother = PedigreeBirdFactory(
        owner=profile, ring_number="M", sex=PedigreeBirdSex.FEMALE
    )
    claimed_grandmother = PedigreeBirdFactory(
        owner=other.active_profile,
        registered_by=profile,
        ring_number="MM",
        sex=PedigreeBirdSex.FEMALE,
    )
    foreign_grandfather = PedigreeBirdFactory(
        owner=other.active_profile, ring_number="FF"
    )
    document = PedigreeDocumentFactory(owner=profile, uploaded_by=user)

    response = review(
        user,
        document,
        [
            {"path": "", "ring_number": "S"},
            {"path": "F", "ring_number": "F"},
            {"path": "M", "ring_number": "M"},
            {"path": "FF", "ring_number": "FF"},
            {"path": "MM", "ring_number": "MM"},
        ],
    )

    assert response.status_code == 200
    ids = response.data["path_to_bird_id"]
    assert ids["F"] == unowned_father.pk
    assert ids["M"] == owned_mother.pk
    assert ids["MM"] == claimed_grandmother.pk
    assert ids["FF"] != foreign_grandfather.pk
    for bird in (
        unowned_father,
        owned_mother,
        claimed_grandmother,
        foreign_grandfather,
    ):
        bird.refresh_from_db()
    assert_unowned(unowned_father)
    assert unowned_father.father_id == ids["FF"]
    assert_owned_by(owned_mother, profile)
    assert_owned_by(claimed_grandmother, other.active_profile)
    new_grandfather = PedigreeBird.objects.get(pk=ids["FF"])
    assert new_grandfather.registered_by_id == profile.pk
    assert_unowned(new_grandfather)
    assert foreign_grandfather.registered_by_id == other.active_profile.pk
    assert_owned_by(foreign_grandfather, other.active_profile)


def test_ocr_review_claims_an_unowned_subject_but_never_another_owner():
    user = customer()
    other = customer()
    profile = user.active_profile
    unowned_subject = unowned_bird(profile, ring_number="S1")
    owned_elsewhere = PedigreeBirdFactory(
        owner=other.active_profile,
        registered_by=profile,
        ring_number="S2",
    )

    first = review(
        user,
        PedigreeDocumentFactory(owner=profile, uploaded_by=user),
        [{"path": "", "ring_number": "S1"}],
    )
    second = review(
        user,
        PedigreeDocumentFactory(owner=profile, uploaded_by=user),
        [{"path": "", "ring_number": "S2"}],
    )

    assert first.status_code == second.status_code == 200
    unowned_subject.refresh_from_db()
    owned_elsewhere.refresh_from_db()
    assert_owned_by(unowned_subject, profile)
    assert_owned_by(owned_elsewhere, other.active_profile)


def test_ocr_review_rejects_owned_status_on_an_ancestor_atomically():
    user = customer()
    document = PedigreeDocumentFactory(owner=user.active_profile, uploaded_by=user)

    response = review(
        user,
        document,
        [
            {"path": "", "ring_number": "S"},
            {"path": "F", "ring_number": "F", "ownership_status": OWNED},
        ],
    )

    assert response.status_code == 400
    assert not PedigreeBird.objects.exists()


# XLSX import


def test_import_owns_only_row_roots_across_a_deep_pedigree():
    target = customer()
    profile = target.active_profile

    response = import_file(
        manager_user(),
        target,
        row(
            "root",
            Estok_Father1No="sire",
            Estok_Mother1No="dam",
            Estok_Father2No="sire-sire",
            Estok_Mother2No="sire-dam",
            Estok_Father3No="dam-sire",
            Estok_Mother3No="dam-dam",
            Estok_Father4No="great-sire",
            Estok_Mother4No="great-dam",
        ),
    )

    assert response.status_code == 200
    assert response.data["birds_created"] == 9
    birds = {
        bird.normalized_ring_number: bird
        for bird in PedigreeBird.objects.filter(registered_by=profile)
    }
    assert len(birds) == PedigreeBird.objects.count() == 9
    assert_owned_by(birds.pop("ROOT"), profile)
    for ancestor in birds.values():
        assert_unowned(ancestor)
    assert birds["SIRE-SIRE"].father_id == birds["GREAT-SIRE"].pk


def test_import_treats_a_ring_with_its_own_row_as_a_subject():
    target = customer()
    profile = target.active_profile

    response = import_file(
        manager_user(),
        target,
        row("child", Estok_Father1No="sire", Estok_Father2No="grand-sire"),
        row("sire", "ذكر"),
    )

    assert response.status_code == 200
    sire = PedigreeBird.objects.get(
        registered_by=profile, normalized_ring_number="SIRE"
    )
    grand_sire = PedigreeBird.objects.get(
        registered_by=profile, normalized_ring_number="GRAND-SIRE"
    )
    assert_owned_by(sire, profile)
    assert_unowned(grand_sire)


def test_import_reuses_scope_birds_without_claiming_ancestors_or_other_owners():
    target = customer()
    other = customer()
    profile = target.active_profile
    root = unowned_bird(profile, ring_number="ROOT")
    unowned_sire = unowned_bird(profile, ring_number="SIRE")
    claimed_dam = PedigreeBirdFactory(
        owner=other.active_profile,
        registered_by=profile,
        ring_number="DAM",
    )
    foreign_grand_sire = PedigreeBirdFactory(
        owner=other.active_profile, ring_number="GRAND-SIRE"
    )

    response = import_file(
        manager_user(),
        target,
        row(
            "root",
            Estok_Father1No="sire",
            Estok_Mother1No="dam",
            Estok_Father2No="grand-sire",
        ),
    )

    assert response.status_code == 200
    assert response.data["birds_reused"] == 3
    assert response.data["birds_created"] == 1
    for bird in (root, unowned_sire, claimed_dam, foreign_grand_sire):
        bird.refresh_from_db()
    assert_owned_by(root, profile)
    assert_unowned(unowned_sire)
    assert_owned_by(claimed_dam, other.active_profile)
    new_grand_sire = PedigreeBird.objects.get(
        registered_by=profile, normalized_ring_number="GRAND-SIRE"
    )
    assert new_grand_sire.pk != foreign_grand_sire.pk
    assert_unowned(new_grand_sire)
    assert unowned_sire.father_id == new_grand_sire.pk
    assert foreign_grand_sire.registered_by_id == other.active_profile.pk


# Report generation


def test_generate_accepts_an_unowned_subject_in_the_registration_scope(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    user = customer()
    profile = user.active_profile
    create_report_subscription(profile)
    RegisteredPedigree.objects.create(pedigree_id=7)
    ancestor = unowned_bird(profile, ring_number="F")
    upstream = UpstreamResponse(
        content=b"%PDF-1.4 bytes",
        headers={"Content-Type": "application/pdf"},
    )

    with patch("requests.post", return_value=upstream) as post:
        response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(ancestor.pk),
            format="json",
        )

    assert response.status_code == 200
    post.assert_called_once()
