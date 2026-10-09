from unittest.mock import patch

from django.db import IntegrityError, transaction
import pytest
import yaml

from apps.authentication.services import switch_user_profile
from apps.pedigrees.models import RegisteredPedigree, RegisteredPedigreeUser
from apps.users.constants import ProfileType
from tests.factories import (
    PedigreeBirdFactory,
    SellerProfileFactory,
    UserAccountFactory,
)
from tests.pedigrees.test_pedigrees import (
    UpstreamResponse,
    authenticated_client,
    create_report_subscription,
    generation_request,
)

pytestmark = pytest.mark.django_db

REGISTRY_URL = "/api/pedigrees/registered/"
REGISTRY_PERMISSIONS = [
    "pedigrees.registered.list",
    "pedigrees.registered.retrieve",
    "pedigrees.registered.create",
    "pedigrees.registered.update",
    "pedigrees.registered.delete",
]


def detail_url(pedigree):
    return f"{REGISTRY_URL}{pedigree.pk}/"


def register(pedigree_id, *, is_public=True, users=()):
    pedigree = RegisteredPedigree.objects.create(
        pedigree_id=pedigree_id,
        is_public=is_public,
    )
    pedigree.users.set(users)
    return pedigree


def manager_client(permissions=None):
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    profile = manager.active_profile
    profile.permissions = (
        REGISTRY_PERMISSIONS if permissions is None else permissions
    )
    profile.save(update_fields=["permissions"])
    return authenticated_client(manager)


def visible_ids(response):
    assert response.status_code == 200
    return sorted(item["pedigree_id"] for item in response.data["results"])


