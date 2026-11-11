from unittest.mock import patch

import pytest
import requests
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdColour,
    RegisteredPedigree,
)
from apps.pedigrees.services import PedigreeReportMapper
from apps.users.constants import ProfileType
from tests.factories import PedigreeBirdFactory, UserAccountFactory
from tests.pedigrees.test_pedigrees import (
    UpstreamResponse,
    create_report_subscription,
    generation_request,
)

pytestmark = pytest.mark.django_db

GENERATE_URL = "/api/pedigrees/generate/"
OMITTED = object()


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture(autouse=True)
def report_provider(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"
    RegisteredPedigree.objects.create(pedigree_id=7)


def subscribed_customer():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    create_report_subscription(user.active_profile)
    return user


def pdf_response(status_code=200):
    return UpstreamResponse(
        content=b"%PDF-1.4 bytes",
        status_code=status_code,
        headers={"Content-Type": "application/pdf"},
    )


def generate(user, subject, *, color=OMITTED, parameters=None, upstream=None):
    payload = generation_request(subject.pk)
    if color is not OMITTED:
        payload["color"] = color
    if parameters:
        payload["report"]["parameters"].update(parameters)
    with patch("requests.post", return_value=upstream or pdf_response()) as post:
        response = authenticated_client(user).post(
            GENERATE_URL, payload, format="json"
        )
    sent = post.call_args.kwargs["json"]["parameters"] if post.called else None
    return response, sent


def stored_color(bird):
    bird.refresh_from_db()
    return bird.default_color


def test_default_color_is_nullable_and_independent_of_plumage_colour():
    user = subscribed_customer()
    bird = PedigreeBirdFactory(
        owner=user.active_profile, colour=PedigreeBirdColour.BLUE_BAR
    )

    response, _ = generate(user, bird, color="#ff0000")

    assert PedigreeBird._meta.get_field("default_color").null is True
    assert response.status_code == 200
    bird.refresh_from_db()
    assert bird.default_color == "#FF0000"
    assert bird.colour == PedigreeBirdColour.BLUE_BAR


def test_explicit_color_drives_bird_box_back_colors_and_is_saved():
    user = subscribed_customer()
    profile = user.active_profile
    father = PedigreeBirdFactory(owner=None, registered_by=profile)
    subject = PedigreeBirdFactory(owner=profile, father=father)
    unrelated = PedigreeBirdFactory(owner=profile, default_color="#ABCDEF")

    response, sent = generate(user, subject, color="#ff0000")

    assert response.status_code == 200
    for field in ("Farkh_No", "Esmah_Name", "Farth_FatherNo", "Father1_NO"):
        assert sent[f"{field}_BackColor"] == "#FF0000"
    # Only bird box backgrounds: no other style or non-bird field is touched.
    assert "Farkh_No_TextColor" not in sent
    assert "OwnerLoftName_BackColor" not in sent
    assert "BackgroundImage_BackColor" not in sent
    assert stored_color(subject) == "#FF0000"
    assert stored_color(father) == "#FF0000"
    assert stored_color(unrelated) == "#ABCDEF"


@pytest.mark.parametrize("color", [None, OMITTED])
def test_null_or_omitted_color_uses_the_stored_default(color):
    user = subscribed_customer()
    subject = PedigreeBirdFactory(owner=user.active_profile, default_color="#00AA00")

    response, sent = generate(user, subject, color=color)

    assert response.status_code == 200
    assert sent["Farkh_No_BackColor"] == "#00AA00"
    assert stored_color(subject) == "#00AA00"


@pytest.mark.parametrize("color", [None, OMITTED])
def test_missing_color_everywhere_prints_without_color_and_saves_nothing(color):
    user = subscribed_customer()
    subject = PedigreeBirdFactory(owner=user.active_profile)

    response, sent = generate(user, subject, color=color)

    assert response.status_code == 200
    assert not [key for key in sent if key.endswith("_BackColor")]
    assert stored_color(subject) is None


@pytest.mark.parametrize("status_code", [400, 500, 503])
def test_failed_provider_response_does_not_change_default_color(status_code):
    user = subscribed_customer()
    profile = user.active_profile
    mother = PedigreeBirdFactory(owner=None, registered_by=profile)
    subject = PedigreeBirdFactory(
        owner=profile, mother=mother, default_color="#00AA00"
    )

    response, sent = generate(
        user, subject, color="#FF0000", upstream=pdf_response(status_code)
    )

    assert response.status_code == status_code
    assert sent["Farkh_No_BackColor"] == "#FF0000"
    assert stored_color(subject) == "#00AA00"
    assert stored_color(mother) is None


@pytest.mark.parametrize(
    "error",
    [requests.exceptions.Timeout, requests.exceptions.ConnectionError],
)
def test_provider_network_error_does_not_change_default_color(error):
    user = subscribed_customer()
    subject = PedigreeBirdFactory(owner=user.active_profile)
    payload = {**generation_request(subject.pk), "color": "#FF0000"}

    with patch("requests.post", side_effect=error):
        response = authenticated_client(user).post(
            GENERATE_URL, payload, format="json"
        )

    assert response.status_code in (502, 504)
    assert stored_color(subject) is None


def test_frontend_per_field_styles_win_over_the_report_color():
    user = subscribed_customer()
    subject = PedigreeBirdFactory(owner=user.active_profile)

    response, sent = generate(
        user,
        subject,
        color="#FF0000",
        parameters={
            "Farkh_No_BackColor": "#123456",
            "Farkh_No_TextColor": "#FFFFFF",
            "Father1_NO_BackColor": None,
        },
    )

    assert response.status_code == 200
    assert sent["Farkh_No_BackColor"] == "#123456"
    assert sent["Farkh_No_TextColor"] == "#FFFFFF"
    assert sent["Father1_NO_BackColor"] == "#FF0000"
    assert sent["Esmah_Name_BackColor"] == "#FF0000"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_frontend_styles_get_the_box_color(blank):
    user = subscribed_customer()
    profile = user.active_profile
    grandmother = PedigreeBirdFactory(owner=None, registered_by=profile)
    mother = PedigreeBirdFactory(
        owner=None, registered_by=profile, mother=grandmother
    )
    subject = PedigreeBirdFactory(owner=profile, mother=mother)
    grandmother_styles = (
        "Mother2_1_NO_BackColor",
        "Mother2_1_Name_BackColor",
        "Mother2_1_BackColor",
    )

    response, sent = generate(
        user,
        subject,
        color="#FF0000",
        parameters={style: blank for style in grandmother_styles},
    )

    assert response.status_code == 200
    assert sent["Farth_MotherNo_BackColor"] == "#FF0000"
    for style in grandmother_styles:
        assert sent[style] == "#FF0000"


def test_without_request_color_each_box_uses_its_own_bird_saved_color():
    user = subscribed_customer()
    profile = user.active_profile
    grandmother = PedigreeBirdFactory(
        owner=None, registered_by=profile, default_color="#00AA00"
    )
    mother = PedigreeBirdFactory(
        owner=None, registered_by=profile, mother=grandmother
    )
    subject = PedigreeBirdFactory(
        owner=profile, mother=mother, default_color="#0000FF"
    )

    response, sent = generate(
        user, subject, color=None, parameters={"Mother2_1_BackColor": ""}
    )

    assert response.status_code == 200
    assert sent["Farkh_No_BackColor"] == "#0000FF"
    assert sent["Esmah_FullNo_BackColor"] == "#0000FF"
    # The mother has no saved colour, so her box is left uncoloured.
    assert "Farth_MotherNo_BackColor" not in sent
    assert "Mother_Info_BackColor" not in sent
    assert sent["Mother2_1_NO_BackColor"] == "#00AA00"
    assert sent["Mother2_1_Name_BackColor"] == "#00AA00"
    assert sent["Mother2_1_BackColor"] == "#00AA00"
    # Nothing explicit was sent, so nothing is written.
    assert stored_color(subject) == "#0000FF"
    assert stored_color(mother) is None
    assert stored_color(grandmother) == "#00AA00"


def test_bird_color_follows_it_from_mother_to_grandmother():
    user = subscribed_customer()
    profile = user.active_profile
    bird = PedigreeBirdFactory(owner=None, registered_by=profile)
    child = PedigreeBirdFactory(owner=None, registered_by=profile, mother=bird)
    grandchild = PedigreeBirdFactory(owner=profile, mother=child)

    as_mother, _ = generate(user, child, color="#FF0000")
    after_mother_print = stored_color(bird)
    as_grandmother, sent = generate(user, grandchild, color=None)
    recoloured, _ = generate(user, grandchild, color="#0000FF")

    assert as_mother.status_code == 200
    assert after_mother_print == "#FF0000"
    assert as_grandmother.status_code == 200
    assert sent["Mother2_1_NO_BackColor"] == "#FF0000"
    assert recoloured.status_code == 200
    assert stored_color(bird) == "#0000FF"
    assert stored_color(child) == "#0000FF"
    assert stored_color(grandchild) == "#0000FF"


def test_explicit_color_is_saved_only_on_printed_birds_of_the_registrant():
    user = subscribed_customer()
    profile = user.active_profile
    other_profile = UserAccountFactory(
        with_profile=ProfileType.CUSTOMER
    ).active_profile
    beyond_depth = PedigreeBirdFactory(
        owner=None, registered_by=profile, default_color="#ABCDEF"
    )
    grandmother = PedigreeBirdFactory(
        owner=None, registered_by=profile, mother=beyond_depth
    )
    # Legacy link to a bird another profile registered: never read or written.
    foreign_father = PedigreeBirdFactory(
        owner=other_profile, default_color="#00AA00"
    )
    mother = PedigreeBirdFactory(
        owner=None,
        registered_by=profile,
        mother=grandmother,
        father=foreign_father,
    )
    subject = PedigreeBirdFactory(owner=profile, mother=mother)

    without_color, sent = generate(user, subject, color=None)
    with_color, _ = generate(user, subject, color="#FF0000")

    assert without_color.status_code == 200
    assert "Father2_1_NO_BackColor" not in sent
    assert with_color.status_code == 200
    assert stored_color(subject) == "#FF0000"
    assert stored_color(mother) == "#FF0000"
    assert stored_color(grandmother) == "#FF0000"
    assert stored_color(foreign_father) == "#00AA00"
    assert stored_color(beyond_depth) == "#ABCDEF"


@pytest.mark.parametrize("color", ["red", "#12345", "#GGGGGG", "FF0000"])
def test_invalid_color_is_rejected_before_the_provider(color):
    user = subscribed_customer()
    subject = PedigreeBirdFactory(owner=user.active_profile)

    response, sent = generate(user, subject, color=color)

    assert response.status_code == 400
    assert "color" in response.data
    assert sent is None
    assert stored_color(subject) is None


def test_generation_cannot_change_a_bird_outside_the_registration_scope():
    user = subscribed_customer()
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    foreign = PedigreeBirdFactory(owner=other.active_profile)
    # Owned by the user but registered by someone else: still out of scope.
    owned_elsewhere = PedigreeBirdFactory(
        owner=user.active_profile, registered_by=other.active_profile
    )

    for bird in (foreign, owned_elsewhere):
        response, sent = generate(user, bird, color="#FF0000")
        assert response.status_code == 404
        assert sent is None
        assert stored_color(bird) is None


def test_generation_without_entitlement_does_not_change_default_color():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    subject = PedigreeBirdFactory(owner=user.active_profile)

    response, sent = generate(user, subject, color="#FF0000")

    assert response.status_code == 403
    assert sent is None
    assert stored_color(subject) is None


def test_manager_generation_follows_the_existing_permission_path():
    subject = PedigreeBirdFactory(owner=subscribed_customer().active_profile)
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager_profile = manager.active_profile
    create_report_subscription(manager_profile)

    manager_profile.permissions = []
    manager_profile.save(update_fields=["permissions"])
    denied, denied_sent = generate(manager, subject, color="#FF0000")
    manager_profile.permissions = ["pedigrees.reports.generate"]
    manager_profile.save(update_fields=["permissions"])
    stored_after_denied = stored_color(subject)
    allowed, _ = generate(manager, subject, color="#00FF00")

    assert denied.status_code == 403
    assert denied_sent is None
    assert stored_after_denied is None
    assert allowed.status_code == 200
    subject.refresh_from_db()
    assert subject.default_color == "#00FF00"
    assert subject.registered_by_id != manager_profile.pk


def test_default_color_is_read_only_on_the_bird_api():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    bird = PedigreeBirdFactory(owner=user.active_profile, default_color="#00AA00")
    client = authenticated_client(user)

    patched = client.patch(
        f"/api/pedigrees/birds/{bird.pk}/",
        {"default_color": "#FF0000"},
        format="json",
    )
    created = client.post(
        "/api/pedigrees/birds/",
        {"ring_number": "NEW", "default_color": "#FF0000"},
        format="json",
    )

    assert patched.status_code == 200
    assert patched.data["default_color"] == "#00AA00"
    assert stored_color(bird) == "#00AA00"
    assert created.status_code == 201
    assert PedigreeBird.objects.get(pk=created.data["id"]).default_color is None


def test_mapper_without_report_color_adds_no_style_parameters():
    subject = PedigreeBirdFactory()
    report = {"parameters": {"Farkh_No_BackColor": "#123456"}}

    payload = PedigreeReportMapper.merge_payload(
        frontend_report=report,
        subject_bird=subject,
        generation_depth=3,
    )

    style_keys = [key for key in payload["parameters"] if key.endswith("Color")]
    assert style_keys == ["Farkh_No_BackColor"]
