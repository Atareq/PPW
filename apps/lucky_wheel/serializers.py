from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.lucky_wheel.models import (
    LuckyWheelGrant,
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

    def validate(self, attrs):
        instance = (
            LuckyWheelPrize.objects.get(pk=self.instance.pk)
            if self.instance is not None
            else LuckyWheelPrize()
        )
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict) from exc
            raise serializers.ValidationError({"detail": exc.messages}) from exc
        return attrs


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
        ]
        read_only_fields = fields


class LuckyWheelGrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = LuckyWheelGrant
        fields = [
            "id",
            "source_type",
            "total_attempts",
            "remaining_attempts",
            "expires_at",
            "is_active",
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
            "created",
        ]
        read_only_fields = fields


class LuckyWheelCurrentSerializer(serializers.Serializer):
    available = serializers.BooleanField(read_only=True)
    eligible = serializers.BooleanField(read_only=True)
    has_active_wheel = serializers.BooleanField(read_only=True)
    remaining_attempts = serializers.IntegerField(read_only=True)
    cooldown_until = serializers.DateTimeField(read_only=True, allow_null=True)
    segments = LuckyWheelSegmentSerializer(many=True, read_only=True)
    active_grants = LuckyWheelGrantSerializer(many=True, read_only=True)
    user_promotion_grants = UserPromotionGrantSerializer(many=True, read_only=True)


class LuckyWheelSpinRequestSerializer(serializers.Serializer):
    ALLOWED_FIELDS = {"idempotency_key"}

    idempotency_key = serializers.CharField(allow_blank=False, trim_whitespace=True)

    def validate(self, attrs):
        if isinstance(self.initial_data, dict):
            forbidden = sorted(set(self.initial_data.keys()) - self.ALLOWED_FIELDS)
            if forbidden:
                raise serializers.ValidationError(
                    {
                        field: [
                            _(
                                "Client cannot specify prize, outcome, slot, or grant parameters."
                            )
                        ]
                        for field in forbidden
                    }
                )
        return attrs


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


def grant_for_spin(spin):
    return (
        UserPromotionGrant.objects.select_related(
            "discount_offer",
            "cashback_offer",
        )
        .filter(source_type="lucky_wheel", source_id=str(spin.pk))
        .first()
    )
