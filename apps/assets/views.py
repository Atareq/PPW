from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import SAFE_METHODS

from apps.assets.filters import (
    AccessoryFilter,
    FeedFilter,
    SupplementFilter,
    SupplyFilter,
)
from apps.assets.models import Accessory, Bird, Feed, Supplement, Supply
from apps.assets.serializer import (
    AccessorySerializer,
    BirdSerializer,
    FeedSerializer,
    SupplementSerializer,
    SupplySerializer,
)
from apps.general.authorizations import HasAppAccess
from apps.users.constants import ProfileType


class BirdViewSet(viewsets.ModelViewSet):
    queryset = Bird.objects.prefetch_related("images").order_by("-created")
    serializer_class = BirdSerializer
    permission_classes = [
        HasAppAccess,
    ]
    permission_resource = "BIRD"


class ProductAssetViewSet(viewsets.ModelViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "PRODUCT_ASSET"
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

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("images").order_by(
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
