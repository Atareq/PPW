from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    AuctionViewSet,
    AuctionItemViewSet,
    BidViewSet,
    AuctionEventViewSet,
)

router = DefaultRouter()
router.register(r'items', AuctionItemViewSet, basename='auction-item')
router.register(r'bids', BidViewSet, basename='bid')
router.register(r'events', AuctionEventViewSet, basename='auction-event')
router.register(r'', AuctionViewSet, basename='auction')

app_name = 'auctions'

urlpatterns = [
    path('', include(router.urls)),
]

