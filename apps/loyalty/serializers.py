from rest_framework import serializers

from apps.loyalty.constants import (
    BADGE_DEFINITIONS,
    REWARD_RULE_CONFIGURATION_MAX_KEYS,
    BadgeType,
)
from apps.loyalty.models import BadgeAward, PointTransaction, RewardRuleConfig
from apps.loyalty.services import BadgeService
from apps.promotions.services import CashbackService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile
from apps.users.models import Profile
from django.utils.translation import gettext_lazy as _


class PointBalanceSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(read_only=True)
    active_profile_id = serializers.IntegerField(read_only=True)
    balance = serializers.IntegerField(read_only=True)


class PointTransactionSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)
    profile_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = PointTransaction
        fields = [
            "id",
            "user_id",
            "transaction_type",
            "points",
            "balance_after",
            "reason",
            "source_type",
            "source_id",
            "profile_id",
            "created",
        ]
        read_only_fields = fields


class BadgeDefinitionSerializer(serializers.Serializer):
    badge_type = serializers.ChoiceField(
        choices=BadgeType.choices,
        read_only=True,
    )
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    eligible_profile_type = serializers.CharField(read_only=True)
    criteria_type = serializers.CharField(read_only=True)
    criteria_value = serializers.JSONField(read_only=True)
    icon_url = serializers.CharField(read_only=True, allow_blank=True)
    is_active = serializers.BooleanField(read_only=True)


class BadgeAwardSerializer(serializers.ModelSerializer):
    profile_id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    eligible_profile_type = serializers.SerializerMethodField()
    criteria_type = serializers.SerializerMethodField()
    criteria_value = serializers.SerializerMethodField()
    icon_url = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = BadgeAward
        fields = [
            "id",
            "profile_id",
            "badge_type",
            "name",
            "description",
            "eligible_profile_type",
            "criteria_type",
            "criteria_value",
            "icon_url",
            "is_active",
            "awarded_at",
            "expires_at",
            "revoked_at",
            "source_type",
            "source_id",
            "idempotency_key",
            "metadata",
        ]
        read_only_fields = fields

    def _definition(self, obj):
        return BADGE_DEFINITIONS[obj.badge_type]

    def get_name(self, obj):
        return self._definition(obj)["name"]

    def get_description(self, obj):
        return self._definition(obj)["description"]

    def get_eligible_profile_type(self, obj):
        return self._definition(obj)["eligible_profile_type"]

    def get_criteria_type(self, obj):
        return self._definition(obj)["criteria_type"]

    def get_criteria_value(self, obj):
        return self._definition(obj)["criteria_value"]

    def get_icon_url(self, obj):
        return self._definition(obj)["icon_url"]


class BadgeAwardCreateSerializer(serializers.Serializer):
    profile_id = serializers.IntegerField()
    badge_type = serializers.ChoiceField(choices=BadgeType.choices)
    source_type = serializers.CharField(required=False, allow_blank=True)
    source_id = serializers.CharField(required=False, allow_blank=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False)

    def validate_profile_id(self, value):
        try:
            return Profile.objects.get(pk=value)
        except Profile.DoesNotExist as exc:
            raise serializers.ValidationError(_("Profile was not found.")) from exc

    def create(self, validated_data):
        return BadgeService.award_badge(
            profile=validated_data["profile_id"],
            badge_type=validated_data["badge_type"],
            source_type=validated_data.get("source_type"),
            source_id=validated_data.get("source_id"),
            expires_at=validated_data.get("expires_at"),
            metadata=validated_data.get("metadata"),
        )


class BuyPointsWithCashbackSerializer(serializers.Serializer):
    cashback_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    idempotency_key = serializers.CharField()

    def validate(self, attrs):
        profile = self.context["request"].user.active_profile
        if profile.type != ProfileType.CUSTOMER:
            raise serializers.ValidationError(
                _("Only customer profiles can buy points with cashback.")
            )
        attrs["customer_profile"] = CustomerProfile.objects.get(pk=profile.pk)
        return attrs

    def create(self, validated_data):
        cashback_transaction, point_transaction = (
            CashbackService.buy_points_with_cashback(
                customer_profile=validated_data["customer_profile"],
                cashback_amount=validated_data["cashback_amount"],
                idempotency_key=validated_data["idempotency_key"],
            )
        )
        return {
            "cashback_transaction": cashback_transaction,
            "point_transaction": point_transaction,
        }


class BuyPointsWithCashbackResponseSerializer(serializers.Serializer):
    cashback_balance = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )
    point_balance = serializers.IntegerField(read_only=True)
    points_awarded = serializers.IntegerField(read_only=True)
    cashback_transaction_id = serializers.IntegerField(read_only=True)
    point_transaction_id = serializers.IntegerField(read_only=True)


class RewardRuleSerializer(serializers.Serializer):
    """Effective value of one system-defined reward rule."""

    code = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    enabled = serializers.BooleanField(read_only=True)
    points = serializers.IntegerField(read_only=True, allow_null=True)
    multiplier = serializers.DecimalField(
        max_digits=10,
        decimal_places=4,
        read_only=True,
        allow_null=True,
    )
    threshold = serializers.IntegerField(read_only=True, allow_null=True)
    configuration = serializers.JSONField(read_only=True)
    required_fields = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    is_implemented = serializers.BooleanField(read_only=True)
    is_overridden = serializers.BooleanField(read_only=True)
    updated_by_id = serializers.IntegerField(read_only=True, allow_null=True)
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)


class RewardRuleUpdateSerializer(serializers.ModelSerializer):
    """Partial update of tunable values; ``context["rule"]`` is the current rule."""

    class Meta:
        model = RewardRuleConfig
        fields = ["enabled", "points", "multiplier", "threshold", "configuration"]
        extra_kwargs = {
            "points": {"min_value": 0},
            "threshold": {"min_value": 0},
        }

    def validate_configuration(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("Configuration must be a JSON object."))
        if len(value) > REWARD_RULE_CONFIGURATION_MAX_KEYS:
            raise serializers.ValidationError(
                _("Configuration can have at most %(count)d keys.")
                % {"count": REWARD_RULE_CONFIGURATION_MAX_KEYS}
            )
        for item in value.values():
            items = item if isinstance(item, list) else [item]
            if any(isinstance(entry, (dict, list)) for entry in items):
                raise serializers.ValidationError(
                    _("Configuration values must be plain values or lists of them.")
                )
        return value

    def validate(self, attrs):
        rule = self.context["rule"]
        missing = {
            field: _("This field is required for this reward rule.")
            for field in rule.required_fields
            if attrs.get(field, getattr(rule, field)) is None
        }
        if missing:
            raise serializers.ValidationError(missing)
        return attrs
