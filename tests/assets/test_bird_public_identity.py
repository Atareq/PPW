import uuid
from importlib import import_module

from django.db import IntegrityError, transaction
from django.test import override_settings
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.assets.models import AssetStatus, Bird
from apps.assets.serializer import BirdSerializer, PublicBirdSerializer
from tests.factories import AssetMediaFactory, BirdFactory


@pytest.mark.django_db
def test_bird_gets_unique_public_id_automatically():
    first = BirdFactory()
    second = BirdFactory()

    assert first.public_id
    assert second.public_id
    assert first.public_id != second.public_id


@pytest.mark.django_db
def test_bird_public_id_unique_constraint():
    bird = BirdFactory()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BirdFactory(public_id=bird.public_id)


@pytest.mark.django_db
@override_settings(PUBLIC_FRONTEND_BASE_URL="https://birds.example")
def test_bird_serializer_exposes_public_id_and_qr_payload_url():
    bird = BirdFactory()

    data = BirdSerializer(bird).data

    assert data["public_id"] == str(bird.public_id)
    assert data["qr_payload_url"] == (
        f"https://birds.example/birds/{bird.public_id}"
    )


@pytest.mark.django_db
def test_bird_public_id_cannot_be_edited_through_api(
    authenticated_seller_client,
):
    create_public_id = uuid.uuid4()
    patch_public_id = uuid.uuid4()
    create_response = authenticated_seller_client.post(
        "/api/assets/birds/",
        {
            "title": "Public id protected bird",
            "description": "",
            "category": "bird",
            "status": "available",
            "count": 1,
            "price": "100.00",
            "is_market_listed": True,
            "is_for_auction": False,
            "name": "Protected",
            "ring_number": "PUBLIC-ID-PROTECTED",
            "gender": "male",
            "colour": "Blue",
            "birthday": None,
            "father": None,
            "mother": None,
            "achievements": "Protected public id",
            "flying_speed": None,
            "stamina_ability": "good",
            "public_id": str(create_public_id),
        },
        format="json",
    )

    assert create_response.status_code == status.HTTP_201_CREATED, (
        create_response.data
    )
    assert create_response.data["public_id"] != str(create_public_id)

    bird = Bird.objects.get(pk=create_response.data["id"])
    original_public_id = bird.public_id
    patch_response = authenticated_seller_client.patch(
        f"/api/assets/birds/{bird.pk}/",
        {"public_id": str(patch_public_id)},
        format="json",
    )
    bird.refresh_from_db()

    assert patch_response.status_code == status.HTTP_200_OK
    assert bird.public_id == original_public_id
    assert patch_response.data["public_id"] == str(original_public_id)


@pytest.mark.django_db
def test_public_bird_serializer_exposes_only_public_safe_fields():
    bird = BirdFactory()

    data = PublicBirdSerializer(bird).data

    assert set(data) == {
        "public_id",
        "ring_number",
        "title",
        "name",
        "gender",
        "colour",
        "hatch_date",
        "media",
        "seller",
    }
    assert "id" not in data
    assert "owner" not in data
    assert "price" not in data
    assert "status" not in data


@pytest.mark.django_db
def test_public_bird_endpoint_returns_public_data_without_auth():
    bird = BirdFactory()
    AssetMediaFactory(
        asset=bird,
        media_url="https://example.com/public-bird.jpg",
        is_primary=True,
    )
    client = APIClient()

    response = client.get(f"/api/assets/birds/public/{bird.public_id}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["public_id"] == str(bird.public_id)
    assert response.data["ring_number"] == bird.ring_number
    assert response.data["title"] == bird.title
    assert response.data["name"] == bird.name
    assert response.data["media"] == [
        {
            "media_url": "https://example.com/public-bird.jpg",
            "media_type": "image",
            "alt_text": "",
            "is_primary": True,
            "order": 0,
        }
    ]
    assert "id" not in response.data
    assert "owner" not in response.data


@pytest.mark.django_db
def test_public_bird_endpoint_returns_404_for_invalid_or_unknown_public_id():
    client = APIClient()

    invalid_response = client.get("/api/assets/birds/public/not-a-uuid/")
    unknown_response = client.get(f"/api/assets/birds/public/{uuid.uuid4()}/")

    assert invalid_response.status_code == status.HTTP_404_NOT_FOUND
    assert unknown_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_public_bird_endpoint_does_not_expose_inactive_or_deleted_birds():
    inactive = BirdFactory(status=AssetStatus.INACTIVE)
    deleted = BirdFactory()
    deleted_public_id = deleted.public_id
    deleted.delete()
    client = APIClient()

    inactive_response = client.get(
        f"/api/assets/birds/public/{inactive.public_id}/"
    )
    deleted_response = client.get(
        f"/api/assets/birds/public/{deleted_public_id}/"
    )

    assert inactive_response.status_code == status.HTTP_404_NOT_FOUND
    assert deleted_response.status_code == status.HTTP_404_NOT_FOUND


class _FakeBird:
    def __init__(self, pk):
        self.pk = pk
        self.public_id = None


class _FakeBirdManager:
    def __init__(self, birds):
        self.birds = birds

    def using(self, db_alias):
        return self

    def filter(self, **kwargs):
        if kwargs == {"public_id__isnull": True}:
            return [bird for bird in self.birds if bird.public_id is None]

        pk = kwargs["pk"]
        manager = self

        class _UpdateQuery:
            def update(self, **values):
                for bird in manager.birds:
                    if bird.pk == pk:
                        bird.public_id = values["public_id"]
                return 1

        return _UpdateQuery()


class _FakeApps:
    def __init__(self, bird_model):
        self.bird_model = bird_model

    def get_model(self, app_label, model_name):
        assert (app_label, model_name) == ("assets", "Bird")
        return self.bird_model


class _FakeSchemaEditor:
    class connection:
        alias = "default"


def test_existing_birds_receive_public_id_in_migration():
    migration = import_module("apps.assets.migrations.0007_bird_public_id")
    birds = [_FakeBird(pk=1), _FakeBird(pk=2)]

    class _FakeBirdModel:
        objects = _FakeBirdManager(birds)

    migration.populate_bird_public_ids(
        _FakeApps(_FakeBirdModel),
        _FakeSchemaEditor(),
    )

    assert all(bird.public_id for bird in birds)
    assert len({bird.public_id for bird in birds}) == 2
