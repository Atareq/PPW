from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.seasons.models import (
    Season,
    SeasonPrize,
    SeasonSellerInsight,
    SeasonStatus,
)


class SeasonPrizeSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = SeasonPrize
        fields = [
            "id",
            "rank",
            "pp_points",
            "symbolic_prize_code",
            "title",
        ]
        read_only_fields = fields


class SeasonPrizeSerializer(serializers.ModelSerializer):
    rank = serializers.IntegerField()
    pp_points = serializers.IntegerField()

    class Meta:
        model = SeasonPrize
        fields = [
            "id",
            "season",
            "rank",
            "pp_points",
            "symbolic_prize_code",
            "title",
            "metadata",
            "created_by",
            "modified_by",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "season",
            "created_by",
            "modified_by",
            "created",
            "modified",
        ]

    def validate_rank(self, value):
        if value < 1 or value > 3:
            raise serializers.ValidationError(
                _("Season prize rank must be between 1 and 3.")
            )
        return value

    def validate_pp_points(self, value):
        if value < 0:
            raise serializers.ValidationError(
                _("Season prize points must be zero or positive.")
            )
        return value


class SeasonSerializer(serializers.ModelSerializer):
    finalized_at = serializers.DateTimeField(read_only=True)
    prizes = SeasonPrizeSummarySerializer(many=True, read_only=True)

    class Meta:
        model = Season
        fields = [
            "id",
            "title",
            "starts_at",
            "ends_at",
            "status",
            "calculated_at",
            "finalized_at",
            "finalized_by",
            "prizes",
            "metadata",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "status",
            "calculated_at",
            "finalized_at",
            "finalized_by",
            "prizes",
            "created",
            "modified",
        ]

    def validate(self, attrs):
        instance = self.instance
        if instance is not None and instance.status == SeasonStatus.FINALIZED:
            errors = {}
            if "starts_at" in attrs and attrs["starts_at"] != instance.starts_at:
                errors["starts_at"] = _(
                    "Finalized season dates cannot be modified."
                )
            if "ends_at" in attrs and attrs["ends_at"] != instance.ends_at:
                errors["ends_at"] = _(
                    "Finalized season dates cannot be modified."
                )
            if errors:
                raise serializers.ValidationError(errors)

        starts_at = attrs.get(
            "starts_at",
            instance.starts_at if instance is not None else None,
        )
        ends_at = attrs.get(
            "ends_at",
            instance.ends_at if instance is not None else None,
        )
        if starts_at and ends_at and starts_at >= ends_at:
            raise serializers.ValidationError(
                {"ends_at": _("Season end must be after start.")}
            )
        return attrs


class SeasonSellerInsightSerializer(serializers.ModelSerializer):
    seller_id = serializers.IntegerField(read_only=True)
    seller_nickname = serializers.CharField(source="seller.nickname", read_only=True)

    class Meta:
        model = SeasonSellerInsight
        fields = [
            "id",
            "season",
            "seller_id",
            "seller_nickname",
            "completed_sales_count",
            "sales_score",
            "ratings_count",
            "avg_rating",
            "rating_score",
            "seller_cancellations_count",
            "reliability_score",
            "interaction_opportunities_count",
            "interaction_replied_count",
            "interaction_score",
            "season_score",
            "rank",
            "completed_orders_count",
            "total_sales_amount",
            "points_awarded",
            "metadata",
            "created",
        ]
        read_only_fields = fields
