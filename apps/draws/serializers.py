from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.draws.models import (
    WeeklyDraw,
    WeeklyDrawEffectiveStatus,
    WeeklyDrawEntry,
    WeeklyDrawEntryStatus,
    WeeklyDrawStatus,
)


class TimezoneAwareDateTimeField(serializers.DateTimeField):
    """Reject naive datetimes instead of silently assuming the default timezone."""

    def enforce_timezone(self, value):
        if timezone.is_naive(value):
            raise serializers.ValidationError(
                _("Datetime value must be timezone-aware.")
            )
        return super().enforce_timezone(value)


class WeeklyDrawSerializer(serializers.ModelSerializer):
    """Admin serializer for weekly onboarding draw windows (BE-DRW-01 / BE-DRW-02)."""

    title = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
        default="",
    )
    starts_at = TimezoneAwareDateTimeField()
    ends_at = TimezoneAwareDateTimeField()
    status = serializers.ChoiceField(
        choices=WeeklyDrawStatus.choices,
        required=False,
        default=WeeklyDrawStatus.DRAFT,
    )
    effective_status = serializers.SerializerMethodField(read_only=True)
    entries_count = serializers.SerializerMethodField(read_only=True)
    is_participant_set_frozen = serializers.SerializerMethodField(read_only=True)
    winner_selection_status = serializers.SerializerMethodField(read_only=True)
    metadata = serializers.JSONField(required=False, default=dict)

    class Meta:
        model = WeeklyDraw
        fields = [
            "id",
            "title",
            "starts_at",
            "ends_at",
            "status",
            "effective_status",
            "entries_count",
            "is_participant_set_frozen",
            "winner_selection_status",
            "created_by",
            "metadata",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "effective_status",
            "entries_count",
            "is_participant_set_frozen",
            "winner_selection_status",
            "created_by",
            "created",
            "modified",
        ]

    @extend_schema_field(
        serializers.ChoiceField(choices=WeeklyDrawEffectiveStatus.choices)
    )
    def get_effective_status(self, obj: WeeklyDraw) -> str:
        return obj.effective_status

    @extend_schema_field(serializers.IntegerField(min_value=0))
    def get_entries_count(self, obj: WeeklyDraw) -> int:
        annotated = getattr(obj, "entries_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.entries.filter(
            status=WeeklyDrawEntryStatus.ASSIGNED,
            deleted__isnull=True,
        ).count()

    @extend_schema_field(serializers.BooleanField())
    def get_is_participant_set_frozen(self, obj: WeeklyDraw) -> bool:
        if obj.status in (WeeklyDrawStatus.CLOSED, WeeklyDrawStatus.FINALIZED):
            return True
        return isinstance(obj.metadata, dict) and isinstance(
            obj.metadata.get("execution_freeze"), dict
        )

    @extend_schema_field(serializers.CharField())
    def get_winner_selection_status(self, obj: WeeklyDraw) -> str:
        if obj.status == WeeklyDrawStatus.FINALIZED:
            return "FINALIZED"
        if isinstance(obj.metadata, dict) and isinstance(
            obj.metadata.get("execution_freeze"), dict
        ):
            return str(
                obj.metadata["execution_freeze"].get(
                    "winner_selection_status",
                    "BLOCKED_PENDING_PRODUCT_DECISION",
                )
            )
        if obj.status == WeeklyDrawStatus.CLOSED:
            return "BLOCKED_PENDING_PRODUCT_DECISION"
        return "PENDING_EXECUTION"

    def validate_metadata(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                _("Weekly draw metadata must be a JSON object.")
            )
        return value

    def validate(self, attrs):
        starts_at = attrs.get(
            "starts_at",
            getattr(self.instance, "starts_at", None),
        )
        ends_at = attrs.get(
            "ends_at",
            getattr(self.instance, "ends_at", None),
        )
        if starts_at is not None and ends_at is not None and starts_at >= ends_at:
            raise serializers.ValidationError(
                {
                    "ends_at": _(
                        "Weekly draw start time must be earlier than end time."
                    )
                }
            )
        return attrs


class WeeklyDrawEntryAdminSerializer(serializers.ModelSerializer):
    """Manager read-only serializer for weekly draw entries (BE-DRW-01 / BE-DRW-02)."""

    draw_id = serializers.IntegerField(read_only=True, allow_null=True)
    user_id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = WeeklyDrawEntry
        fields = [
            "id",
            "draw_id",
            "user_id",
            "username",
            "source",
            "status",
            "assigned_at",
            "created",
            "modified",
        ]
        read_only_fields = fields


class MyWeeklyDrawEntryStatusSerializer(serializers.Serializer):
    """Safe user-facing read-only status serializer for `GET /api/draws/my-weekly-entry/`."""

    has_entry = serializers.BooleanField(read_only=True)
    status = serializers.ChoiceField(
        choices=WeeklyDrawEntryStatus.choices,
        allow_null=True,
        read_only=True,
    )
    draw_id = serializers.IntegerField(allow_null=True, read_only=True)
    draw_starts_at = serializers.DateTimeField(allow_null=True, read_only=True)
    draw_ends_at = serializers.DateTimeField(allow_null=True, read_only=True)
    assigned_at = serializers.DateTimeField(allow_null=True, read_only=True)
