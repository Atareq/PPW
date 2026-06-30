from rest_framework import serializers

from apps.general.models import AssetMedia
from django.utils.translation import gettext_lazy as _


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
