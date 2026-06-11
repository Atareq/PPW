from django.db import transaction
from rest_framework import serializers

from apps.assets.constants import (
    PRODUCT_ASSET_FIELDS,
    PRODUCT_ASSET_READ_ONLY_FIELDS,
)
from apps.assets.models import Accessory, Bird, Feed, Supplement, Supply
from apps.assets.services import (
    AssetMediaWriteService,
    BirdValidator,
    ProductAssetCreationService,
)
from apps.general.serializers import AssetMediaSerializer


class RemovedThumbnailUrlMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict) and "images" in data:
            raise serializers.ValidationError(
                {
                    "images": (
                        "images is no longer supported. Use media instead."
                    )
                }
            )

        if isinstance(data, dict) and "thumbnail_url" in data:
            raise serializers.ValidationError(
                {
                    "thumbnail_url": (
                        "thumbnail_url is no longer supported. "
                        "Use media instead."
                    )
                }
            )
        return super().to_internal_value(data)


class BirdSerializer(RemovedThumbnailUrlMixin, serializers.ModelSerializer):
    media = AssetMediaSerializer(many=True, required=False)

    class Meta:
        model = Bird
        fields = "__all__"
        read_only_fields = ["owner"]

    def validate(self, attrs):
        request = self.context.get("request")
        owner = request.user.active_profile if request else None
        if owner is None:
            raise serializers.ValidationError(
                {"owner": "Owner profile is required."}
            )

        return BirdValidator.validate(
            attrs=attrs,
            instance=self.instance,
            owner=owner,
        )

    def create(self, validated_data):
        media_data = validated_data.pop("media", [])
        validated_data["owner"] = self.context["request"].user.active_profile
        with transaction.atomic():
            bird = super().create(validated_data)
            AssetMediaWriteService.create_media(bird, media_data)
            return bird

    def update(self, instance, validated_data):
        media_data = validated_data.pop("media", None)
        with transaction.atomic():
            bird = super().update(instance, validated_data)
            if media_data is not None:
                AssetMediaWriteService.replace_media(bird, media_data)
            return bird


class ProductAssetSerializer(
    RemovedThumbnailUrlMixin,
    serializers.ModelSerializer,
):
    media = AssetMediaSerializer(many=True, required=False)

    class Meta:
        fields = PRODUCT_ASSET_FIELDS
        read_only_fields = PRODUCT_ASSET_READ_ONLY_FIELDS

    def create(self, validated_data):
        media_data = validated_data.pop("media", [])
        request = self.context.get("request")
        owner = request.user.active_profile

        return ProductAssetCreationService.create_product(
            self.Meta.model,
            owner,
            validated_data,
            media_data=media_data,
        )

    def update(self, instance, validated_data):
        media_data = validated_data.pop("media", None)
        with transaction.atomic():
            product = super().update(instance, validated_data)
            if media_data is not None:
                AssetMediaWriteService.replace_media(product, media_data)
            return product


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
