from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.social.models import SellerFollow
from apps.social.serializers import (
    AuctionFeedResultSerializer,
    FeedQuerySerializer,
    MarketFeedResultSerializer,
    SellerFollowSerializer,
    SellerFollowSellerSerializer,
    SellerUnfollowResponseSerializer,
)
from apps.social.services import FeedService, SellerFollowService


def active_profile(request):
    return request.user.active_profile


@extend_schema_view(
    create=extend_schema(
        tags=["Social"],
        request=None,
        responses={200: SellerFollowSerializer},
    ),
)
class SellerFollowCreateViewSet(viewsets.GenericViewSet):
    """Create or reactivate a customer follow for one seller."""

    permission_classes = [IsAuthenticated]
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
        tags=["Social"],
        request=None,
        responses={200: SellerUnfollowResponseSerializer},
    ),
)
class SellerUnfollowCreateViewSet(viewsets.GenericViewSet):
    """Deactivate a customer follow for one seller without deleting it."""

    permission_classes = [IsAuthenticated]
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
        tags=["Social"],
        responses={200: SellerFollowSellerSerializer(many=True)},
    ),
)
class SellerFollowingViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """List sellers followed by the active customer profile."""

    permission_classes = [IsAuthenticated]
    queryset = SellerFollow.objects.all()
    serializer_class = SellerFollowSellerSerializer

    def list(self, request, *args, **kwargs):
        follows = SellerFollowService.following_queryset(
            active_profile(request)
        )
        sellers = [follow.seller for follow in follows]
        return Response(
            SellerFollowSellerSerializer(sellers, many=True).data,
            status=status.HTTP_200_OK,
        )


class BaseFeedViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]

    def _feed_response(self, request, service_method, result_serializer_class):
        query_serializer = FeedQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        limit = FeedService.parse_limit(
            query_serializer.validated_data.get("limit")
        )
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
