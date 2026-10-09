from drf_writable_nested import WritableNestedModelSerializer
from rest_framework import serializers

from apps.assets.models import Asset
from apps.ratings.models import (
    AssetComment,
    AssetRating,
    DealRating,
    SellerComment,
    SellerRating,
)
from apps.ratings.services import (
    create_asset_comment,
    create_asset_rating,
    create_seller_comment,
    create_seller_rating,
)
from apps.users.models import Profile, SellerProfile
from django.utils.translation import gettext_lazy as _


class OwnerSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = [
            "id",
            "type",
        ]
        read_only_fields = fields


class AssetSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Asset
        fields = [
            "id",
            "title",
            "category",
        ]
        read_only_fields = fields


class SellerSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = SellerProfile
        fields = [
            "id",
            "nickname",
        ]
        read_only_fields = fields


class AssetInputSerializer(serializers.Serializer):
    id = serializers.IntegerField()


class SellerInputSerializer(serializers.Serializer):
    id = serializers.IntegerField()


class RatingCommentInputSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    text = serializers.CharField(allow_blank=False, trim_whitespace=True)

    def validate(self, attrs):
        if "id" in attrs:
            raise serializers.ValidationError(
                {"id": _("Rating comments must be created inline.")}
            )
        return attrs


class AssetCommentCreateSerializer(WritableNestedModelSerializer):
    asset = AssetInputSerializer()

    class Meta:
        model = AssetComment
        fields = [
            "id",
            "asset",
            "text",
            "is_rating_comment",
        ]
        read_only_fields = [
            "id",
            "is_rating_comment",
        ]

    def create(self, validated_data):
        request = self.context["request"]
        return create_asset_comment(
            request.user.active_profile,
            validated_data["asset"]["id"],
            validated_data["text"],
        )


class SellerCommentCreateSerializer(WritableNestedModelSerializer):
    seller = SellerInputSerializer()

    class Meta:
        model = SellerComment
        fields = [
            "id",
            "seller",
            "text",
            "is_rating_comment",
        ]
        read_only_fields = [
            "id",
            "is_rating_comment",
        ]

    def create(self, validated_data):
        request = self.context["request"]
        return create_seller_comment(
            request.user.active_profile,
            validated_data["seller"]["id"],
            validated_data["text"],
        )


class AssetCommentSerializer(serializers.ModelSerializer):
    owner = OwnerSummarySerializer(read_only=True)
    asset = AssetSummarySerializer(read_only=True)

    class Meta:
        model = AssetComment
        fields = [
            "id",
            "owner",
            "asset",
            "text",
            "is_rating_comment",
            "created",
            "modified",
        ]
        read_only_fields = fields


class SellerCommentSerializer(serializers.ModelSerializer):
    owner = OwnerSummarySerializer(read_only=True)
    seller = SellerSummarySerializer(read_only=True)

    class Meta:
        model = SellerComment
        fields = [
            "id",
            "owner",
            "seller",
            "text",
            "is_rating_comment",
            "created",
            "modified",
        ]
        read_only_fields = fields


class AssetRatingCreateSerializer(WritableNestedModelSerializer):
    asset = AssetInputSerializer()
    comment = RatingCommentInputSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = AssetRating
        fields = [
            "id",
            "asset",
            "stars",
            "comment",
        ]
        read_only_fields = [
            "id",
        ]

    def create(self, validated_data):
        request = self.context["request"]
        return create_asset_rating(
            request.user.active_profile,
            validated_data["asset"]["id"],
            validated_data["stars"],
            validated_data.get("comment"),
        )


class SellerRatingCreateSerializer(WritableNestedModelSerializer):
    seller = SellerInputSerializer()
    comment = RatingCommentInputSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = SellerRating
        fields = [
            "id",
            "seller",
            "stars",
            "comment",
        ]
        read_only_fields = [
            "id",
        ]

    def create(self, validated_data):
        request = self.context["request"]
        return create_seller_rating(
            request.user.active_profile,
            validated_data["seller"]["id"],
            validated_data["stars"],
            validated_data.get("comment"),
        )


class AssetRatingSerializer(serializers.ModelSerializer):
    owner = OwnerSummarySerializer(read_only=True)
    asset = AssetSummarySerializer(read_only=True)
    comment = AssetCommentSerializer(read_only=True)

    class Meta:
        model = AssetRating
        fields = [
            "id",
            "owner",
            "asset",
            "stars",
            "comment",
            "created",
            "modified",
        ]
        read_only_fields = fields


class SellerRatingSerializer(serializers.ModelSerializer):
    owner = OwnerSummarySerializer(read_only=True)
    seller = SellerSummarySerializer(read_only=True)
    comment = SellerCommentSerializer(read_only=True)

    class Meta:
        model = SellerRating
        fields = [
            "id",
            "owner",
            "seller",
            "stars",
            "comment",
            "created",
            "modified",
        ]
        read_only_fields = fields


class DealRatingCreateSerializer(serializers.Serializer):
    """Rate the other party of a completed deal; the direction is derived."""

    deal_id = serializers.IntegerField(min_value=1)
    stars = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        default="",
    )


class DealRatingSerializer(serializers.ModelSerializer):
    deal_id = serializers.IntegerField(read_only=True)
    rater_profile_id = serializers.IntegerField(read_only=True)
    rated_profile_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = DealRating
        fields = [
            "id",
            "deal_id",
            "direction",
            "rater_profile_id",
            "rated_profile_id",
            "stars",
            "comment",
            "created",
        ]
        read_only_fields = fields


class DealRatingStatusSerializer(serializers.Serializer):
    deal_id = serializers.IntegerField(read_only=True)
    role = serializers.CharField(read_only=True)
    has_rated = serializers.BooleanField(read_only=True)
    counterparty_has_rated = serializers.BooleanField(read_only=True)
    is_mutual = serializers.BooleanField(read_only=True)
    my_rating = DealRatingSerializer(read_only=True, allow_null=True)
    counterparty_rating = DealRatingSerializer(read_only=True, allow_null=True)
