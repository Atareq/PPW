from django.urls import path

from apps.social.views import (
    SellerFollowCreateViewSet,
    SellerFollowingViewSet,
    SellerUnfollowCreateViewSet,
)

CREATE_ACTION = {"post": "create"}
LIST_ACTION = {"get": "list"}

app_name = "social"

urlpatterns = [
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
]
