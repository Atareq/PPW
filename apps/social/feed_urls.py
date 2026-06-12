from django.urls import path

from apps.social.views import AuctionFeedViewSet, MarketFeedViewSet

LIST_ACTION = {"get": "list"}

app_name = "feed"

urlpatterns = [
    path(
        "auctions/",
        AuctionFeedViewSet.as_view(LIST_ACTION),
        name="auction-feed",
    ),
    path(
        "market/",
        MarketFeedViewSet.as_view(LIST_ACTION),
        name="market-feed",
    ),
]
