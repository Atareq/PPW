from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.auctions.views import (
    AuctionCommentViewSet,
    AuctionEventViewSet,
    AuctionItemViewSet,
    AuctionViewSet,
    BidViewSet,
)

router = DefaultRouter()
router.register(r"items", AuctionItemViewSet, basename="auction-item")
router.register(r"bids", BidViewSet, basename="bid")
router.register(r"events", AuctionEventViewSet, basename="auction-event")
router.register(r"comments", AuctionCommentViewSet, basename="auction-comment")
router.register(r"", AuctionViewSet, basename="auction")

app_name = "auctions"

urlpatterns = [
    path("", include(router.urls)),
]
