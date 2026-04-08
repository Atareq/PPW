from rest_framework import serializers

from apps.assets.models import Bird
from apps.assets.services import BirdValidator


class BirdSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bird
        fields = "__all__"
        read_only_fields = ["owner"]

    def validate(self, attrs):
        request = self.context.get("request")
        owner = (
            getattr(request.user, "active_profile", None) if request else None
        )

        return BirdValidator.validate(
            attrs=attrs,
            instance=self.instance,
            owner=owner,
        )

    def create(self, validated_data):
        validated_data["owner"] = self.context["request"].user.active_profile
        return super().create(validated_data)
