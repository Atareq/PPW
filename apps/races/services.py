from django.db import transaction
from django.db.models import Max

from apps.races.models import RaceResult


def _next_placeholder_rank(race_id: int) -> int:
    max_rank = (
        RaceResult.objects.filter(race_id=race_id).aggregate(Max("rank"))[
            "rank__max"
        ]
        or 0
    )
    return max_rank + 1


def recalculate_race_ranks(race_id: int):
    """Re-rank locked results by speed, arrival time, then id."""

    with transaction.atomic():
        results = list(
            RaceResult.objects.select_for_update()
            .filter(race_id=race_id)
            .order_by("-speed", "arrival_datetime", "id")
        )
        changed = []
        for rank, result in enumerate(results, start=1):
            if result.rank != rank:
                result.rank = rank
                changed.append(result)

        if not changed:
            return results

        # Move changed rows away from final ranks before enforcing uniqueness.
        max_rank = max((result.rank for result in results), default=0)
        temporary_rank = max_rank + len(results) + 1
        for result in changed:
            result.rank = temporary_rank
            temporary_rank += 1
        RaceResult.objects.bulk_update(changed, ["rank"])

        for rank, result in enumerate(results, start=1):
            result.rank = rank
        RaceResult.objects.bulk_update(changed, ["rank"])

        return list(RaceResult.objects.filter(race_id=race_id))


def create_race_result(validated_data):
    with transaction.atomic():
        race = validated_data["race"]
        result = RaceResult.objects.create(
            **validated_data,
            rank=_next_placeholder_rank(race.pk),
        )
        recalculate_race_ranks(race.pk)
        result.refresh_from_db()
        return result


def update_race_result(instance, validated_data):
    with transaction.atomic():
        result = RaceResult.objects.select_for_update().get(pk=instance.pk)
        for field, value in validated_data.items():
            setattr(result, field, value)
        result.save()
        recalculate_race_ranks(result.race_id)
        result.refresh_from_db()
        return result


def delete_race_result(instance):
    with transaction.atomic():
        result = RaceResult.objects.select_for_update().get(pk=instance.pk)
        race_id = result.race_id
        result.delete()
        recalculate_race_ranks(race_id)


def bulk_create_race_results(race, results_data):
    with transaction.atomic():
        next_rank = _next_placeholder_rank(race.pk)
        created = []
        for item in results_data:
            created.append(
                RaceResult.objects.create(
                    race=race,
                    rank=next_rank,
                    **item,
                )
            )
            next_rank += 1

        recalculate_race_ranks(race.pk)
        return list(RaceResult.objects.filter(race=race).order_by("rank"))
