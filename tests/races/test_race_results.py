from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.races.models import RaceResult
from tests.factories import RaceFactory, RaceResultFactory

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def race_payload(**overrides):
    payload = {
        "title": "500KM",
        "season_year": 2026,
        "station_name": "500KM",
        "release_datetime": timezone.now(),
        "weather_condition": "جيدة",
        "total_birds": 284,
        "competitors_count": 22,
        "planned_distance_km": "500.000",
        "coordinate_x": "30:48:29,2",
        "coordinate_y": "28:17:00,3",
        "notes": "",
    }
    payload.update(overrides)
    return payload


def result_payload(race, **overrides):
    payload = {
        "race": race.pk,
        "competitor_name": "عز سيد",
        "bird_ring_number": "1312 26 sa",
        "distance_km": "490.832",
        "speed": "1461.171",
        "arrival_datetime": race.release_datetime + timedelta(hours=1),
        "basket_number": 10,
    }
    payload.update(overrides)
    return payload


def create_result(client, race, speed, ring, competitor="Competitor"):
    return client.post(
        "/api/race-results/",
        result_payload(
            race,
            speed=speed,
            bird_ring_number=ring,
            competitor_name=competitor,
        ),
        format="json",
    )


def result_ids(response):
    return [item["id"] for item in response.data["results"]]


def result_ranks_for(race):
    return list(
        RaceResult.objects.filter(race=race)
        .order_by("rank")
        .values_list("bird_ring_number", "rank")
    )


def test_manager_can_create_race(manager_user):
    client = authenticated_client(manager_user)

    response = client.post("/api/races/", race_payload(), format="json")

    assert response.status_code == 201
    assert response.data["title"] == "500KM"


def test_non_manager_authenticated_user_cannot_create_race(
    authenticated_customer_client,
):
    response = authenticated_customer_client.post(
        "/api/races/",
        race_payload(),
        format="json",
    )

    assert response.status_code == 403


def test_authenticated_user_can_list_races(authenticated_customer_client):
    race = RaceFactory(title="Visible race")

    response = authenticated_customer_client.get("/api/races/")

    assert response.status_code == 200
    assert race.pk in result_ids(response)


def test_manager_can_add_race_result(manager_user):
    race = RaceFactory()
    client = authenticated_client(manager_user)

    response = client.post(
        "/api/race-results/",
        result_payload(race),
        format="json",
    )

    assert response.status_code == 201
    assert response.data["rank"] == 1
    assert response.data["bird_ring_number"] == "1312 26 sa"


def test_non_manager_cannot_add_race_result(authenticated_customer_client):
    race = RaceFactory()

    response = authenticated_customer_client.post(
        "/api/race-results/",
        result_payload(race),
        format="json",
    )

    assert response.status_code == 403


def test_race_result_rank_is_calculated_by_speed_descending(manager_user):
    race = RaceFactory()
    client = authenticated_client(manager_user)

    slow = create_result(client, race, "1300.000", "LOW")
    fast = create_result(client, race, "1500.000", "HIGH")

    assert slow.status_code == 201
    assert fast.status_code == 201
    assert result_ranks_for(race) == [
        ("HIGH", 1),
        ("LOW", 2),
    ]


def test_client_submitted_rank_is_ignored(manager_user):
    race = RaceFactory()
    client = authenticated_client(manager_user)

    response = client.post(
        "/api/race-results/",
        result_payload(race, rank=99),
        format="json",
    )

    assert response.status_code == 201
    assert response.data["rank"] == 1


def test_bird_ring_number_must_be_unique_inside_same_race(manager_user):
    race = RaceFactory()
    client = authenticated_client(manager_user)

    create_result(client, race, "1400.000", "DUP")
    response = create_result(client, race, "1450.000", "DUP")

    assert response.status_code == 400


def test_same_bird_ring_number_can_exist_in_another_race(manager_user):
    first_race = RaceFactory()
    second_race = RaceFactory()
    client = authenticated_client(manager_user)

    first_response = create_result(client, first_race, "1400.000", "R-1")
    second_response = create_result(client, second_race, "1450.000", "R-1")

    assert first_response.status_code == 201
    assert second_response.status_code == 201


def test_nested_result_create_rejects_duplicate_ring_number_in_payload(
    manager_user,
):
    race = RaceFactory()
    client = authenticated_client(manager_user)

    response = client.post(
        f"/api/races/{race.pk}/results/",
        {
            "results": [
                result_payload(race, bird_ring_number="DUP"),
                result_payload(race, bird_ring_number="DUP"),
            ]
        },
        format="json",
    )

    assert response.status_code == 400
    assert RaceResult.objects.filter(race=race).count() == 0


def test_nested_result_create_rejects_existing_ring_number_in_race(
    manager_user,
):
    race = RaceFactory()
    client = authenticated_client(manager_user)
    create_result(client, race, "1400.000", "EXISTS")

    response = client.post(
        f"/api/races/{race.pk}/results/",
        {
            "results": [
                result_payload(race, bird_ring_number="EXISTS"),
            ]
        },
        format="json",
    )

    assert response.status_code == 400
    assert RaceResult.objects.filter(race=race).count() == 1


