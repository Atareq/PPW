from apps.general.viewsets import CreateListViewSet
from apps.ratings.filters import (
    AssetCommentFilter,
    AssetRatingFilter,
    SellerCommentFilter,
    SellerRatingFilter,
)
from apps.ratings.models import (
    AssetComment,
    AssetRating,
    SellerComment,
    SellerRating,
)
from apps.ratings.serializers import (
    AssetCommentCreateSerializer,
    AssetCommentSerializer,
    AssetRatingCreateSerializer,
    AssetRatingSerializer,
    SellerCommentCreateSerializer,
    SellerCommentSerializer,
    SellerRatingCreateSerializer,
    SellerRatingSerializer,
)


class AssetCommentViewSet(CreateListViewSet):
    permission_resource = "ASSET_COMMENT"
    queryset = AssetComment.objects.all()
    filterset_class = AssetCommentFilter
    create_serializer_class = AssetCommentCreateSerializer
    list_serializer_class = AssetCommentSerializer

    def get_queryset(self):
        return AssetComment.objects.select_related(
            "owner",
            "owner__user",
            "asset",
        ).order_by("-created")


class SellerCommentViewSet(CreateListViewSet):
    permission_resource = "SELLER_COMMENT"
    queryset = SellerComment.objects.all()
    filterset_class = SellerCommentFilter
    create_serializer_class = SellerCommentCreateSerializer
    list_serializer_class = SellerCommentSerializer

    def get_queryset(self):
        return SellerComment.objects.select_related(
            "owner",
            "owner__user",
            "seller",
            "seller__user",
        ).order_by("-created")


class AssetRatingViewSet(CreateListViewSet):
    permission_resource = "ASSET_RATING"
    queryset = AssetRating.objects.all()
    filterset_class = AssetRatingFilter
    create_serializer_class = AssetRatingCreateSerializer
    list_serializer_class = AssetRatingSerializer
    ordering_fields = [
        "created",
        "modified",
        "stars",
    ]

    def get_queryset(self):
        return AssetRating.objects.select_related(
            "owner",
            "owner__user",
            "asset",
            "comment",
            "comment__owner",
            "comment__owner__user",
            "comment__asset",
        ).order_by("-created")


class SellerRatingViewSet(CreateListViewSet):
    permission_resource = "SELLER_RATING"
    queryset = SellerRating.objects.all()
    filterset_class = SellerRatingFilter
    create_serializer_class = SellerRatingCreateSerializer
    list_serializer_class = SellerRatingSerializer
    ordering_fields = [
        "created",
        "modified",
        "stars",
    ]

    def get_queryset(self):
        return SellerRating.objects.select_related(
            "owner",
            "owner__user",
            "seller",
            "seller__user",
            "comment",
            "comment__owner",
            "comment__owner__user",
            "comment__seller",
            "comment__seller__user",
        ).order_by("-created")
