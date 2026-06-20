from rest_framework import serializers

from apps.assets.models import Asset
from apps.auctions.serializers import AuctionListSerializer
from apps.general.serializers import AssetMediaSerializer
from apps.feed.models import SellerCustomerBlock, SellerFollow
from apps.users.constants import ProfileType


class BlockProfileSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    type = serializers.CharField(read_only=True)
    nickname = serializers.CharField(read_only=True, allow_blank=True)
    country = serializers.CharField(read_only=True)
    currency = serializers.CharField(read_only=True)

    def to_representation(self, instance):
        profile = self._concrete_profile(instance)
        data = {
            "id": instance.pk,
            "type": instance.type,
        }
        nickname = getattr(profile, "nickname", "")
        if nickname:
            data["nickname"] = nickname
        country = getattr(profile, "country", "")
        if country:
            data["country"] = country
        currency = getattr(profile, "currency", "")
        if currency:
            data["currency"] = currency
        return data

    def _concrete_profile(self, instance):
        if instance.type == ProfileType.SELLER:
            return getattr(instance, "sellerprofile", instance)
        if instance.type == ProfileType.CUSTOMER:
            return getattr(instance, "customerprofile", instance)
        return instance


class SellerCustomerBlockSerializer(serializers.ModelSerializer):
    customer = BlockProfileSummarySerializer(read_only=True)
    seller = BlockProfileSummarySerializer(read_only=True)
    blocked_by = BlockProfileSummarySerializer(read_only=True)
    other_participant = serializers.SerializerMethodField()

    class Meta:
        model = SellerCustomerBlock
        fields = [
            "id",
            "customer",
            "seller",
            "blocked_by",
            "other_participant",
            "reason",
            "is_active",
            "created",
        ]
        read_only_fields = fields

    def get_other_participant(self, obj):
        active_profile = self.context.get("active_profile")
        if active_profile and active_profile.pk == obj.customer_id:
            return BlockProfileSummarySerializer(obj.seller).data
        return BlockProfileSummarySerializer(obj.customer).data


class CreateBlockSerializer(serializers.Serializer):
    profile_id = serializers.IntegerField()
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
    )


class BlockUnblockResponseSerializer(serializers.Serializer):
    profile_id = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


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
            "avatar_url": instance.photo or "",
            "is_followed": True,
        }
        return data


class SellerSuggestionSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    nickname = serializers.CharField(read_only=True, allow_blank=True)
    country = serializers.CharField(read_only=True)
    currency = serializers.CharField(read_only=True)
    avg_rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=2,
        read_only=True,
    )
    ratings_count = serializers.IntegerField(read_only=True)
    avatar_url = serializers.URLField(source="photo", read_only=True, allow_blank=True)
    is_followed = serializers.SerializerMethodField()

    def get_is_followed(self, obj):
        return False


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


class SellerSuggestionQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(required=False, min_value=1)


class SellerListSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    nickname = serializers.CharField(read_only=True, allow_blank=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    country = serializers.CharField(read_only=True)
    avg_rating = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    ratings_count = serializers.IntegerField(read_only=True)
    avatar_url = serializers.URLField(source="photo", read_only=True, allow_blank=True)
