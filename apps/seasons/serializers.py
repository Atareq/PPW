from rest_framework import serializers

from apps.seasons.models import Season, SeasonSellerInsight


class SeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Season
        fields = [
            "id",
            "title",
            "starts_at",
            "ends_at",
            "status",
            "calculated_at",
            "finalized_by",
            "metadata",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "status",
            "calculated_at",
            "finalized_by",
            "created",
            "modified",
        ]


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
            "completed_orders_count",
            "avg_rating",
            "total_sales_amount",
            "rank",
            "points_awarded",
            "metadata",
            "created",
        ]
        read_only_fields = fields