@pytest.fixture
def provider(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    settings.PEDIGREE_REPORT_API_KEY = "provider-key"


@pytest.fixture
def scenario():
    """A/B/C customers with public, assigned-private, and unassigned rows."""
    user_a = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_b = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_c = UserAccountFactory(with_profile=ProfileType.SELLER)
    return {
        "a": user_a,
        "b": user_b,
        "c": user_c,
        "public_1": register(1),
        "public_2": register(2),
        "private_a": register(3, is_public=False, users=[user_a]),
        "private_b": register(4, is_public=False, users=[user_b]),
        "private_none": register(5, is_public=False),
    }


# Manager CRUD


def test_manager_creates_public_and_private_registered_pedigrees():
    user_a = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_b = UserAccountFactory(with_profile=ProfileType.SELLER)
    client = manager_client()

    public = client.post(REGISTRY_URL, {"pedigree_id": 4}, format="json")
    private = client.post(
        REGISTRY_URL,
        {
            "pedigree_id": 7,
            "is_public": False,
            "user_ids": [user_b.pk, user_a.pk],
        },
        format="json",
    )

    assert public.status_code == 201
    assert public.data["is_public"] is True
    assert public.data["user_ids"] == []
    assert private.status_code == 201
    assert private.data["is_public"] is False
    assert sorted(private.data["user_ids"]) == sorted([user_a.pk, user_b.pk])
    assert set(private.data) == {
        "id",
        "pedigree_id",
        "is_public",
        "user_ids",
        "created",
        "modified",
    }
    assert RegisteredPedigreeUser.objects.filter(
        pedigree_id=private.data["id"]
    ).count() == 2


def test_duplicate_pedigree_id_rejected_until_registration_is_deleted():
    client = manager_client()
    created = client.post(REGISTRY_URL, {"pedigree_id": 4}, format="json")

    duplicate = client.post(REGISTRY_URL, {"pedigree_id": 4}, format="json")
    deleted = client.delete(f"{REGISTRY_URL}{created.data['id']}/")
    recreated = client.post(REGISTRY_URL, {"pedigree_id": 4}, format="json")

    assert duplicate.status_code == 400
    assert "pedigree_id" in duplicate.data
    assert deleted.status_code == 204
    assert RegisteredPedigree.all_objects.get(pk=created.data["id"]).deleted
    assert recreated.status_code == 201


def test_user_ids_must_be_user_accounts_and_duplicates_collapse():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = manager_client()

    invalid = client.post(
        REGISTRY_URL,
        {"pedigree_id": 4, "user_ids": [user.pk, 999999]},
        format="json",
    )
    duplicated = client.post(
        REGISTRY_URL,
        {"pedigree_id": 4, "user_ids": [user.pk, user.pk]},
        format="json",
    )

    assert invalid.status_code == 400
    assert "user_ids" in invalid.data
    assert duplicated.status_code == 201
    assert duplicated.data["user_ids"] == [user.pk]
    assert RegisteredPedigreeUser.objects.count() == 1


def test_bridge_row_is_unique_per_pedigree_and_user():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    pedigree = register(4, is_public=False, users=[user])

    with pytest.raises(IntegrityError), transaction.atomic():
        RegisteredPedigreeUser.objects.create(pedigree=pedigree, user=user)


def test_patch_user_ids_semantics():
    users = [
        UserAccountFactory(with_profile=ProfileType.CUSTOMER) for _ in range(3)
    ]
    pedigree = register(4, is_public=False, users=users[:2])
    client = manager_client()

    omitted = client.patch(
        detail_url(pedigree), {"is_public": True}, format="json"
    )
    assert omitted.status_code == 200
    assert omitted.data["is_public"] is True
    assert sorted(omitted.data["user_ids"]) == [users[0].pk, users[1].pk]

    replaced = client.patch(
        detail_url(pedigree),
        {"user_ids": [users[1].pk, users[2].pk]},
        format="json",
    )
    assert replaced.status_code == 200
    assert replaced.data["is_public"] is True
    assert sorted(replaced.data["user_ids"]) == [users[1].pk, users[2].pk]

    cleared = client.patch(detail_url(pedigree), {"user_ids": []}, format="json")
    assert cleared.status_code == 200
    assert cleared.data["user_ids"] == []
    assert not RegisteredPedigreeUser.objects.filter(pedigree=pedigree).exists()


def test_visibility_toggle_keeps_assignments():
    user_a = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_b = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    pedigree = register(4, is_public=False, users=[user_a])
    client = manager_client()

    client.patch(detail_url(pedigree), {"is_public": True}, format="json")
    assert visible_ids(authenticated_client(user_b).get(REGISTRY_URL)) == [4]
    assert RegisteredPedigreeUser.objects.filter(
        pedigree=pedigree, user=user_a
    ).exists()

    client.patch(detail_url(pedigree), {"is_public": False}, format="json")
    assert visible_ids(authenticated_client(user_a).get(REGISTRY_URL)) == [4]
    assert visible_ids(authenticated_client(user_b).get(REGISTRY_URL)) == []


def test_manager_lists_every_registration_only_with_permission(scenario):
    forbidden = manager_client(permissions=[]).get(REGISTRY_URL)
    allowed = manager_client(
        permissions=["pedigrees.registered.list"]
    ).get(REGISTRY_URL)

    assert forbidden.status_code == 403
    assert visible_ids(allowed) == [1, 2, 3, 4, 5]
    private_none = next(
        item for item in allowed.data["results"] if item["pedigree_id"] == 5
    )
    assert private_none["user_ids"] == []


@pytest.mark.parametrize(
    "profile_type", [ProfileType.CUSTOMER, ProfileType.SELLER]
)
def test_customers_and_sellers_cannot_use_admin_writes(profile_type):
    pedigree = register(4)
    client = authenticated_client(
        UserAccountFactory(with_profile=profile_type)
    )

    created = client.post(REGISTRY_URL, {"pedigree_id": 5}, format="json")
    updated = client.patch(
        detail_url(pedigree), {"is_public": False}, format="json"
    )
    deleted = client.delete(detail_url(pedigree))

    assert [created.status_code, updated.status_code, deleted.status_code] == [
        403,
        403,
        403,
    ]
    assert RegisteredPedigree.objects.get(pk=pedigree.pk).is_public is True


def test_unauthenticated_requests_are_rejected(api_client):
    pedigree = register(4)

    assert api_client.get(REGISTRY_URL).status_code == 401
    assert api_client.get(detail_url(pedigree)).status_code == 401


# User visibility


def test_users_see_public_union_their_assigned_private_only(scenario):
    response_a = authenticated_client(scenario["a"]).get(REGISTRY_URL)

    assert visible_ids(response_a) == [1, 2, 3]
    assert response_a.data["count"] == 3
    assert visible_ids(authenticated_client(scenario["b"]).get(REGISTRY_URL)) == [
        1,
        2,
        4,
    ]
    assert visible_ids(authenticated_client(scenario["c"]).get(REGISTRY_URL)) == [
        1,
        2,
    ]
    for item in response_a.data["results"]:
        assert set(item) == {"id", "pedigree_id", "is_public"}


def test_detail_returns_404_for_private_pedigrees_of_others(scenario):
    client = authenticated_client(scenario["a"])

    assert client.get(detail_url(scenario["public_1"])).status_code == 200
    assigned = client.get(detail_url(scenario["private_a"]))
    assert assigned.status_code == 200
    assert "user_ids" not in assigned.data
    assert client.get(detail_url(scenario["private_b"])).status_code == 404
    assert client.get(detail_url(scenario["private_none"])).status_code == 404


def test_query_params_only_narrow_the_authorized_queryset(scenario):
    client = authenticated_client(scenario["a"])
    other_id = scenario["b"].pk

    widened = client.get(
        REGISTRY_URL,
        {
            "user_id": other_id,
            "user": other_id,
            "include_private": "true",
            "show_all": "true",
            "owner": other_id,
            "assigned_to": other_id,
        },
    )

    assert visible_ids(widened) == [1, 2, 3]
    assert widened.data["count"] == 3
    assert visible_ids(client.get(REGISTRY_URL, {"is_public": "false"})) == [3]
    assert visible_ids(client.get(REGISTRY_URL, {"pedigree_id": 4})) == []
    assert visible_ids(client.get(REGISTRY_URL, {"ordering": "-pedigree_id"})) == [
        1,
        2,
        3,
    ]


def test_visibility_follows_user_account_across_profile_modes():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    SellerProfileFactory(user=user)
    register(4, is_public=False, users=[user])

    customer_view = visible_ids(authenticated_client(user).get(REGISTRY_URL))
    switched, profile_type = switch_user_profile(user, ProfileType.SELLER)
    seller_view = visible_ids(authenticated_client(user).get(REGISTRY_URL))

    assert switched and profile_type == ProfileType.SELLER
    assert customer_view == seller_view == [4]


# Provider endpoints


@pytest.mark.parametrize(
    "registration, called",
    [
        (None, False),
        ("public", True),
        ("assigned", True),
        ("other_user", False),
        ("unassigned", False),
    ],
)
def test_generate_requires_visible_registered_pdf_id(
    provider, registration, called
):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    create_report_subscription(user.active_profile)
    subject = PedigreeBirdFactory(owner=user.active_profile)
    if registration == "public":
        register(7)
    elif registration == "assigned":
        register(7, is_public=False, users=[user])
    elif registration == "other_user":
        register(7, is_public=False, users=[other])
    elif registration == "unassigned":
        register(7, is_public=False)
    upstream = UpstreamResponse(
        content=b"%PDF-1.4",
        headers={"Content-Type": "application/pdf"},
    )

    with patch("requests.post", return_value=upstream) as post:
        response = authenticated_client(user).post(
            "/api/pedigrees/generate/",
            generation_request(subject.pk),
            format="json",
        )

    assert post.called is called
    assert response.status_code == (200 if called else 404)


@pytest.mark.parametrize(
    "path",
    [
        "/api/pedigrees/templates/4/schema/?generation=3",
        "/api/pedigrees/templates/4/style-parameters/?generation=3",
    ],
)
def test_metadata_proxies_require_visible_registered_shape(provider, path):
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    other = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = authenticated_client(user)
    upstream = UpstreamResponse(
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )

    with patch("requests.get", return_value=upstream) as get:
        unregistered = client.get(path)
        pedigree = register(4, is_public=False, users=[other])
        private_other = client.get(path)
        assert get.call_count == 0
        pedigree.users.add(user)
        assigned = client.get(path)

    assert unregistered.status_code == 404
    assert private_other.status_code == 404
    assert assigned.status_code == 200
    assert get.call_count == 1


def test_manager_metadata_proxy_is_not_limited_by_user_visibility(provider):
    upstream = UpstreamResponse(
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    client = manager_client(permissions=["pedigrees.reports.list"])

    with patch("requests.get", return_value=upstream) as get:
        response = client.get("/api/pedigrees/templates/9/schema/?generation=3")

    assert response.status_code == 200
    get.assert_called_once()


@pytest.mark.parametrize(
    "profile_type", [ProfileType.CUSTOMER, ProfileType.SELLER]
)
def test_template_list_fails_closed_for_users_without_provider_call(
    provider, profile_type
):
    register(4)
    register(5, is_public=False)
    client = authenticated_client(
        UserAccountFactory(with_profile=profile_type)
    )

    with patch("requests.get") as get:
        response = client.get("/api/pedigrees/templates/")

    assert response.status_code == 503
    get.assert_not_called()


def test_registry_is_documented_in_openapi(authenticated_client, settings):
    settings.DEBUG = False

    response = authenticated_client.get("/api/schema/")
    assert response.status_code == 200
    schema = yaml.safe_load(response.content.decode())

    assert set(schema["paths"][REGISTRY_URL]) >= {"get", "post"}
    assert set(schema["paths"][f"{REGISTRY_URL}{{id}}/"]) >= {
        "get",
        "patch",
        "delete",
    }
    components = schema["components"]["schemas"]
    assert {"pedigree_id", "is_public", "user_ids"} <= set(
        components["RegisteredPedigreeAdminRequest"]["properties"]
    )
    assert "user_ids" not in components["RegisteredPedigree"]["properties"]
