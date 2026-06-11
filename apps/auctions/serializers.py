from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.assets.models import Bird
from apps.auctions.models import Auction, AuctionEvent, AuctionItem, Bid
from apps.auctions.services import AuctionService, SettlementService
from apps.general.serializers import AssetImageSerializer

# ============================================================================
# NESTED SERIALIZERS
# ============================================================================


class BirdBasicSerializer(serializers.ModelSerializer):
    """Minimal bird info for auction display"""

    images = AssetImageSerializer(many=True, read_only=True)

    class Meta:
        model = Bird
        fields = [
            "id",
            "name",
            "ring_number",
            "gender",
            "colour",
            "birthday",
            "images",
            "video_url",
        ]


class BidSerializer(serializers.ModelSerializer):
    """Bid display serializer"""

    is_winning_bid = serializers.BooleanField(read_only=True)
    bidder_username = serializers.CharField(
        source="bidder.username", read_only=True
    )

    class Meta:
        model = Bid
        fields = [
            "id",
            "amount",
            "bidder",
            "bidder_username",
            "created",
            "is_winning_bid",
        ]
        read_only_fields = [
            "id",
            "created",
            "is_winning_bid",
        ]


class AuctionItemDetailSerializer(serializers.ModelSerializer):
    """Detailed auction item with bid history"""

    bird = BirdBasicSerializer(read_only=True)
    paired_bird = BirdBasicSerializer(read_only=True)
    bids = BidSerializer(many=True, read_only=True)
    current_highest_bidder = serializers.CharField(read_only=True)
    winner_username = serializers.CharField(
        source="winner.username", read_only=True, allow_null=True
    )
    is_active = serializers.SerializerMethodField()
    auction_title = serializers.CharField(
        source="auction.title", read_only=True
    )
    auction_id = serializers.IntegerField(source="auction.id", read_only=True)

    class Meta:
        model = AuctionItem
        fields = [
            "id",
            "bird",
            "paired_bird",
            "status",
            "starting_price",
            "current_price",
            "winner",
            "winner_username",
            "bids",
            "current_highest_bidder",
            "is_active",
            "created",
            "modified",
            "auction_id",
            "auction_title",
        ]
        read_only_fields = [
            "id",
            "current_price",
            "winner",
            "status",
            "bids",
            "created",
            "modified",
        ]

    def get_is_active(self, obj) -> bool:
        return obj.is_active()


class AuctionItemListSerializer(serializers.ModelSerializer):
    """Minimal item info for listing"""

    bird_name = serializers.CharField(source="bird.name", read_only=True)
    bird_gender = serializers.CharField(source="bird.gender", read_only=True)
    current_highest_bidder = serializers.CharField(read_only=True)
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = AuctionItem
        fields = [
            "id",
            "bird_name",
            "bird_gender",
            "status",
            "starting_price",
            "current_price",
            "current_highest_bidder",
            "is_active",
        ]
        read_only_fields = [
            "id",
            "status",
            "current_price",
            "is_active",
        ]

    def get_is_active(self, obj) -> bool:
        return obj.is_active()


class AuctionEventSerializer(serializers.ModelSerializer):
    """Audit trail of auction events"""

    user_phone = serializers.CharField(
        source="performer.user.phone_number", read_only=True, allow_null=True
    )
    event_type_display = serializers.CharField(
        source="get_event_type_display", read_only=True
    )

    class Meta:
        model = AuctionEvent
        fields = "__all__"
        read_only_fields = [
            "id",
            "event_type",
            "event_type_display",
            "created",
        ]


# ============================================================================
# MAIN AUCTION SERIALIZERS
# ============================================================================


class AuctionDetailSerializer(serializers.ModelSerializer):
    """Detailed auction view with all info"""

    seller_nickname = serializers.CharField(
        source="owner.sellerprofile.nickname", read_only=True
    )
    items = AuctionItemDetailSerializer(many=True, read_only=True)
    events = AuctionEventSerializer(many=True, read_only=True)

    time_remaining = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()
    is_ended = serializers.SerializerMethodField()

    auction_type_display = serializers.CharField(
        source="get_auction_type_display", read_only=True
    )

    class Meta:
        model = Auction
        fields = [
            "id",
            "title",
            "description",
            "thumbnail_url",
            "auction_type",
            "auction_type_display",
            "seller_nickname",
            "owner",
            "start_time",
            "end_time",
            "auto_extend_enabled",
            "auto_extend_minutes",
            "buy_now_enabled",
            "buy_now_price",
            "deposit_required",
            "payment_deadline_days",
            "min_bid_increment",
            "is_visible",
            "tags",
            "status",
            "time_remaining",
            "is_active",
            "is_ended",
            "created",
            "modified",
            "items",
            "events",
        ]
        read_only_fields = [
            "id",
            "status",
            "is_visible",
            "items",
            "events",
            "owner",
            "created",
            "modified",
            "status ",
        ]

    def get_time_remaining(self, obj) -> int | None:
        remaining = obj.time_remaining()
        if remaining:
            return int(remaining.total_seconds())
        return None

    def get_is_active(self, obj) -> bool:
        return obj.is_active()

    def get_is_ended(self, obj) -> bool:
        return obj.is_ended()


