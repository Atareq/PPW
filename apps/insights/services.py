import logging
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.assets.models import Asset, AssetCategory, AssetStatus
from apps.auctions.models import (
    Auction,
    AuctionItem,
    AuctionItemStatus,
    AuctionStatus,
    Bid,
)
from apps.chat.models import Conversation, Message
from apps.feed.models import SellerFollow
from apps.insights.models import (
    SellerInsightEvent,
    SellerInsightEventType,
    SellerInsightObjectType,
)
from apps.loyalty.models import BadgeAward
from apps.orders.models import OrderItem, OrderItemStatus
from apps.packages.models import AssetSellerPackage, SellerPackageStatus
from apps.payments.models import (
    PaymentRequest,
    PaymentRequestStatus,
    PaymentRequestType,
)
from apps.ratings.models import SellerRating
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile

logger = logging.getLogger(__name__)

RECENT_WINDOW_DAYS = 7
ENDING_SOON_WINDOW_HOURS = 24
LOW_STOCK_THRESHOLD = 3
PRODUCT_MARKET_CATEGORIES = [
    AssetCategory.SUPPLY,
    AssetCategory.ACCESSORY,
    AssetCategory.FEED,
    AssetCategory.SUPPLEMENT,
]


class SellerInsightEventService:
    @staticmethod
    def record_event(
        *,
        seller,
        event_type,
        object_type=None,
        object_id=None,
        actor_profile=None,
    ):
        try:
            seller_profile = SellerInsightEventService._resolve_seller(seller)
            if object_type is None:
                object_type = SellerInsightEventService._default_object_type(event_type)
            if event_type not in SellerInsightEventType.values:
                raise ValueError(_("Invalid seller insight event type."))
            if object_type not in SellerInsightObjectType.values:
                raise ValueError(_("Invalid seller insight object type."))

            return SellerInsightEvent.objects.create(
                seller=seller_profile,
                actor_profile=actor_profile,
                event_type=event_type,
                object_type=object_type,
                object_id=object_id,
            )
        except Exception:
            logger.exception("Failed to record seller insight event.")
            return None

    @staticmethod
    def _resolve_seller(seller):
        if isinstance(seller, SellerProfile):
            return seller
        return SellerProfile.objects.get(pk=seller.pk)

    @staticmethod
    def _default_object_type(event_type):
        return {
            SellerInsightEventType.SELLER_PROFILE_VIEW: (
                SellerInsightObjectType.SELLER_PROFILE
            ),
            SellerInsightEventType.AUCTION_VIEW: SellerInsightObjectType.AUCTION,
            SellerInsightEventType.MARKET_ITEM_VIEW: (
                SellerInsightObjectType.MARKET_ITEM
            ),
        }[event_type]


