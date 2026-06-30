from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.races.models import Race, RaceResult
from apps.races.services import (
    bulk_create_race_results,
    create_race_result,
    update_race_result,
)


class RaceSerializer(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(source="created", read_only=True)
    updated_at = serializers.DateTimeField(source="modified", read_only=True)

    class Meta:
        model = Race
        fields = [
            "id",
            "title",
            "season_year",
            "station_name",
            "release_datetime",
            "weather_condition",
            "total_birds",
            "competitors_count",
            "planned_distance_km",
            "coordinate_x",
            "coordinate_y",
            "notes",
            "created",
            "modified",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created",
            "modified",
            "created_at",
            "updated_at",
        ]


class RaceSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Race
        fields = [
            "id",
            "title",
            "season_year",
            "station_name",
            "release_datetime",
        ]
        read_only_fields = fields


class RaceResultSerializer(serializers.ModelSerializer):
    race = RaceSummarySerializer(read_only=True)
    created_at = serializers.DateTimeField(source="created", read_only=True)
    updated_at = serializers.DateTimeField(source="modified", read_only=True)

    class Meta:
        model = RaceResult
        fields = [
            "id",
            "race",
            "rank",
            "competitor_name",
            "bird_ring_number",
            "distance_km",
            "speed",
            "arrival_datetime",
            "basket_number",
            "created",
            "modified",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("include_race", False):
            data.pop("race", None)
        return data


class RaceDetailSerializer(RaceSerializer):
    results = RaceResultSerializer(many=True, read_only=True)

    class Meta(RaceSerializer.Meta):
        fields = RaceSerializer.Meta.fields + [
            "results",
        ]
        read_only_fields = RaceSerializer.Meta.read_only_fields + [
            "results",
        ]


class RaceResultValidationMixin:
    def validate(self, attrs):
        race = attrs.get("race") or self.context.get("race")
        if race is None and self.instance is not None:
            race = self.instance.race

        arrival_datetime = attrs.get("arrival_datetime")
        if arrival_datetime is None and self.instance is not None:
            arrival_datetime = self.instance.arrival_datetime

        if (
            race is not None
            and arrival_datetime is not None
            and arrival_datetime <= race.release_datetime
        ):
            raise serializers.ValidationError(
                {
                    "arrival_datetime": (
                        _("Arrival datetime must be after race release datetime.")
                    )
                }
            )

        ring_number = attrs.get("bird_ring_number")
        if ring_number is not None and race is not None:
            queryset = RaceResult.objects.filter(
                race=race,
                bird_ring_number=ring_number,
            )
            if self.instance is not None:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError(
                    {
                        "bird_ring_number": (
                            _("This bird ring number already exists in this race.")
                        )
                    }
                )

        return attrs


class RaceResultCreateSerializer(
    RaceResultValidationMixin,
    serializers.ModelSerializer,
):
    rank = serializers.IntegerField(read_only=True)

    class Meta:
        model = RaceResult
        fields = [
            "id",
            "race",
            "rank",
            "competitor_name",
            "bird_ring_number",
            "distance_km",
            "speed",
            "arrival_datetime",
            "basket_number",
        ]
        read_only_fields = [
            "id",
            "rank",
        ]

    def create(self, validated_data):
        return create_race_result(validated_data)

    def to_representation(self, instance):
        return RaceResultSerializer(
            instance,
            context=self.context,
        ).data


class RaceResultUpdateSerializer(
    RaceResultValidationMixin,
    serializers.ModelSerializer,
):
    rank = serializers.IntegerField(read_only=True)

    class Meta:
        model = RaceResult
        fields = [
            "id",
            "rank",
            "competitor_name",
            "bird_ring_number",
            "distance_km",
            "speed",
            "arrival_datetime",
            "basket_number",
        ]
        read_only_fields = [
            "id",
            "rank",
        ]

    def update(self, instance, validated_data):
        return update_race_result(instance, validated_data)

    def to_representation(self, instance):
        return RaceResultSerializer(
            instance,
            context=self.context,
        ).data


class BulkRaceResultItemSerializer(
    RaceResultValidationMixin,
    serializers.Serializer,
):
    competitor_name = serializers.CharField(
        max_length=255,
        allow_blank=False,
        trim_whitespace=True,
    )
    bird_ring_number = serializers.CharField(
        max_length=100,
        allow_blank=False,
        trim_whitespace=True,
    )
    distance_km = serializers.DecimalField(
        max_digits=10,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    speed = serializers.DecimalField(
        max_digits=10,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    arrival_datetime = serializers.DateTimeField()
    basket_number = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )


class BulkRaceResultCreateSerializer(serializers.Serializer):
    results = BulkRaceResultItemSerializer(many=True, allow_empty=False)

    def validate_results(self, results):
        race = self.context["race"]
        ring_numbers = [item["bird_ring_number"] for item in results]
        duplicate_ring_numbers = {
            ring for ring in ring_numbers if ring_numbers.count(ring) > 1
        }
        if duplicate_ring_numbers:
            raise serializers.ValidationError(
                _("Duplicate bird_ring_number entries are not allowed.")
            )

        existing_ring_numbers = set(
            RaceResult.objects.filter(
                race=race,
                bird_ring_number__in=ring_numbers,
            ).values_list("bird_ring_number", flat=True)
        )
        if existing_ring_numbers:
            raise serializers.ValidationError(
                _("bird_ring_number already exists in this race.")
            )

        return results

    def create(self, validated_data):
        return bulk_create_race_results(
            self.context["race"],
            validated_data["results"],
        )

    def to_representation(self, instance):
        return {
            "results": RaceResultSerializer(
                instance,
                many=True,
                context=self.context,
            ).data
        }
