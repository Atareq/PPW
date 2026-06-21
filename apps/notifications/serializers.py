from rest_framework import serializers

from apps.notifications.models import Notification


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
            "target_url",
            "is_read",
            "read_at",
            "created",
        ]
        read_only_fields = fields


class UnreadCountSerializer(serializers.Serializer):
    unread_count = serializers.IntegerField(read_only=True)


class MarkAllReadSerializer(serializers.Serializer):
    updated_count = serializers.IntegerField(read_only=True)

