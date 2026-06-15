from django.urls import path

from apps.feed.views import (
    AuctionFeedViewSet,
    MarketFeedViewSet,
    PeopleYouMayKnowViewSet,
    SellerCustomerBlockViewSet,
    SellerFollowCreateViewSet,
    SellerFollowingViewSet,
    SellerUnfollowCreateViewSet,
)

CREATE_ACTION = {"post": "create"}
LIST_ACTION = {"get": "list"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
DESTROY_ACTION = {"delete": "destroy"}

app_name = "feed"

urlpatterns = [
    path(
        "blocks/customer-seller/",
        SellerCustomerBlockViewSet.as_view(LIST_CREATE_ACTION),
        name="seller-customer-block-list-create",
    ),
    path(
        "blocks/customer-seller/<int:profile_id>/",
        SellerCustomerBlockViewSet.as_view(DESTROY_ACTION),
        name="seller-customer-block-delete",
    ),
    path(
        "sellers/<int:seller_id>/follow/",
        SellerFollowCreateViewSet.as_view(CREATE_ACTION),
        name="seller-follow",
    ),
    path(
        "sellers/<int:seller_id>/unfollow/",
        SellerUnfollowCreateViewSet.as_view(CREATE_ACTION),
        name="seller-unfollow",
    ),
    path(
        "sellers/following/",
        SellerFollowingViewSet.as_view(LIST_ACTION),
        name="seller-following",
    ),
    path(
        "people-you-may-know/",
        PeopleYouMayKnowViewSet.as_view(LIST_ACTION),
        name="people-you-may-know",
    ),
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