class AuctionListSerializer(serializers.ModelSerializer):
    """Minimal auction info for listing"""

    seller_nickname = serializers.CharField(
        source="owner.sellerprofile.nickname", read_only=True
    )
    item_count = serializers.SerializerMethodField()
    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )
    auction_type_display = serializers.CharField(
        source="get_auction_type_display", read_only=True
    )
    time_remaining = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Auction
        fields = [
            "id",
            "title",
            "description",
            "auction_type",
            "auction_type_display",
            "status_display",
            "seller_nickname",
            "start_time",
            "end_time",
            "item_count",
            "is_visible",
            "time_remaining",
            "created",
            "status",
            "thumbnail_url",
        ]
        read_only_fields = [
            "id",
            "is_visible",
            "created",
        ]

    def get_item_count(self, obj) -> int:
        return obj.items.count()

    def get_time_remaining(self, obj) -> int | None:
        remaining = obj.time_remaining()
        if remaining:
            return int(remaining.total_seconds())
        return None

    def get_thumbnail_url(self, obj) -> str | None:
        # Prefer the auction's own uploaded cover image
        if obj.thumbnail_url:
            return obj.thumbnail_url
        # Fall back to the first item's bird image
        item = obj.items.first()
        if item and item.bird:
            img = (
                item.bird.images.filter(is_primary=True).first()
                or item.bird.images.first()
            )
            return img.image_url if img else None
        return None


# ============================================================================
# CREATION & ACTION SERIALIZERS
# ============================================================================
# ============================================================================
# AUCTION ITEM INPUT (NESTED IN CREATE REQUEST)
# ============================================================================


class AuctionItemInputSerializer(serializers.Serializer):
    """
    Validates a single item within an auction creation request.

    Replaces parallel arrays with structured per-item data:
    {
        "bird_id": 1,
        "paired_bird_id": 2,  # optional, required for PAIR auctions
        "starting_price": "100.00",
    }
    """

    bird_id = serializers.IntegerField()
    paired_bird_id = serializers.IntegerField(required=False, allow_null=True)
    starting_price = serializers.DecimalField(max_digits=10, decimal_places=2)

    description = serializers.CharField(required=False, allow_blank=True)


# ============================================================================
# AUCTION CREATION SERIALIZER
# ============================================================================


class AuctionCreateSerializer(serializers.ModelSerializer):
    """
    Production-grade auction creation serializer.

    Key design:
    - Nested items array (replaces parallel arrays)
    - Explicit fields only (no **kwargs)
    - Lifecycle fields NOT exposed (status, is_visible)
    - Full validation before service call
    - Clear error messages

    Example request:
    {
        "title": "Auction Title",
        "description": "...",
        "auction_type": "single",
        "start_time": "2026-04-06T10:00:00Z",
        "end_time": "2026-04-07T10:00:00Z",
        "auto_extend_enabled": true,
        "auto_extend_minutes": 5,
        "buy_now_enabled": true,
        "deposit_required": false,
        "payment_deadline_days": null,
        "min_bid_increment": "1.00",
        "tags": "pigeon,racing",
        "items": [
            {
                "bird_id": 1,
                "starting_price": "100.00",
                "buy_now_price": "150.00",

            }
        ]
    }
    """

    items = AuctionItemInputSerializer(many=True, allow_empty=False)

    class Meta:
        model = Auction
        fields = [
            "title",
            "description",
            "thumbnail_url",
            "auction_type",
            "start_time",
            "end_time",
            "auto_extend_enabled",
            "auto_extend_minutes",
            "buy_now_enabled",
            "deposit_required",
            "payment_deadline_days",
            "min_bid_increment",
            "tags",
            "items",
            "buy_now_price",
        ]

    def validate(self, data):
        start_time = data["start_time"]
        end_time = data["end_time"]
        now = timezone.now()

        # ===== TIME VALIDATION =====
        if start_time >= end_time:
            raise serializers.ValidationError(
                {"start_time": "Start time must be before end time"}
            )

        if start_time <= now:
            raise serializers.ValidationError(
                {"start_time": "Start time must be in the future"}
            )

        # ===== AUTO-EXTEND VALIDATION =====
        if data.get("auto_extend_enabled"):
            if not data.get("auto_extend_minutes"):
                raise serializers.ValidationError(
                    {
                        "auto_extend_minutes": "Required when auto_extend_enabled=True"
                    }
                )

        # ===== DEPOSIT VALIDATION =====
        if data.get("deposit_required"):
            if not data.get("payment_deadline_days"):
                raise serializers.ValidationError(
                    {
                        "payment_deadline_days": "Required when deposit_required=True"
                    }
                )

        if data.get("buy_now_enabled"):
            if not data.get("buy_now_price"):
                raise serializers.ValidationError(
                    {"buy_now_price": "Required when buy_now_enabled=True"}
                )

        return data


class EndingSoonQuerySerializer(serializers.Serializer):
    minutes = serializers.IntegerField(
        min_value=1, max_value=99999999, required=False, default=60
    )


class AuctionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Auction
        fields = [
            "title",
            "description",
            "tags",
        ]

    def update(self, instance, validated_data):
        """Update only allowed fields through the service layer."""
        return AuctionService.update_auction(instance, validated_data)


class PlaceBidSerializer(serializers.Serializer):

    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01")
    )


class BuyNowSerializer(serializers.Serializer):
    """Serializer for buy now action"""

    def create(self, validated_data):
        """Trigger buy now"""

        request = self.context.get("request")
        item = self.context.get("item")

        if not request or not item:
            raise serializers.ValidationError("Invalid request context")

        buyer = request.user.active_profile

        try:
            bid = SettlementService.trigger_buy_now(item, buyer)
            return bid
        except serializers.ValidationError:
            raise


class CancelAuctionSerializer(serializers.Serializer):
    """Serializer for cancelling auction"""

    reason = serializers.CharField(required=False, allow_blank=True)

    def create(self, validated_data):
        """Cancel auction"""

        request = self.context.get("request")
        auction = self.context.get("auction")

        if not request or not auction:
            raise serializers.ValidationError("Invalid request context")

        try:
            auction = AuctionService.cancel_auction(auction)
            return auction
        except serializers.ValidationError:
            raise
