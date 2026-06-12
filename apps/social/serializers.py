from rest_framework import serializers

from apps.assets.models import Asset
from apps.auctions.serializers import AuctionListSerializer
from apps.general.serializers import AssetMediaSerializer
from apps.social.models import SellerFollow


class SellerFollowSellerSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    nickname = serializers.CharField(read_only=True, allow_blank=True)
    country = serializers.CharField(read_only=True)
    currency = serializers.CharField(read_only=True)
    is_followed = serializers.BooleanField(read_only=True, default=True)

    def to_representation(self, instance):
        data = {
            "id": instance.pk,
            "nickname": instance.nickname,
            "country": instance.country,
            "currency": instance.currency,
            "is_followed": True,
        }
        return data


class SellerFollowSerializer(serializers.ModelSerializer):
    seller = SellerFollowSellerSerializer(read_only=True)

    class Meta:
        model = SellerFollow
        fields = [
            "id",
            "seller",
            "is_active",
            "created",
            "modified",
        ]
        read_only_fields = fields


class SellerUnfollowResponseSerializer(serializers.Serializer):
    seller_id = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


class MarketFeedAssetSerializer(serializers.ModelSerializer):
    media = AssetMediaSerializer(many=True, read_only=True)
    seller_nickname = serializers.CharField(
        source="owner.sellerprofile.nickname",
        read_only=True,
    )

    class Meta:
        model = Asset
        fields = [
            "id",
            "title",
            "description",
            "category",
            "price",
            "status",
            "is_market_listed",
            "is_for_auction",
            "count",
            "seller_nickname",
            "media",
            "created",
            "modified",
        ]
        read_only_fields = fields


class AuctionFeedResultSerializer(serializers.Serializer):
    source = serializers.ChoiceField(
        choices=[
            ("following", "following"),
            ("discovery", "discovery"),
        ],
        read_only=True,
    )
    item = serializers.SerializerMethodField()

    def get_item(self, obj):
        return AuctionListSerializer(
            obj["item"],
            context=self.context,
        ).data


class MarketFeedResultSerializer(serializers.Serializer):
    source = serializers.ChoiceField(
        choices=[
            ("following", "following"),
            ("discovery", "discovery"),
        ],
        read_only=True,
    )
    item = serializers.SerializerMethodField()

    def get_item(self, obj):
        return MarketFeedAssetSerializer(
            obj["item"],
            context=self.context,
        ).data


class FeedQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(required=False, min_value=1)
    cursor = serializers.CharField(required=False, allow_blank=False)
