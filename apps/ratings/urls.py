from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.ratings.views import (
    AssetCommentViewSet,
    AssetRatingViewSet,
    SellerCommentViewSet,
    SellerRatingViewSet,
)

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
    path("", include(router.urls)),
]
