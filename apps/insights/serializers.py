from rest_framework import serializers


class AuctionSummarySerializer(serializers.Serializer):
    active_count = serializers.IntegerField(read_only=True)
    closed_count = serializers.IntegerField(read_only=True)
    ending_soon_count = serializers.IntegerField(read_only=True)
    total_bids_received = serializers.IntegerField(read_only=True)
    unique_bidders_count = serializers.IntegerField(read_only=True)
    pending_payment_requests_count = serializers.IntegerField(read_only=True)
    sold_items_count = serializers.IntegerField(read_only=True)


class MarketSummarySerializer(serializers.Serializer):
    active_listings_count = serializers.IntegerField(read_only=True)
    sold_items_count = serializers.IntegerField(read_only=True)
    pending_order_items_count = serializers.IntegerField(read_only=True)
    pending_payment_requests_count = serializers.IntegerField(read_only=True)
    low_stock_products_count = serializers.IntegerField(read_only=True)


class EngagementSummarySerializer(serializers.Serializer):
    followers_count = serializers.IntegerField(read_only=True)
    new_followers_count_7d = serializers.IntegerField(read_only=True)
    unread_messages_count = serializers.IntegerField(read_only=True)
    active_conversations_count = serializers.IntegerField(read_only=True)
    profile_views_7d = serializers.IntegerField(read_only=True)
    auction_views_7d = serializers.IntegerField(read_only=True)
    market_item_views_7d = serializers.IntegerField(read_only=True)


class TrustSummarySerializer(serializers.Serializer):
    average_rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=2,
        allow_null=True,
        read_only=True,
    )
    ratings_count = serializers.IntegerField(read_only=True)
    recent_reviews_count_7d = serializers.IntegerField(read_only=True)
    badges_count = serializers.IntegerField(read_only=True)


class ActivePackageSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    remaining_points = serializers.IntegerField(read_only=True)


class PackageSummarySerializer(serializers.Serializer):
    active_package = ActivePackageSummarySerializer(
        allow_null=True,
        read_only=True,
    )
    remaining_auction_quota = serializers.IntegerField(
        allow_null=True,
        read_only=True,
    )
    remaining_market_quota = serializers.IntegerField(
        allow_null=True,
        read_only=True,
    )
    promoted_auctions_count = serializers.IntegerField(read_only=True)
    promoted_market_items_count = serializers.IntegerField(read_only=True)
    package_expiry_date = serializers.DateTimeField(
        allow_null=True,
        read_only=True,
    )


class HomeNotificationSerializer(serializers.Serializer):
    kind = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True)
    created_at = serializers.CharField(read_only=True)
    time_hint = serializers.CharField(read_only=True)
    is_new = serializers.BooleanField(read_only=True)


class SellerInsightsHomeSerializer(serializers.Serializer):
    auction_summary = AuctionSummarySerializer(read_only=True)
    market_summary = MarketSummarySerializer(read_only=True)
    engagement_summary = EngagementSummarySerializer(read_only=True)
    trust_summary = TrustSummarySerializer(read_only=True)
    package_summary = PackageSummarySerializer(read_only=True)
    notifications = HomeNotificationSerializer(many=True, read_only=True)
    notifications_new_count = serializers.IntegerField(read_only=True)
