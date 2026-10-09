from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.ratings.views import (
    AssetCommentViewSet,
    AssetRatingViewSet,
    DealRatingStatusViewSet,
    DealRatingViewSet,
    SellerCommentViewSet,
    SellerRatingViewSet,
)

CREATE_ACTION = {"post": "create"}
RETRIEVE_ACTION = {"get": "retrieve"}

router = DefaultRouter()
router.register(
    "asset-comments",
    AssetCommentViewSet,
    basename="asset-comment",
)
router.register(
    "seller-comments",
    SellerCommentViewSet,
    basename="seller-comment",
)
router.register(
    "asset-ratings",
    AssetRatingViewSet,
    basename="asset-rating",
)
router.register(
    "seller-ratings",
    SellerRatingViewSet,
    basename="seller-rating",
)

urlpatterns = [
    path(
        "deal-ratings/",
        DealRatingViewSet.as_view(CREATE_ACTION),
        name="deal-rating-create",
    ),
    path(
        "deal-ratings/deals/<int:deal_id>/",
        DealRatingStatusViewSet.as_view(RETRIEVE_ACTION),
        name="deal-rating-status",
    ),
    path("", include(router.urls)),
]