class SellerInsightsService:
    @staticmethod
    def get_home_summary(seller_profile):
        seller = SellerInsightsService._resolve_active_seller(seller_profile)
        now = timezone.now()
        recent_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)

        return {
            "auction_summary": SellerInsightsService._auction_summary(
                seller,
                now,
            ),
            "market_summary": SellerInsightsService._market_summary(seller),
            "engagement_summary": SellerInsightsService._engagement_summary(
                seller,
                recent_cutoff,
            ),
            "trust_summary": SellerInsightsService._trust_summary(
                seller,
                recent_cutoff,
            ),
            "package_summary": SellerInsightsService._package_summary(seller),
        }

    @staticmethod
    def _resolve_active_seller(profile):
        if profile.type != ProfileType.SELLER:
            raise PermissionError("Active profile must be a seller.")
        return SellerProfile.objects.get(pk=profile.pk)

    @staticmethod
    def _auction_summary(seller, now):
        seller_auctions = Auction.objects.filter(
            owner_id=seller.pk,
            deleted__isnull=True,
        )
        active_auctions = seller_auctions.filter(
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time__lte=now,
            end_time__gt=now,
        )
        seller_bids = Bid.objects.filter(
            item__auction__owner_id=seller.pk,
            item__auction__deleted__isnull=True,
            item__deleted__isnull=True,
            deleted__isnull=True,
        )

        return {
            "active_count": active_auctions.count(),
            "closed_count": seller_auctions.filter(
                status__in=[AuctionStatus.ENDED, AuctionStatus.CLOSED],
            ).count(),
            "ending_soon_count": active_auctions.filter(
                end_time__lte=now + timedelta(hours=ENDING_SOON_WINDOW_HOURS),
            ).count(),
            "total_bids_received": seller_bids.count(),
            "unique_bidders_count": seller_bids.values("bidder_id").distinct().count(),
            "pending_payment_requests_count": PaymentRequest.objects.filter(
                seller_id=seller.pk,
                request_type=PaymentRequestType.AUCTION,
                status=PaymentRequestStatus.PENDING,
                deleted__isnull=True,
            ).count(),
            "sold_items_count": AuctionItem.objects.filter(
                auction__owner_id=seller.pk,
                auction__deleted__isnull=True,
                status=AuctionItemStatus.SOLD,
                deleted__isnull=True,
            ).count(),
        }

    @staticmethod
    def _market_summary(seller):
        seller_products = Asset.objects.filter(
            owner_id=seller.pk,
            category__in=PRODUCT_MARKET_CATEGORIES,
            deleted__isnull=True,
        )

        return {
            "active_listings_count": seller_products.filter(
                is_market_listed=True,
                status=AssetStatus.AVAILABLE,
                count__gt=0,
            ).count(),
            "sold_items_count": OrderItem.objects.filter(
                seller_id=seller.pk,
                status=OrderItemStatus.COMPLETED,
                deleted__isnull=True,
            ).count(),
            "pending_order_items_count": OrderItem.objects.filter(
                seller_id=seller.pk,
                status=OrderItemStatus.PENDING_SELLER,
                deleted__isnull=True,
            ).count(),
            "pending_payment_requests_count": PaymentRequest.objects.filter(
                seller_id=seller.pk,
                request_type=PaymentRequestType.MARKET,
                status=PaymentRequestStatus.PENDING,
                deleted__isnull=True,
            ).count(),
            "low_stock_products_count": seller_products.filter(
                is_market_listed=True,
                status=AssetStatus.AVAILABLE,
                count__gt=0,
                count__lte=LOW_STOCK_THRESHOLD,
            ).count(),
        }

    @staticmethod
    def _engagement_summary(seller, recent_cutoff):
        active_followers = SellerFollow.objects.filter(
            seller_id=seller.pk,
            is_active=True,
            follower__is_active=True,
            follower__activated=True,
            follower__is_valid=True,
            follower__deleted__isnull=True,
            deleted__isnull=True,
        )
        recent_events = SellerInsightEvent.objects.filter(
            seller_id=seller.pk,
            created__gte=recent_cutoff,
            deleted__isnull=True,
        )

        return {
            "followers_count": active_followers.count(),
            "new_followers_count_7d": active_followers.filter(
                created__gte=recent_cutoff,
            ).count(),
            "unread_messages_count": Message.objects.filter(
                conversation__seller_id=seller.pk,
                receiver_id=seller.pk,
                is_read=False,
                deleted__isnull=True,
            ).count(),
            "active_conversations_count": Conversation.objects.filter(
                seller_id=seller.pk,
                is_active=True,
                deleted__isnull=True,
            ).count(),
            "profile_views_7d": recent_events.filter(
                event_type=SellerInsightEventType.SELLER_PROFILE_VIEW,
            ).count(),
            "auction_views_7d": recent_events.filter(
                event_type=SellerInsightEventType.AUCTION_VIEW,
            ).count(),
            "market_item_views_7d": recent_events.filter(
                event_type=SellerInsightEventType.MARKET_ITEM_VIEW,
            ).count(),
        }

    @staticmethod
    def _trust_summary(seller, recent_cutoff):
        average_rating = None
        if seller.ratings_count:
            average_rating = seller.avg_rating

        return {
            "average_rating": average_rating,
            "ratings_count": seller.ratings_count,
            "recent_reviews_count_7d": SellerRating.objects.filter(
                seller_id=seller.pk,
                created__gte=recent_cutoff,
                deleted__isnull=True,
            ).count(),
            "badges_count": BadgeAward.objects.filter(
                profile_id=seller.pk,
                deleted__isnull=True,
            ).count(),
        }

    @staticmethod
    def _package_summary(seller):
        active_package = (
            AssetSellerPackage.objects.filter(
                owner_id=seller.pk,
                status=SellerPackageStatus.ACTIVE,
                activated=True,
                deleted__isnull=True,
            )
            .select_related("package")
            .order_by("-created")
            .first()
        )
        if active_package is None:
            return {
                "active_package": None,
                "remaining_auction_quota": None,
                "remaining_market_quota": None,
                "promoted_auctions_count": 0,
                "promoted_market_items_count": 0,
                "package_expiry_date": None,
            }

        return {
            "active_package": {
                "id": active_package.pk,
                "name": active_package.package.name,
                "remaining_points": active_package.remaining_points,
            },
            "remaining_auction_quota": (
                active_package.remaining_points // active_package.package.auction_cost
            ),
            "remaining_market_quota": (
                active_package.remaining_points // active_package.package.product_cost
            ),
            "promoted_auctions_count": 0,
            "promoted_market_items_count": 0,
            "package_expiry_date": active_package.expires_at,
        }
