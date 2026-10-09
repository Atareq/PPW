from django.utils import translation
from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.payload import localize_notification_text
from apps.users.models import Profile


class NotificationActorProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    type = serializers.CharField(read_only=True)
    nickname = serializers.CharField(read_only=True, allow_blank=True)

    def to_representation(self, instance):
        if instance is None:
            return None

        data = {
            "id": instance.pk,
            "type": instance.type,
        }
        nickname = getattr(instance, "nickname", "")
        if nickname:
            data["nickname"] = nickname
        return data


class NotificationSerializer(serializers.ModelSerializer):
    actor_profile = NotificationActorProfileSerializer(read_only=True)
    title = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "title",
            "body",
            "actor_profile",
            "object_type",
            "object_id",
            "metadata",
            "target_url",
            "is_read",
            "read_at",
            "created",
        ]
        read_only_fields = fields

    def get_title(self, obj):
        request = getattr(self, "context", {}).get("request") if hasattr(self, "context") else None
        lang = getattr(request, "LANGUAGE_CODE", None) or translation.get_language() or "ar"
        return localize_notification_text(obj.title, language=lang)

    def get_body(self, obj):
        request = getattr(self, "context", {}).get("request") if hasattr(self, "context") else None
        lang = getattr(request, "LANGUAGE_CODE", None) or translation.get_language() or "ar"
        return localize_notification_text(obj.body, language=lang)


class UnreadCountSerializer(serializers.Serializer):
    unread_count = serializers.IntegerField(read_only=True)


class MarkAllReadSerializer(serializers.Serializer):
    updated_count = serializers.IntegerField(read_only=True)


class ManualNotificationCreateSerializer(serializers.Serializer):
    recipient_profile_id = serializers.IntegerField()
    title = serializers.CharField(max_length=255, trim_whitespace=True)
    body = serializers.CharField(trim_whitespace=True)
    object_type = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )
    object_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    metadata = serializers.DictField(required=False)
    target_url = serializers.CharField(
        max_length=2048,
        required=False,
        allow_blank=True,
    )

    def validate_recipient_profile_id(self, value):
        if not Profile.objects.filter(
            pk=value,
            is_active=True,
            activated=True,
            is_valid=True,
            deleted__isnull=True,
        ).exists():
            raise serializers.ValidationError(_("Recipient profile was not found."))
        return value


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    dnd_enabled = serializers.BooleanField(required=False)
    dnd_start = serializers.TimeField(required=False, allow_null=True)
    dnd_end = serializers.TimeField(required=False, allow_null=True)

    class Meta:
        model = NotificationPreference
        fields = [
            "dnd_enabled",
            "dnd_start",
            "dnd_end",
        ]

    def validate(self, attrs):
        if isinstance(getattr(self, "initial_data", None), dict):
            forbidden_fields = {
                "user",
                "user_id",
                "profile",
                "profile_id",
            } & set(self.initial_data.keys())
            if forbidden_fields:
                raise serializers.ValidationError(
                    {
                        field: _("This field is not allowed.")
                        for field in sorted(forbidden_fields)
                    }
                )
        return attrs

