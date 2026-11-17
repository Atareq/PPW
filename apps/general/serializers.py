from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.general.models import (
    AdminAnnouncement,
    AnnouncementDismissal,
    AnnouncementDurationUnit,
    AppSettings,
    AssetMedia,
)


class AssetMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetMedia
        fields = [
            "id",
            "media_url",
            "media_type",
            "alt_text",
            "is_primary",
            "order",
        ]
        read_only_fields = ["id"]

    def to_internal_value(self, data):
        if isinstance(data, dict) and "id" in data:
            raise serializers.ValidationError(
                {"id": (_("Media ids are not accepted for nested asset media " "writes."))}
            )
        return super().to_internal_value(data)


class CurrentAnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminAnnouncement
        fields = [
            "id",
            "text",
            "starts_at",
            "expires_at",
            "show_in_bar",
            "created",
            "modified",
        ]
        read_only_fields = fields


class AnnouncementDismissSerializer(serializers.Serializer):
    def validate(self, attrs):
        if isinstance(self.initial_data, dict) and self.initial_data:
            raise serializers.ValidationError(
                {
                    field: [
                        _(
                            "Client cannot specify user, profile, or dismissal parameters."
                        )
                    ]
                    for field in sorted(self.initial_data.keys())
                }
            )
        return attrs


class AnnouncementDismissResponseSerializer(serializers.ModelSerializer):
    announcement_id = serializers.IntegerField(
        source="announcement.id",
        read_only=True,
    )

    class Meta:
        model = AnnouncementDismissal
        fields = [
            "id",
            "announcement",
            "announcement_id",
            "dismissed_at",
        ]
        read_only_fields = fields


class AdminAnnouncementSerializer(serializers.ModelSerializer):
    starts_at = serializers.DateTimeField(required=False)
    expires_at = serializers.DateTimeField(required=False)
    duration_value = serializers.IntegerField(
        min_value=1,
        required=False,
        write_only=True,
    )
    duration_unit = serializers.ChoiceField(
        choices=AnnouncementDurationUnit.choices,
        required=False,
        write_only=True,
    )

    class Meta:
        model = AdminAnnouncement
        fields = [
            "id",
            "text",
            "starts_at",
            "expires_at",
            "duration_value",
            "duration_unit",
            "is_active",
            "show_in_bar",
            "created_by",
            "created",
            "modified",
        ]
        read_only_fields = ["id", "created_by", "created", "modified"]

    def validate_text(self, value):
        if not value or not str(value).strip():
            raise serializers.ValidationError(
                _("Announcement text is required.")
            )
        return value

    def validate(self, attrs):
        has_expires_at = (
            "expires_at" in attrs and attrs["expires_at"] is not None
        )
        has_duration_value = (
            "duration_value" in attrs and attrs["duration_value"] is not None
        )
        has_duration_unit = (
            "duration_unit" in attrs and attrs["duration_unit"] is not None
        )

        if has_duration_value != has_duration_unit:
            missing_field = (
                "duration_unit" if has_duration_value else "duration_value"
            )
            raise serializers.ValidationError(
                {
                    missing_field: [
                        _(
                            "Both duration_value and duration_unit are required when specifying duration."
                        )
                    ]
                }
            )

        has_duration = has_duration_value and has_duration_unit
        if has_expires_at and has_duration:
            raise serializers.ValidationError(
                {
                    "expires_at": [
                        _(
                            "Provide either expires_at or duration_value with duration_unit, not both."
                        )
                    ]
                }
            )

        if self.instance is None and not has_expires_at and not has_duration:
            raise serializers.ValidationError(
                {
                    "expires_at": [
                        _(
                            "Either expires_at or duration_value with duration_unit is required."
                        )
                    ]
                }
            )

        if "starts_at" in attrs and attrs["starts_at"] is not None:
            starts_at = attrs["starts_at"]
        elif self.instance is not None:
            starts_at = self.instance.starts_at
        else:
            starts_at = timezone.now()
            attrs["starts_at"] = starts_at

        if has_duration:
            duration_value = attrs.pop("duration_value")
            duration_unit = attrs.pop("duration_unit")
            if duration_unit == AnnouncementDurationUnit.HOURS:
                attrs["expires_at"] = starts_at + timedelta(hours=duration_value)
            else:
                attrs["expires_at"] = starts_at + timedelta(days=duration_value)

        expires_at = attrs.get(
            "expires_at",
            getattr(self.instance, "expires_at", None),
        )
        if (
            starts_at is not None
            and expires_at is not None
            and starts_at >= expires_at
        ):
            raise serializers.ValidationError(
                {
                    "expires_at": [
                        _(
                            "Announcement expiration time must be later than start time."
                        )
                    ]
                }
            )

        return attrs


class AppSettingsSerializer(serializers.ModelSerializer):
    """Client-facing feature flags; only the computed visibility is exposed."""

    referral_share_visible = serializers.BooleanField(
        source="is_referral_share_visible",
        read_only=True,
    )

    class Meta:
        model = AppSettings
        fields = ["referral_share_visible"]
        read_only_fields = fields


class AdminAppSettingsSerializer(serializers.ModelSerializer):
    referral_share_visible = serializers.BooleanField(
        source="is_referral_share_visible",
        read_only=True,
    )

    class Meta:
        model = AppSettings
        fields = [
            "referral_share_enabled",
            "referral_share_starts_at",
            "referral_share_ends_at",
            "referral_share_visible",
            "modified",
        ]
        read_only_fields = ["referral_share_visible", "modified"]

    def validate(self, attrs):
        starts_at = attrs.get(
            "referral_share_starts_at",
            getattr(self.instance, "referral_share_starts_at", None),
        )
        ends_at = attrs.get(
            "referral_share_ends_at",
            getattr(self.instance, "referral_share_ends_at", None),
        )
        if (
            starts_at is not None
            and ends_at is not None
            and starts_at >= ends_at
        ):
            raise serializers.ValidationError(
                {
                    "referral_share_ends_at": [
                        _(
                            "Referral share end time must be later than "
                            "start time."
                        )
                    ]
                }
            )
        return attrs
