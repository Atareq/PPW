from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count
from django.db import transaction
from decimal import Decimal
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
from apps.ratings.models import AssetRating


class RemovedThumbnailUrlMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict) and "images" in data:
            raise serializers.ValidationError(
                {"images": ("images is no longer supported. Use media instead.")}
            )

        if isinstance(data, dict) and "thumbnail_url" in data:
            raise serializers.ValidationError(
                {
                    "thumbnail_url": (
                        "thumbnail_url is no longer supported. " "Use media instead."
                    )
                }
            )
        return super().to_internal_value(data)


class BirdSerializer(RemovedThumbnailUrlMixin, serializers.ModelSerializer):
    media = AssetMediaSerializer(many=True, required=False)
    qr_payload_url = serializers.SerializerMethodField()
    seller_nickname = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    class Meta:
        model = Bird
        fields = "__all__"
        read_only_fields = [
            "owner",
            "public_id",
            "qr_payload_url",
            "seller_nickname",
            "rating",
            "review_count",
        ]

    def get_qr_payload_url(self, obj):
        base_url = settings.PUBLIC_FRONTEND_BASE_URL.rstrip("/")
        return f"{base_url}/birds/{obj.public_id}"

    def get_seller_nickname(self, obj):
        seller_profile = getattr(obj.owner, "sellerprofile", None)
        return getattr(seller_profile, "nickname", "") or ""

    def get_rating(self, obj):
        return _asset_rating_value(obj)

    def get_review_count(self, obj):
        return _asset_review_count(obj)

    def validate(self, attrs):
        request = self.context.get("request")
        owner = request.user.active_profile if request else None
        if owner is None:
            raise serializers.ValidationError({"owner": "Owner profile is required."})

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


class PublicAssetMediaSerializer(serializers.Serializer):
    media_url = serializers.CharField(read_only=True)
    media_type = serializers.CharField(read_only=True)
    alt_text = serializers.CharField(read_only=True)
    is_primary = serializers.BooleanField(read_only=True)
    order = serializers.IntegerField(read_only=True)


class PublicBirdSerializer(serializers.ModelSerializer):
    media = PublicAssetMediaSerializer(many=True, read_only=True)
    hatch_date = serializers.DateField(source="birthday", read_only=True)
    seller = serializers.SerializerMethodField()

    class Meta:
        model = Bird
        fields = [
            "public_id",
            "ring_number",
            "title",
            "name",
            "gender",
            "colour",
            "hatch_date",
            "media",
            "seller",
        ]
        read_only_fields = fields

    def get_seller(self, obj):
        data = {"type": obj.owner.type}
        seller_profile = getattr(obj.owner, "sellerprofile", None)
        nickname = getattr(seller_profile, "nickname", "")
        if nickname:
            data["nickname"] = nickname
        return data


class ProductAssetSerializer(
    RemovedThumbnailUrlMixin,
    serializers.ModelSerializer,
):
    media = AssetMediaSerializer(many=True, required=False)
    rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    class Meta:
        fields = PRODUCT_ASSET_FIELDS
        read_only_fields = PRODUCT_ASSET_READ_ONLY_FIELDS

    def create(self, validated_data):
        media_data = validated_data.pop("media", [])
        request = self.context.get("request")
        try:
            owner = request.user.active_profile
        except PermissionDenied:
            if request.user.is_superuser:
                owner = None
            else:
                raise

        return ProductAssetCreationService.create_product(
            self.Meta.model,
            owner,
            validated_data,
            media_data=media_data,
        )

    def get_rating(self, obj):
        return _asset_rating_value(obj)

    def get_review_count(self, obj):
        return _asset_review_count(obj)

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


def _asset_rating_value(obj):
    annotated_rating = getattr(obj, "rating_annotation", None)
    if annotated_rating is not None:
        return _normalize_decimal(annotated_rating)

    stored_rating = getattr(obj, "avg_rating", None)
    if stored_rating is not None:
        return _normalize_decimal(stored_rating)

    aggregate = AssetRating.objects.filter(asset_id=obj.pk).aggregate(
        rating=Avg("stars")
    )
    return _normalize_decimal(aggregate["rating"] or Decimal("0.00"))


def _asset_review_count(obj):
    annotated_count = getattr(obj, "review_count_annotation", None)
    if annotated_count is not None:
        return annotated_count

    stored_count = getattr(obj, "ratings_count", None)
    if stored_count is not None:
        return stored_count

    aggregate = AssetRating.objects.filter(asset_id=obj.pk).aggregate(count=Count("id"))
    return aggregate["count"] or 0


def _normalize_decimal(value):
    return Decimal(value).quantize(Decimal("0.01"))
