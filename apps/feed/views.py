from drf_spectacular.utils import extend_schema, extend_schema_view
from django.db.models import Count, Q
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.feed.models import SellerCustomerBlock, SellerFollow
from apps.feed.serializers import (
    AuctionFeedResultSerializer,
    BlockUnblockResponseSerializer,
    CreateBlockSerializer,
    FeedQuerySerializer,
    MarketFeedResultSerializer,
    SellerCustomerBlockSerializer,
    SellerDiscoverySerializer,
    SellerFollowSerializer,
    SellerFollowSellerSerializer,
    SellerSuggestionQuerySerializer,
    SellerSuggestionSerializer,
    SellerUnfollowResponseSerializer,
)
from apps.feed.services import BlockService, FeedService, SellerFollowService
from apps.general.authorizations import HasAppAccess
from apps.general.permissions import IsNotManagerProfile
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile
from apps.orders.models import OrderItemStatus


def active_profile(request):
    return request.user.active_profile


@extend_schema_view(
    list=extend_schema(
        tags=["Feed"],
        responses={200: SellerCustomerBlockSerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Feed"],
        request=CreateBlockSerializer,
        responses={200: SellerCustomerBlockSerializer},
    ),
    destroy=extend_schema(
        tags=["Feed"],
        responses={200: BlockUnblockResponseSerializer},
    ),
)
class SellerCustomerBlockViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """List, create, and deactivate customer/seller blocks."""

    permission_classes = [HasAppAccess]
    permission_resource = "FEED_BLOCK"
    queryset = SellerCustomerBlock.objects.all()
    http_method_names = ["get", "post", "delete"]

    def get_serializer_class(self):
        if self.action == "create":
            return CreateBlockSerializer
        if self.action == "destroy":
            return BlockUnblockResponseSerializer
        return SellerCustomerBlockSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["active_profile"] = active_profile(self.request)
        return context

    def list(self, request, *args, **kwargs):
        blocks = BlockService.blocks_for_profile(active_profile(request))
        return Response(
            SellerCustomerBlockSerializer(
                blocks,
                many=True,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        block = BlockService.block_customer_seller(
            active_profile(request),
            serializer.validated_data["profile_id"],
            reason=serializer.validated_data.get("reason"),
        )
        return Response(
            SellerCustomerBlockSerializer(
                block,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        block = BlockService.unblock_customer_seller(
            active_profile(request),
            kwargs["profile_id"],
        )
        return Response(
            {
                "profile_id": kwargs["profile_id"],
                "is_active": block.is_active,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Feed"],
        request=None,
        responses={200: SellerFollowSerializer},
    ),
)
class SellerFollowCreateViewSet(viewsets.GenericViewSet):
    """Create or reactivate a customer follow for one seller."""

    permission_classes = [IsAuthenticated, IsNotManagerProfile]
    queryset = SellerFollow.objects.all()
    serializer_class = SellerFollowSerializer

    def create(self, request, *args, **kwargs):
        follow = SellerFollowService.follow(
            active_profile(request),
            kwargs["seller_id"],
        )
        return Response(
            SellerFollowSerializer(follow).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Feed"],
        request=None,
        responses={200: SellerUnfollowResponseSerializer},
    ),
)
class SellerUnfollowCreateViewSet(viewsets.GenericViewSet):
    """Deactivate a customer follow for one seller without deleting it."""

    permission_classes = [IsAuthenticated, IsNotManagerProfile]
    queryset = SellerFollow.objects.all()
    serializer_class = SellerUnfollowResponseSerializer

    def create(self, request, *args, **kwargs):
        seller = SellerFollowService.unfollow(
            active_profile(request),
            kwargs["seller_id"],
        )
        return Response(
            {
                "seller_id": seller.pk,
                "is_active": False,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Feed"],
        responses={200: SellerFollowSellerSerializer(many=True)},
    ),
)
class SellerFollowingViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """List sellers followed by the active customer profile."""

    permission_classes = [IsAuthenticated, IsNotManagerProfile]
    queryset = SellerFollow.objects.all()
    serializer_class = SellerFollowSellerSerializer

    def list(self, request, *args, **kwargs):
        follows = SellerFollowService.following_queryset(active_profile(request))
        sellers = [follow.seller for follow in follows]
        return Response(
            SellerFollowSellerSerializer(sellers, many=True).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Feed"],
        responses={200: SellerDiscoverySerializer(many=True)},
    ),
)
class SellerDiscoveryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Paginated discovery list of active seller profiles."""

    permission_classes = [IsAuthenticated]
    queryset = SellerProfile.objects.all()
    serializer_class = SellerDiscoverySerializer

    def get_queryset(self):
        profile = active_profile(self.request)
        queryset = (
            SellerProfile.objects.filter(
                is_active=True,
                activated=True,
                is_valid=True,
                deleted__isnull=True,
            )
            .select_related("user")
            .annotate(
                completed_orders_count=Count(
                    "sold_order_items",
                    filter=Q(
                        sold_order_items__status=OrderItemStatus.COMPLETED,
                        sold_order_items__deleted__isnull=True,
                    ),
                )
            )
        )

        if profile.type == ProfileType.SELLER:
            queryset = queryset.exclude(pk=profile.pk)
        elif profile.type == ProfileType.CUSTOMER:
            queryset = queryset.exclude(user_id=profile.user_id)
            blocked_seller_ids = BlockService.get_blocked_seller_ids_for_customer(
                profile
            )
            if blocked_seller_ids:
                queryset = queryset.exclude(pk__in=blocked_seller_ids)

        return queryset.order_by(
            "-completed_orders_count",
            "-avg_rating",
            "-ratings_count",
            "id",
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Feed"],
        parameters=[SellerSuggestionQuerySerializer],
        responses={200: SellerSuggestionSerializer(many=True)},
    ),
)
class PeopleYouMayKnowViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Return random active seller suggestions for the active customer."""

    permission_classes = [IsAuthenticated, IsNotManagerProfile]
    queryset = SellerFollow.objects.none()
    serializer_class = SellerSuggestionSerializer

    def list(self, request, *args, **kwargs):
        query_serializer = SellerSuggestionQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        limit = FeedService.parse_suggestion_limit(
            query_serializer.validated_data.get("limit")
        )
        sellers = FeedService.get_people_you_may_know(
            active_profile(request),
            limit,
        )
        return Response(
            SellerSuggestionSerializer(sellers, many=True).data,
            status=status.HTTP_200_OK,
        )


class BaseFeedViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsNotManagerProfile]

    def _feed_response(self, request, service_method, result_serializer_class):
        query_serializer = FeedQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        limit = FeedService.parse_limit(query_serializer.validated_data.get("limit"))
        cursor = query_serializer.validated_data.get("cursor")
        feed = service_method(
            active_profile(request),
            limit,
            cursor=cursor,
        )
        return Response(
            {
                "results": result_serializer_class(
                    feed["results"],
                    many=True,
                    context={"request": request},
                ).data,
                "next_cursor": feed["next_cursor"],
                "has_more": feed["has_more"],
                "meta": feed["meta"],
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Feed"],
        parameters=[FeedQuerySerializer],
        responses={200: AuctionFeedResultSerializer(many=True)},
    ),
)
class AuctionFeedViewSet(BaseFeedViewSet):
    """Cursor feed for active auctions, following first then discovery."""

    queryset = SellerFollow.objects.none()

    def list(self, request, *args, **kwargs):
        return self._feed_response(
            request,
            FeedService.get_auction_feed,
            AuctionFeedResultSerializer,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Feed"],
        parameters=[FeedQuerySerializer],
        responses={200: MarketFeedResultSerializer(many=True)},
    ),
)
class MarketFeedViewSet(BaseFeedViewSet):
    """Cursor feed for market-listed products, following first then discovery."""

    queryset = SellerFollow.objects.none()

    def list(self, request, *args, **kwargs):
        return self._feed_response(
            request,
            FeedService.get_market_feed,
            MarketFeedResultSerializer,
        )
