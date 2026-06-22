from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.auctions.models import Auction, AuctionEvent, AuctionItem, Bid
from apps.auctions.serializers import (
    AuctionCreateSerializer,
    AuctionDetailSerializer,
    AuctionEventSerializer,
    AuctionItemDetailSerializer,
    AuctionItemListSerializer,
    AuctionListSerializer,
    AuctionUpdateSerializer,
    BidSerializer,
    BuyNowSerializer,
    CancelAuctionSerializer,
    EndingSoonQuerySerializer,
    PlaceBidSerializer,
)
from apps.auctions.services import (
    AuctionBusinessValidator,
    AuctionService,
    BidBusinessValidator,
    BiddingService,
)
from apps.general.authorizations import HasAppAccess
from apps.general.permissions import IsNotManagerProfile
from apps.insights.models import SellerInsightEventType
from apps.insights.services import SellerInsightEventService
from apps.users.constants import ProfileType

# ============================================================================
# VIEWSETS
# ============================================================================


@extend_schema_view(
    create=extend_schema(
        tags=["Auctions"],
        request=AuctionCreateSerializer,
        responses={201: AuctionDetailSerializer},
    ),
    partial_update=extend_schema(
        tags=["Auctions"],
        request=AuctionUpdateSerializer,
        responses={200: AuctionDetailSerializer},
    ),
    my_auctions=extend_schema(
        tags=["Auctions"],
        parameters=[
            OpenApiParameter(
                name="status",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter auctions by status.",
            )
        ],
        responses={200: AuctionListSerializer(many=True)},
    ),
    active=extend_schema(
        tags=["Auctions"],
        responses={200: AuctionListSerializer(many=True)},
    ),
    ending_soon=extend_schema(
        tags=["Auctions"],
        parameters=[EndingSoonQuerySerializer],
        responses={200: AuctionDetailSerializer(many=True)},
    ),
    cancel=extend_schema(
        tags=["Auctions"],
        request=CancelAuctionSerializer,
        responses={200: AuctionDetailSerializer},
    ),
    close=extend_schema(
        tags=["Auctions"],
        request=None,
        responses={200: AuctionDetailSerializer},
    ),
)
class AuctionViewSet(viewsets.ModelViewSet):
    """
    Auction ViewSet

    Actions:
    - list: Get all ACTIVE auctions (with filters)
    - create: Create new auction (ACTIVE)
    - retrieve: Get auction details
    - destroy: Delete auction (ACTIVE only)
    - cancel: Cancel active auction
    - my_auctions: Get my created auctions
    - close: Admin action to close auction
    """

    permission_resource = "AUCTION"
    queryset = Auction.objects.all().order_by("-created")
    permission_classes = [IsAuthenticated, HasAppAccess]
    manager_permission_map = {
        "list": "auctions.list",
        "active": "auctions.list",
        "ending_soon": "auctions.list",
        "retrieve": "auctions.retrieve",
        "cancel": "auctions.cancel",
        "close": "auctions.close",
    }

    def get_serializer_class(self):
        """Choose serializer based on action"""
        if self.action == "create":
            return AuctionCreateSerializer
        elif self.action == "partial_update":
            return AuctionUpdateSerializer
        elif self.action == "retrieve":
            return AuctionDetailSerializer
        elif self.action in ["list", "my_auctions", "active", "ended"]:
            return AuctionListSerializer
        elif self.action == "cancel":
            return CancelAuctionSerializer
        elif self.action == "ending_soon":
            return EndingSoonQuerySerializer
        return AuctionDetailSerializer

    def get_queryset(self):
        """Filter based on action"""
        if self.request.user.role == ProfileType.MANAGER:
            return Auction.objects.all().order_by("-created")
        else:
            if self.action in [
                "my_auctions",
                "cancel",
                "partial_update",
                "destroy",
            ]:
                return Auction.objects.filter(
                    owner=self.request.user.active_profile
                ).order_by("-created")

            # Public listings only show published auctions
            if self.action in ["list", "active", "ended"]:
                return Auction.objects.filter(is_visible=True).order_by(
                    "-created"
                )
            if self.action == "retrieve":
                return Auction.objects.all().order_by("-created")

        return Auction.objects.all().order_by("-created")

    def get_serializer_context(self):
        """Add request to context for serializers"""
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        auction = Auction(
            owner=self.request.user.active_profile,
            title=validated_data["title"],
            description=validated_data.get("description", ""),
            auction_type=validated_data["auction_type"],
            start_time=validated_data["start_time"],
            end_time=validated_data["end_time"],
            auto_extend_enabled=validated_data.get(
                "auto_extend_enabled", False
            ),
            auto_extend_minutes=validated_data.get("auto_extend_minutes", 5),
            buy_now_enabled=validated_data.get("buy_now_enabled", False),
            buy_now_price=validated_data.get("buy_now_price", None),
            deposit_required=validated_data.get("deposit_required", False),
            payment_deadline_days=validated_data.get("payment_deadline_days"),
            min_bid_increment=validated_data.get(
                "min_bid_increment", Decimal("1.00")
            ),
            tags=validated_data.get("tags", ""),
        )
        items_data = validated_data["items"]

        # Business validation (MUST pass before creation)
        validator = AuctionBusinessValidator(
            auction=auction, items_data=items_data
        )
        validator.validate()

        auction = AuctionService.create_auction(
            owner=self.request.user.active_profile,
            auction=auction,
            items_data=items_data,
        )
        if not auction:
            raise ValidationError("Failed to create auction")
        return Response(
            AuctionDetailSerializer(auction).data,
            status=status.HTTP_201_CREATED,
        )

    def retrieve(self, request, *args, **kwargs):
        auction = self.get_object()
        SellerInsightEventService.record_event(
            seller=auction.owner,
            event_type=SellerInsightEventType.AUCTION_VIEW,
            object_id=auction.pk,
            actor_profile=request.user.active_profile,
        )
        serializer = self.get_serializer(auction)
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):

        auction = self.get_object()
        serializer = self.get_serializer(
            auction, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)

        try:
            auction = serializer.save()
            return Response(
                AuctionDetailSerializer(auction).data,
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            return Response(
                {"error": str(e.detail)}, status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=["get"])
    def my_auctions(self, request):
        """
        Get all auctions created by authenticated user

        Query params:
        - status: active, ended, cancelled
        """
        queryset = self.get_queryset()

        # Filter by status if provided
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def active(self, request):
        """Get all active auctions"""
        queryset = Auction.objects.filter(
            is_visible=True,
            start_time__lte=timezone.now(),
            end_time__gt=timezone.now(),
        ).order_by("end_time")

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def ending_soon(self, request):
        """Get auctions ending in next X minutes"""
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        minutes = serializer.validated_data["minutes"]
        now = timezone.now()
        later = now + timedelta(minutes=minutes)

        queryset = Auction.objects.filter(
            is_visible=True, end_time__gt=now, end_time__lte=later
        ).order_by("end_time")

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(
            AuctionDetailSerializer(queryset, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], permission_classes=[HasAppAccess])
    def cancel(self, request, pk=None):
        """
        Cancel auction

        POST /auctions/{id}/cancel/
        """
        auction = self.get_object()
        serializer = self.get_serializer(
            data=request.data, context={"request": request, "auction": auction}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            AuctionDetailSerializer(auction).data, status=status.HTTP_200_OK
        )

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, HasAppAccess],
    )
    def close(self, request, pk=None):
        """
        Admin action: Manually close auction and resolve winners

        POST /auctions/{id}/close/
        """
        auction = self.get_object()

        try:
            auction = AuctionService.close_auction(
                auction,
                performer=self.request.user.active_profile,
            )

            return Response(
                AuctionDetailSerializer(auction).data,
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            return Response(
                {"error": str(e.detail)}, status=status.HTTP_400_BAD_REQUEST
            )


@extend_schema_view(
    list=extend_schema(
        tags=["Auctions"],
        parameters=[
            OpenApiParameter(
                name="auction_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Limit items to a specific auction.",
            )
        ],
        responses={200: AuctionItemListSerializer(many=True)},
    ),
    place_bid=extend_schema(
        tags=["Auctions"],
        request=PlaceBidSerializer,
        responses={201: BidSerializer},
    ),
    buy_now=extend_schema(
        tags=["Auctions"],
        request=None,
        responses={201: BidSerializer},
    ),
)
class AuctionItemViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Auction Item ViewSet (Read-Only for public)

    Actions:
    - list: GET /auctions/items/?auction_id={id}
    - retrieve: GET /auctions/items/{id}/
    - place_bid: POST /auctions/items/{id}/place_bid/
    - buy_now: POST /auctions/items/{id}/buy_now/
    """

    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AuctionItemDetailSerializer
        return AuctionItemListSerializer

    def get_queryset(self):
        """Filter items by auction if provided"""
        queryset = AuctionItem.objects.all()
        auction_id = self.request.query_params.get("auction_id")
        if auction_id:
            queryset = queryset.filter(auction_id=auction_id)
        return queryset.order_by("created")

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated, IsNotManagerProfile],
    )
    def place_bid(self, request, pk=None):
        """
        Place bid on item.

        Flow:
        1. Serializer validates INPUT FORMAT (amount is decimal, positive)
        2. BidBusinessValidator validates BUSINESS RULES (item active,
            bid amount valid, etc)
        3. BiddingService creates bid and saves to database

        POST /auctions/items/{item_id}/place_bid/
        {
            "amount": 100.00
        }
        """
        item = self.get_object()

        serializer = PlaceBidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = serializer.validated_data["amount"]
        bidder = request.user.active_profile

        try:
            validator = BidBusinessValidator(
                item=item,
                bidder=bidder,
                amount=amount,
            )
            validator.validate()

            bid = BiddingService.place_bid(
                item=item,
                bidder=bidder,
                amount=amount,
            )

            return Response(
                BidSerializer(bid).data, status=status.HTTP_201_CREATED
            )
        except ValidationError as e:
            raise ValidationError(str(e.detail))

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated, IsNotManagerProfile],
    )
    def buy_now(self, request, pk=None):
        """
        Trigger buy now on item

        POST /auctions/items/{item_id}/buy_now/
        """
        item = self.get_object()

        serializer = BuyNowSerializer(
            data={}, context={"request": request, "item": item}
        )
        serializer.is_valid(raise_exception=True)
        bid = serializer.save()

        return Response(
            BidSerializer(bid).data, status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Auctions"],
        parameters=[
            OpenApiParameter(
                name="item_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Limit bids to a specific auction item.",
            )
        ],
        responses={200: BidSerializer(many=True)},
    ),
    my_bids=extend_schema(
        tags=["Auctions"],
        parameters=[
            OpenApiParameter(
                name="status",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by auction item status.",
            ),
            OpenApiParameter(
                name="auction_type",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by auction type.",
            ),
        ],
        responses={200: BidSerializer(many=True)},
    ),
)
class BidViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Bid ViewSet (Read-Only)

    Actions:
    - list: GET /auctions/bids/?item_id={id}
    - retrieve: GET /auctions/bids/{id}/
    - my_bids: GET /auctions/bids/my_bids/
    """

    serializer_class = BidSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = Bid.objects.order_by("-amount")

        if self.action == "list":
            item_id = self.request.query_params.get("item_id")
            if item_id:
                queryset = queryset.filter(item_id=item_id)

        return queryset

    @action(
        detail=False,
        methods=["get"],
        permission_classes=[permissions.IsAuthenticated],
    )
    def my_bids(self, request):
        """
        Get all bids placed by authenticated user

        Query params:
        - status: active, sold, unsold
        - auction_type: single, multi, pair, breeding, racing
        """
        queryset = Bid.objects.filter(
            bidder=request.user.active_profile,
        ).order_by("-created")

        # Filter by item status
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(item__status=status_filter)

        # Filter by auction type
        auction_type = request.query_params.get("auction_type")
        if auction_type:
            queryset = queryset.filter(
                item__auction__auction_type=auction_type
            )

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=["Auctions"],
        parameters=[
            OpenApiParameter(
                name="auction_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Limit events to a specific auction.",
            )
        ],
        responses={200: AuctionEventSerializer(many=True)},
    )
)
class AuctionEventViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Auction Event ViewSet (Audit Trail)

    Read-only view of auction event history

    Actions:
    - list: GET /auctions/events/?auction_id={id}
    - retrieve: GET /auctions/events/{id}/
    """

    serializer_class = AuctionEventSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Filter events by auction if provided"""
        queryset = AuctionEvent.objects.all().order_by("-created")
        auction_id = self.request.query_params.get("auction_id")
        if auction_id:
            queryset = queryset.filter(auction_id=auction_id)
        return queryset
