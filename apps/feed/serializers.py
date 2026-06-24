from rest_framework import serializers

from apps.assets.models import Asset
from apps.auctions.serializers import AuctionListSerializer
from apps.general.serializers import AssetMediaSerializer
from apps.feed.models import SellerCustomerBlock, SellerFollow, SellerPackageFollow
from apps.loyalty.services import SellerLevelService
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
    avatar_url = serializers.SerializerMethodField()
    is_followed = serializers.BooleanField(read_only=True, default=True)
    completed_orders_count = serializers.IntegerField(read_only=True)
    seller_level = serializers.SerializerMethodField()
    seller_level_label = serializers.SerializerMethodField()

    def to_representation(self, instance):
        data = {
            "id": instance.pk,
            "nickname": instance.nickname,
            "country": instance.country,
            "currency": instance.currency,
            "avatar_url": self.get_avatar_url(instance),
            "is_followed": True,
            "completed_orders_count": _seller_completed_orders_count(instance),
            "seller_level": self.get_seller_level(instance),
            "seller_level_label": self.get_seller_level_label(instance),
        }
        return data

    def get_avatar_url(self, obj):
        return _seller_avatar_url(obj)

    def get_seller_level(self, obj):
        return SellerLevelService.get_level(_seller_completed_orders_count(obj)).code

    def get_seller_level_label(self, obj):
        return SellerLevelService.get_level(_seller_completed_orders_count(obj)).label


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
    avatar_url = serializers.SerializerMethodField()
    is_followed = serializers.SerializerMethodField()
    completed_orders_count = serializers.IntegerField(read_only=True)
    seller_level = serializers.SerializerMethodField()
    seller_level_label = serializers.SerializerMethodField()

    def get_is_followed(self, obj):
        return False

    def get_avatar_url(self, obj):
        return _seller_avatar_url(obj)

    def get_seller_level(self, obj):
        return SellerLevelService.get_level(_seller_completed_orders_count(obj)).code

    def get_seller_level_label(self, obj):
        return SellerLevelService.get_level(_seller_completed_orders_count(obj)).label


class SellerDiscoverySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    nickname = serializers.CharField(read_only=True, allow_blank=True)
    description = serializers.SerializerMethodField()
    country = serializers.CharField(read_only=True)
    avg_rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=2,
        read_only=True,
    )
    ratings_count = serializers.IntegerField(read_only=True)
    avatar_url = serializers.SerializerMethodField()
    completed_orders_count = serializers.IntegerField(read_only=True)
    seller_level = serializers.SerializerMethodField()
    seller_level_label = serializers.SerializerMethodField()

    def get_description(self, obj):
        return getattr(obj, "description", "") or ""

    def get_avatar_url(self, obj):
        return _seller_avatar_url(obj)

    def get_seller_level(self, obj):
        return SellerLevelService.get_level(_seller_completed_orders_count(obj)).code

    def get_seller_level_label(self, obj):
        return SellerLevelService.get_level(_seller_completed_orders_count(obj)).label


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


class SellerPackageSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    owner_id = serializers.IntegerField(read_only=True)
    owner_nickname = serializers.CharField(
        source="owner.nickname",
        read_only=True,
        allow_blank=True,
    )
    display_name = serializers.CharField(read_only=True, allow_blank=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    remaining_points = serializers.IntegerField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    is_public = serializers.BooleanField(read_only=True)
    status = serializers.CharField(read_only=True)


class SellerPackageFollowSerializer(serializers.ModelSerializer):
    seller_package = SellerPackageSummarySerializer(read_only=True)

    class Meta:
        model = SellerPackageFollow
        fields = [
            "id",
            "seller_package",
            "is_active",
            "created",
            "modified",
        ]
        read_only_fields = fields


class SellerPackageUnfollowResponseSerializer(serializers.Serializer):
    seller_package_id = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


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
            ("package_following", "package_following"),
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
            ("package_following", "package_following"),
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


def _seller_avatar_url(obj):
    for field_name in ("avatar_url", "profile_photo_url", "photo_url", "image_url"):
        value = getattr(obj, field_name, "")
        if value:
            return value
    return ""


def _seller_completed_orders_count(obj):
    return int(getattr(obj, "completed_orders_count", 0) or 0)
