from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import AllowAny
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response

from apps.assets.filters import (
    AccessoryFilter,
    FeedFilter,
    SupplementFilter,
    SupplyFilter,
)
from apps.assets.models import (
    Accessory,
    AssetStatus,
    Bird,
    Feed,
    Supplement,
    Supply,
)
from apps.assets.serializer import (
    AccessorySerializer,
    BirdSerializer,
    FeedSerializer,
    PublicBirdSerializer,
    SupplementSerializer,
    SupplySerializer,
)
from apps.general.authorizations import HasAppAccess
from apps.insights.models import SellerInsightEventType
from apps.insights.services import SellerInsightEventService
from apps.users.constants import ProfileType


class BirdViewSet(viewsets.ModelViewSet):
    """Bird asset CRUD gated by BIRD app permissions and seller validation."""

    queryset = Bird.objects.prefetch_related("media").order_by("-created")
    serializer_class = BirdSerializer
    permission_classes = [
        HasAppAccess,
    ]
    permission_resource = "BIRD"
    manager_permission_namespace = "assets"


class PublicBirdViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Public-safe bird identity lookup by stable UUID."""

    serializer_class = PublicBirdSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        return (
            Bird.objects.filter(deleted__isnull=True)
            .exclude(status=AssetStatus.INACTIVE)
            .select_related("owner", "owner__sellerprofile")
            .prefetch_related("media")
        )


class ProductAssetViewSet(viewsets.ModelViewSet):
    """Shared product asset API with role-scoped market visibility."""

    permission_classes = [HasAppAccess]
    permission_resource = "PRODUCT_ASSET"
    manager_permission_namespace = "assets"
    allow_scoped_read_access = True
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    ordering_fields = [
        "price",
        "count",
        "created",
        "modified",
        "title",
        "status",
    ]

    def retrieve(self, request, *args, **kwargs):
        asset = self.get_object()
        SellerInsightEventService.record_event(
            seller=asset.owner,
            event_type=SellerInsightEventType.MARKET_ITEM_VIEW,
            object_id=asset.pk,
            actor_profile=request.user.active_profile,
        )
        serializer = self.get_serializer(asset)
        return Response(serializer.data)

    def get_queryset(self):
        """Managers see all; customers see listed; sellers see own plus listed."""

        queryset = super().get_queryset().prefetch_related("media").order_by(
            "-created"
        )
        user = self.request.user
        role = user.role

        if role == ProfileType.MANAGER:
            return queryset

        active_profile = user.active_profile

        if self.request.method in SAFE_METHODS:
            if role == ProfileType.CUSTOMER:
                return queryset.filter(is_market_listed=True)

            if role == ProfileType.SELLER:
                return queryset.filter(
                    Q(owner=active_profile) | Q(is_market_listed=True)
                )

        return queryset


class SupplyViewSet(ProductAssetViewSet):
    queryset = Supply.objects.all()
    serializer_class = SupplySerializer
    filterset_class = SupplyFilter


class AccessoryViewSet(ProductAssetViewSet):
    queryset = Accessory.objects.all()
    serializer_class = AccessorySerializer
    filterset_class = AccessoryFilter


class FeedViewSet(ProductAssetViewSet):
    queryset = Feed.objects.all()
    serializer_class = FeedSerializer
    filterset_class = FeedFilter


class SupplementViewSet(ProductAssetViewSet):
    queryset = Supplement.objects.all()
    serializer_class = SupplementSerializer
    filterset_class = SupplementFilter
