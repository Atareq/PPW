from rest_framework import serializers

from apps.lucky_wheel.models import (
    LuckyWheelGrant,
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelSpin,
)
from apps.promotions.models import UserPromotionGrant
from apps.promotions.serializers import UserPromotionGrantSerializer


class LuckyWheelPrizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LuckyWheelPrize
        fields = [
            "id",
            "label",
            "description",
            "prize_type",
            "target_profile_type",
            "weight",
            "is_active",
            "points_amount",
            "discount_offer",
            "cashback_offer",
            "badge_type",
            "quantity_total",
            "quantity_awarded",
            "metadata",
            "created",
            "modified",
        ]
        read_only_fields = ["id", "quantity_awarded", "created", "modified"]


class LuckyWheelSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LuckyWheelPrize
        fields = [
            "id",
            "label",
            "description",
            "prize_type",
            "points_amount",
            "badge_type",
            "metadata",
        ]
        read_only_fields = fields


class LuckyWheelGrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = LuckyWheelGrant
        fields = [
            "id",
            "source_type",
            "source_id",
            "total_attempts",
            "remaining_attempts",
            "expires_at",
            "is_active",
            "metadata",
            "created",
        ]
        read_only_fields = fields


class LuckyWheelSpinSerializer(serializers.ModelSerializer):
    class Meta:
        model = LuckyWheelSpin
        fields = [
            "id",
            "grant",
            "prize",
            "status",
            "prize_snapshot",
            "awarded_at",
            "metadata",
            "created",
        ]
        read_only_fields = fields


class LuckyWheelCurrentSerializer(serializers.Serializer):
    eligible = serializers.BooleanField(read_only=True)
    remaining_attempts = serializers.IntegerField(read_only=True)
    cooldown_until = serializers.DateTimeField(read_only=True, allow_null=True)
    segments = LuckyWheelSegmentSerializer(many=True, read_only=True)
    active_grants = LuckyWheelGrantSerializer(many=True, read_only=True)
    user_promotion_grants = UserPromotionGrantSerializer(many=True, read_only=True)


class LuckyWheelSpinRequestSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField()


class LuckyWheelSpinResponseSerializer(serializers.Serializer):
    spin_id = serializers.IntegerField(read_only=True)
    remaining_attempts = serializers.IntegerField(read_only=True)
    prize = serializers.JSONField(read_only=True)
    awarded = serializers.BooleanField(read_only=True)
    balances = serializers.JSONField(read_only=True)
    user_promotion_grant = UserPromotionGrantSerializer(
        read_only=True,
        allow_null=True,
    )


class AdminLuckyWheelSpinSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = LuckyWheelSpin
        fields = [
            "id",
            "user_id",
            "profile_id",
            "username",
            "grant",
            "prize",
            "status",
            "prize_snapshot",
            "awarded_at",
            "metadata",
            "created",
        ]
        read_only_fields = fields


class AdminLuckyWheelGrantSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = LuckyWheelGrant
        fields = [
            "id",
            "user_id",
            "profile_id",
            "username",
            "source_type",
            "source_id",
            "total_attempts",
            "remaining_attempts",
            "expires_at",
            "is_active",
            "idempotency_key",
            "metadata",
            "created",
        ]
        read_only_fields = fields


class AdminGrantSpinSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    profile_id = serializers.IntegerField(required=False, allow_null=True)
    attempts = serializers.IntegerField(min_value=1, default=1)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)

    SOURCE_CHOICES = [(v, v) for v in LuckyWheelGrantSourceType.values]
    source_type = serializers.ChoiceField(
        choices=SOURCE_CHOICES,
        default=LuckyWheelGrantSourceType.MANAGER_GRANT,
    )


def grant_for_spin(spin):
    return (
        UserPromotionGrant.objects.select_related(
            "discount_offer",
            "cashback_offer",
        )
        .filter(source_type="lucky_wheel", source_id=str(spin.pk))
        .first()
    )
