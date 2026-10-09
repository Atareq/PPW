from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.auctions.views import (
    AuctionEventViewSet,
    AuctionItemViewSet,
    AuctionSellerPaymentDetailsViewSet,
    AuctionViewSet,
    BidViewSet,
)

RETRIEVE_ACTION = {"get": "retrieve"}

router = DefaultRouter()
router.register(r"items", AuctionItemViewSet, basename="auction-item")
router.register(r"bids", BidViewSet, basename="bid")
router.register(r"events", AuctionEventViewSet, basename="auction-event")
router.register(r"", AuctionViewSet, basename="auction")

app_name = "auctions"

urlpatterns = [
    path(
        "<int:pk>/seller-payment-details/",
        AuctionSellerPaymentDetailsViewSet.as_view(RETRIEVE_ACTION),
        name="auction-seller-payment-details",
    ),
    path("", include(router.urls)),
]
