from rest_framework import serializers

from apps.core.models import Notification, NotificationKind


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "kind",
            "title",
            "body",
            "is_read",
            "read_at",
            "metadata",
            "profile_type",
            "created",
        ]
        read_only_fields = fields


class NotificationCreateSerializer(serializers.Serializer):
    profile_id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=NotificationKind.choices)
    title = serializers.CharField(max_length=200)
    body = serializers.CharField(allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)
    profile_type = serializers.ChoiceField(
        choices=["Customer", "Seller", "All"],
        default="All",
    )


class UnreadCountSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)
