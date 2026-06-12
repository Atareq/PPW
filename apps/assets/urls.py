from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.assets.views import (
    AccessoryViewSet,
    BirdViewSet,
    FeedViewSet,
    PublicBirdViewSet,
    SupplementViewSet,
    SupplyViewSet,
)

router = DefaultRouter()

router.register("birds", BirdViewSet)
router.register("supplies", SupplyViewSet)
router.register("accessories", AccessoryViewSet)
router.register("feeds", FeedViewSet)
router.register("supplements", SupplementViewSet)

BIRD_PUBLIC_RETRIEVE_ACTION = {"get": "retrieve"}

urlpatterns = [
    path(
        "birds/public/<uuid:public_id>/",
        PublicBirdViewSet.as_view(BIRD_PUBLIC_RETRIEVE_ACTION),
        name="bird-public-detail",
    ),
    path("", include(router.urls)),
]
