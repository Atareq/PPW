from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.general.viewsets import CreateListRetrieveDestroyViewSet
from apps.ratings.filters import (
    AssetCommentFilter,
    AssetRatingFilter,
    SellerCommentFilter,
    SellerRatingFilter,
)
from apps.ratings.models import (
    AssetComment,
    AssetRating,
    DealRating,
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
    DealRatingCreateSerializer,
    DealRatingSerializer,
    DealRatingStatusSerializer,
)
from apps.ratings.services import (
    DealRatingService,
    update_asset_rating,
    update_seller_rating,
)


class AssetCommentViewSet(CreateListRetrieveDestroyViewSet):
    permission_resource = "ASSET_COMMENT"
    manager_permission_map = {
        "list": "comments.list",
        "retrieve": "comments.retrieve",
        "destroy": "comments.delete",
    }
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


class SellerCommentViewSet(CreateListRetrieveDestroyViewSet):
    permission_resource = "SELLER_COMMENT"
    manager_permission_map = {
        "list": "comments.list",
        "retrieve": "comments.retrieve",
        "destroy": "comments.delete",
    }
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


class AssetRatingViewSet(CreateListRetrieveDestroyViewSet):
    permission_resource = "ASSET_RATING"
    manager_permission_map = {
        "list": "ratings.list",
        "retrieve": "ratings.retrieve",
        "destroy": "ratings.delete",
    }
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

    @transaction.atomic
    def perform_destroy(self, instance):
        asset = instance.asset
        comment = instance.comment
        instance.delete()
        if comment and comment.is_rating_comment:
            comment.delete()
        update_asset_rating(asset)


class SellerRatingViewSet(CreateListRetrieveDestroyViewSet):
    permission_resource = "SELLER_RATING"
    manager_permission_map = {
        "list": "ratings.list",
        "retrieve": "ratings.retrieve",
        "destroy": "ratings.delete",
    }
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

    @transaction.atomic
    def perform_destroy(self, instance):
        seller = instance.seller
        comment = instance.comment
        instance.delete()
        if comment and comment.is_rating_comment:
            comment.delete()
        update_seller_rating(seller)


class DealRatingViewSet(viewsets.GenericViewSet):
    """Deal participants rate each other; the rated profile is derived.

    The caller rates as their active profile, which must be the deal's buyer
    or seller profile; anyone else gets 404. One rating per party per deal,
    no edits or deletes.
    """

    permission_classes = [HasAppAccess]
    permission_resource = "DEAL_RATING"
    queryset = DealRating.objects.all()
    serializer_class = DealRatingCreateSerializer

    @extend_schema(
        tags=["Ratings"],
        request=DealRatingCreateSerializer,
        responses={201: DealRatingSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = DealRatingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rating = DealRatingService.create(
            request.user.active_profile,
            serializer.validated_data["deal_id"],
            serializer.validated_data["stars"],
            serializer.validated_data["comment"],
        )
        return Response(
            DealRatingSerializer(rating).data,
            status=status.HTTP_201_CREATED,
        )


class DealRatingStatusViewSet(viewsets.GenericViewSet):
    """Mutual rating state of one deal for the active participant."""

    permission_classes = [HasAppAccess]
    permission_resource = "DEAL_RATING"
    queryset = DealRating.objects.all()
    serializer_class = DealRatingStatusSerializer

    @extend_schema(tags=["Ratings"], responses={200: DealRatingStatusSerializer})
    def retrieve(self, request, deal_id=None, *args, **kwargs):
        state = DealRatingService.status(request.user.active_profile, deal_id)
        return Response(DealRatingStatusSerializer(state).data)
