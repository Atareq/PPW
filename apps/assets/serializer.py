from rest_framework import serializers

from apps.assets.constants import (
    PRODUCT_ASSET_FIELDS,
    PRODUCT_ASSET_READ_ONLY_FIELDS,
)
from apps.assets.models import Accessory, Bird, Feed, Supplement, Supply
from apps.assets.services import BirdValidator, ProductAssetCreationService


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


class ProductAssetSerializer(serializers.ModelSerializer):
    class Meta:
        fields = PRODUCT_ASSET_FIELDS
        read_only_fields = PRODUCT_ASSET_READ_ONLY_FIELDS

    def create(self, validated_data):
        request = self.context.get("request")
        owner = (
            getattr(request.user, "active_profile", None) if request else None
        )
        if owner is None:
            raise serializers.ValidationError("Active profile not found.")

        return ProductAssetCreationService.create_product(
            self.Meta.model,
            owner,
            validated_data,
        )


class SupplySerializer(ProductAssetSerializer):
    class Meta(ProductAssetSerializer.Meta):
        model = Supply


class AccessorySerializer(ProductAssetSerializer):
    class Meta(ProductAssetSerializer.Meta):
        model = Accessory


class FeedSerializer(ProductAssetSerializer):
    class Meta(ProductAssetSerializer.Meta):
        model = Feed


class SupplementSerializer(ProductAssetSerializer):
    class Meta(ProductAssetSerializer.Meta):
        model = Supplement
