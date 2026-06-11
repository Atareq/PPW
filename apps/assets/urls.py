from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.assets.views import (
    AccessoryViewSet,
    BirdViewSet,
    FeedViewSet,
    SupplementViewSet,
    SupplyViewSet,
)
router = DefaultRouter()

router.register("birds", BirdViewSet)
router.register("supplies", SupplyViewSet)
router.register("accessories", AccessoryViewSet)
router.register("feeds", FeedViewSet)
router.register("supplements", SupplementViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
