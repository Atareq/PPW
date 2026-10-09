from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.notifications.models import PushDevice


class PushDeviceRegisterSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=4096, trim_whitespace=True)
    platform = serializers.ChoiceField(choices=PushDevice.Platform.choices)

    def validate_token(self, value):
        if not value.strip():
            raise serializers.ValidationError(_("Push token is required."))
        return value.strip()


class PushDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushDevice
        fields = ["id", "profile", "platform", "is_active", "last_seen_at", "created"]
        read_only_fields = fields
