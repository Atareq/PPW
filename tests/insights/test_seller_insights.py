from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.assets.models import AssetStatus
from apps.auctions.models import AuctionItemStatus, AuctionStatus
from apps.authentication.services import build_token_for_user
from apps.chat.models import Conversation, Message
from apps.feed.models import SellerFollow
from apps.insights.models import (
    SellerInsightEvent,
    SellerInsightEventType,
    SellerInsightObjectType,
)
from apps.insights.services import SellerInsightEventService
from apps.orders.models import OrderItemStatus
from apps.payments.models import (
    AuctionPaymentRequest,
    MarketPaymentRequest,
    PaymentRequestStatus,
)
from apps.ratings.models import SellerRating
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    ActiveAssetSellerPackageFactory,
    AuctionFactory,
    AuctionItemFactory,
    BadgeAwardFactory,
    BidFactory,
    CustomerProfileFactory,
    OrderItemFactory,
    PackageFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def concrete_seller(user):
    return SellerProfile.objects.get(pk=user.active_profile.pk)


def concrete_customer(user):
    return CustomerProfile.objects.get(pk=user.active_profile.pk)


def set_created(instance, created):
    instance.__class__.objects.filter(pk=instance.pk).update(created=created)
    instance.refresh_from_db()
    return instance


class TestSellerInsightsPermissions:
    def test_unauthenticated_request_returns_401(self):
        response = APIClient().get("/api/insights/seller/home/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_customer_profile_cannot_access_seller_insights(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)

        response = client.get("/api/insights/seller/home/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_seller_profile_can_access_own_insights(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(seller_user)

        response = client.get("/api/insights/seller/home/")

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == {
            "auction_summary",
            "market_summary",
            "engagement_summary",
            "trust_summary",
            "package_summary",
        }


class TestSellerInsightsSummary:
    def test_summary_returns_safe_defaults_without_data(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(seller_user)

        response = client.get("/api/insights/seller/home/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["auction_summary"] == {
            "active_count": 0,
            "closed_count": 0,
            "ending_soon_count": 0,
            "total_bids_received": 0,
            "unique_bidders_count": 0,
            "pending_payment_requests_count": 0,
            "sold_items_count": 0,
        }
        assert response.data["market_summary"] == {
            "active_listings_count": 0,
            "sold_items_count": 0,
            "pending_order_items_count": 0,
            "pending_payment_requests_count": 0,
            "low_stock_products_count": 0,
        }
        assert response.data["engagement_summary"] == {
            "followers_count": 0,
            "new_followers_count_7d": 0,
            "unread_messages_count": 0,
            "active_conversations_count": 0,
            "profile_views_7d": 0,
            "auction_views_7d": 0,
            "market_item_views_7d": 0,
        }
        assert response.data["trust_summary"] == {
            "average_rating": None,
            "ratings_count": 0,
            "recent_reviews_count_7d": 0,
            "badges_count": 0,
        }
        assert response.data["package_summary"] == {
            "active_package": None,
            "remaining_auction_quota": None,
            "remaining_market_quota": None,
            "promoted_auctions_count": 0,
            "promoted_market_items_count": 0,
            "package_expiry_date": None,
        }

    def test_summary_counts_existing_seller_data(self):
        now = timezone.now()
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = concrete_seller(seller_user)
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer = concrete_customer(customer_user)
        other_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_customer_profile = concrete_customer(other_customer)

        active_auction = AuctionFactory(
            owner=seller,
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
        )
        ending_soon_auction = AuctionFactory(
            owner=seller,
            status=AuctionStatus.ACTIVE,
            is_visible=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=6),
        )
        AuctionFactory(
            owner=seller,
            status=AuctionStatus.ENDED,
            is_visible=True,
            start_time=now - timedelta(days=3),
            end_time=now - timedelta(days=1),
        )
        active_item = AuctionItemFactory(auction=active_auction)
        sold_item = AuctionItemFactory(
            auction=ending_soon_auction,
            status=AuctionItemStatus.SOLD,
            winner=customer,
        )
        BidFactory(item=active_item, bidder=customer)
        BidFactory(item=active_item, bidder=other_customer_profile)
        AuctionPaymentRequest.objects.create(
            buyer=customer,
            seller=seller,
            asset=sold_item.bird,
            amount=Decimal("100.00"),
            status=PaymentRequestStatus.PENDING,
            auction_item=sold_item,
        )

        low_stock = SupplyFactory(
            owner=seller,
            is_market_listed=True,
            status=AssetStatus.AVAILABLE,
            count=2,
        )
        SupplyFactory(
            owner=seller,
            is_market_listed=True,
            status=AssetStatus.AVAILABLE,
            count=10,
        )
        OrderItemFactory(
            asset=low_stock,
            seller=seller,
            status=OrderItemStatus.PENDING_SELLER,
        )
        completed_order_item = OrderItemFactory(
            asset=low_stock,
            seller=seller,
            status=OrderItemStatus.COMPLETED,
        )
        MarketPaymentRequest.objects.create(
            buyer=customer,
            seller=seller,
            asset=low_stock,
            amount=Decimal("25.00"),
            status=PaymentRequestStatus.PENDING,
            order_item=completed_order_item,
        )

        recent_follower = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        old_follower = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        SellerFollow.objects.create(
            follower=concrete_customer(recent_follower),
            seller=seller,
        )
        old_follow = SellerFollow.objects.create(
            follower=concrete_customer(old_follower),
            seller=seller,
        )
        set_created(old_follow, now - timedelta(days=10))
        conversation = Conversation.objects.create(
            customer=customer,
            seller=seller,
            created_by=customer,
            is_active=True,
        )
        Message.objects.create(
            conversation=conversation,
            sender=customer,
            receiver=seller,
            body="Interested",
            is_read=False,
        )

        SellerInsightEventService.record_event(
            seller=seller,
            event_type=SellerInsightEventType.SELLER_PROFILE_VIEW,
            actor_profile=customer,
        )
        SellerInsightEventService.record_event(
            seller=seller,
            event_type=SellerInsightEventType.AUCTION_VIEW,
            object_id=active_auction.pk,
            actor_profile=customer,
        )
        SellerInsightEventService.record_event(
            seller=seller,
            event_type=SellerInsightEventType.MARKET_ITEM_VIEW,
            object_id=low_stock.pk,
            actor_profile=customer,
        )
        old_event = SellerInsightEventService.record_event(
            seller=seller,
            event_type=SellerInsightEventType.MARKET_ITEM_VIEW,
            object_id=low_stock.pk,
            actor_profile=customer,
        )
        set_created(old_event, now - timedelta(days=10))

        seller.avg_rating = Decimal("4.50")
        seller.ratings_count = 2
        seller.save(update_fields=["avg_rating", "ratings_count"])
        SellerRating.objects.create(
            seller=seller,
            owner=customer,
            stars=5,
        )
        BadgeAwardFactory(profile=seller)
        package = PackageFactory(
            points=100,
            auction_cost=10,
            product_cost=5,
        )
        active_package = ActiveAssetSellerPackageFactory(
            owner=seller,
            package=package,
            remaining_points=25,
            expires_at=now + timedelta(days=20),
        )
        client = authenticated_client(seller_user)

        response = client.get("/api/insights/seller/home/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["auction_summary"] == {
            "active_count": 2,
            "closed_count": 1,
            "ending_soon_count": 2,
            "total_bids_received": 2,
            "unique_bidders_count": 2,
            "pending_payment_requests_count": 1,
            "sold_items_count": 1,
        }
        assert response.data["market_summary"] == {
            "active_listings_count": 2,
            "sold_items_count": 1,
            "pending_order_items_count": 1,
            "pending_payment_requests_count": 1,
            "low_stock_products_count": 1,
        }
        assert response.data["engagement_summary"] == {
            "followers_count": 2,
            "new_followers_count_7d": 1,
            "unread_messages_count": 1,
            "active_conversations_count": 1,
            "profile_views_7d": 1,
            "auction_views_7d": 1,
            "market_item_views_7d": 1,
        }
        assert response.data["trust_summary"] == {
            "average_rating": "4.50",
            "ratings_count": 2,
            "recent_reviews_count_7d": 1,
            "badges_count": 1,
        }
        assert response.data["package_summary"]["active_package"] == {
            "id": active_package.pk,
            "name": package.name,
            "remaining_points": 25,
        }
        assert response.data["package_summary"]["remaining_auction_quota"] == 2
        assert response.data["package_summary"]["remaining_market_quota"] == 5
        assert response.data["package_summary"]["promoted_auctions_count"] == 0
        assert response.data["package_summary"]["promoted_market_items_count"] == 0
        assert response.data["package_summary"]["package_expiry_date"]
        assert "user" not in response.data
        assert "email" not in response.data
        assert "phone_number" not in response.data


class TestSellerInsightEventService:
    def test_can_record_seller_profile_view_with_nullable_actor(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_profile = concrete_seller(seller)

        event = SellerInsightEventService.record_event(
            seller=seller_profile,
            event_type=SellerInsightEventType.SELLER_PROFILE_VIEW,
        )

        assert event is not None
        assert event.seller == seller_profile
        assert event.actor_profile is None
        assert event.object_type == SellerInsightObjectType.SELLER_PROFILE

    def test_can_record_auction_and_market_item_views(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_profile = concrete_seller(seller)

        auction_event = SellerInsightEventService.record_event(
            seller=seller_profile,
            event_type=SellerInsightEventType.AUCTION_VIEW,
            object_id=123,
        )
        market_event = SellerInsightEventService.record_event(
            seller=seller_profile,
            event_type=SellerInsightEventType.MARKET_ITEM_VIEW,
            object_id=456,
        )

        assert auction_event.object_type == SellerInsightObjectType.AUCTION
        assert auction_event.object_id == 123
        assert market_event.object_type == SellerInsightObjectType.MARKET_ITEM
        assert market_event.object_id == 456

    def test_event_model_indexes_and_choices_are_available(self):
        index_names = {index.name for index in SellerInsightEvent._meta.indexes}

        assert SellerInsightEventType.SELLER_PROFILE_VIEW in (
            SellerInsightEventType.values
        )
        assert SellerInsightObjectType.MARKET_ITEM in (
            SellerInsightObjectType.values
        )
        assert "ins_seller_event_created_idx" in index_names
        assert "ins_object_lookup_idx" in index_names