def test_nested_result_create_recalculates_ranks_correctly(manager_user):
    race = RaceFactory()
    client = authenticated_client(manager_user)

    response = client.post(
        f"/api/races/{race.pk}/results/",
        {
            "results": [
                result_payload(
                    race,
                    bird_ring_number="MID",
                    speed="1400.000",
                ),
                result_payload(
                    race,
                    bird_ring_number="FAST",
                    speed="1500.000",
                ),
                result_payload(
                    race,
                    bird_ring_number="SLOW",
                    speed="1300.000",
                ),
            ]
        },
        format="json",
    )

    assert response.status_code == 201
    assert [item["bird_ring_number"] for item in response.data["results"]] == [
        "FAST",
        "MID",
        "SLOW",
    ]
    assert result_ranks_for(race) == [
        ("FAST", 1),
        ("MID", 2),
        ("SLOW", 3),
    ]


def test_updating_result_speed_recalculates_race_ranks(manager_user):
    race = RaceFactory()
    client = authenticated_client(manager_user)
    create_result(client, race, "1300.000", "LOW")
    create_result(client, race, "1500.000", "HIGH")
    low_result = RaceResult.objects.get(race=race, bird_ring_number="LOW")

    response = client.patch(
        f"/api/race-results/{low_result.pk}/",
        {"speed": "1600.000"},
        format="json",
    )

    assert response.status_code == 200
    assert result_ranks_for(race) == [
        ("LOW", 1),
        ("HIGH", 2),
    ]


def test_deleting_result_recalculates_race_ranks(manager_user):
    race = RaceFactory()
    client = authenticated_client(manager_user)
    create_result(client, race, "1600.000", "FAST")
    create_result(client, race, "1500.000", "MID")
    create_result(client, race, "1300.000", "SLOW")
    fast_result = RaceResult.objects.get(race=race, bird_ring_number="FAST")

    response = client.delete(f"/api/race-results/{fast_result.pk}/")

    assert response.status_code == 204
    assert result_ranks_for(race) == [
        ("MID", 1),
        ("SLOW", 2),
    ]


def test_authenticated_user_can_list_race_results_for_one_race(
    authenticated_customer_client,
):
    race = RaceFactory()
    RaceResultFactory(race=race, rank=1, speed=Decimal("1500.000"))

    response = authenticated_customer_client.get(
        f"/api/races/{race.pk}/results/"
    )

    assert response.status_code == 200
    assert response.data["count"] == 1


def test_race_results_for_one_race_are_ordered_by_rank(
    authenticated_customer_client,
):
    race = RaceFactory()
    second = RaceResultFactory(
        race=race,
        rank=2,
        bird_ring_number="SECOND",
        speed=Decimal("1400.000"),
    )
    first = RaceResultFactory(
        race=race,
        rank=1,
        bird_ring_number="FIRST",
        speed=Decimal("1500.000"),
    )

    response = authenticated_customer_client.get(
        f"/api/races/{race.pk}/results/"
    )

    assert response.status_code == 200
    assert result_ids(response) == [first.pk, second.pk]


def test_global_search_by_bird_ring_number_works(
    authenticated_customer_client,
):
    race = RaceFactory()
    matching = RaceResultFactory(
        race=race,
        bird_ring_number="ABC-123",
        competitor_name="Different",
    )
    RaceResultFactory(
        race=race,
        bird_ring_number="XYZ-999",
        competitor_name="Other",
    )

    response = authenticated_customer_client.get(
        "/api/race-results/",
        {"bird_ring_number": "ABC"},
    )

    assert response.status_code == 200
    assert result_ids(response) == [matching.pk]


def test_global_search_by_competitor_name_works(
    authenticated_customer_client,
):
    race = RaceFactory()
    matching = RaceResultFactory(
        race=race,
        bird_ring_number="A-1",
        competitor_name="Ali Hassan",
    )
    RaceResultFactory(
        race=race,
        bird_ring_number="B-1",
        competitor_name="Other",
    )

    response = authenticated_customer_client.get(
        "/api/race-results/",
        {"competitor_name": "Ali"},
    )

    assert response.status_code == 200
    assert result_ids(response) == [matching.pk]


def test_global_q_search_works_across_ring_and_competitor(
    authenticated_customer_client,
):
    race = RaceFactory()
    ring_match = RaceResultFactory(
        race=race,
        rank=1,
        bird_ring_number="Q-RING",
        competitor_name="No match",
    )
    name_match = RaceResultFactory(
        race=race,
        rank=2,
        bird_ring_number="OTHER",
        competitor_name="Q Name",
    )

    response = authenticated_customer_client.get(
        "/api/race-results/",
        {"q": "Q"},
    )

    assert response.status_code == 200
    assert set(result_ids(response)) == {ring_match.pk, name_match.pk}


def test_include_race_true_returns_nested_race_object(
    authenticated_customer_client,
):
    race = RaceFactory(title="Nested race")
    RaceResultFactory(race=race, rank=1)

    response = authenticated_customer_client.get(
        "/api/race-results/",
        {"include_race": "true"},
    )

    assert response.status_code == 200
    assert response.data["results"][0]["race"]["id"] == race.pk
    assert response.data["results"][0]["race"]["title"] == "Nested race"


def test_include_race_false_does_not_include_nested_race_object(
    authenticated_customer_client,
):
    race = RaceFactory()
    RaceResultFactory(race=race, rank=1)

    response = authenticated_customer_client.get(
        "/api/race-results/",
        {"include_race": "false"},
    )

    assert response.status_code == 200
    assert "race" not in response.data["results"][0]
