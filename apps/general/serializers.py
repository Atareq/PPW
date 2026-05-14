from rest_framework import serializers

from apps.general.models import AssetImage


class AssetImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetImage
        fields = [
            "id",
            "image_url",
            "alt_text",
            "is_primary",
            "order",
        ]
        read_only_fields = ["id"]

    def to_internal_value(self, data):
        if isinstance(data, dict) and "id" in data:
            raise serializers.ValidationError(
                {
                    "id": (
                        "Image ids are not accepted for nested asset image "
                        "writes."
                    )
                }
            )
        return super().to_internal_value(data)
