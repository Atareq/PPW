from django.urls import path

from apps.loyalty.views import (
    BadgeViewSet,
    BuyPointsWithCashbackViewSet,
    PointBalanceAPIView,
    PointTransactionViewSet,
)

point_transaction_list = PointTransactionViewSet.as_view(
    {
        "get": "list",
    }
)
badge_list = BadgeViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
my_badge_list = BadgeViewSet.as_view(
    {
        "get": "my",
    }
)
buy_points_with_cashback = BuyPointsWithCashbackViewSet.as_view(
    {
        "post": "create",
    }
)

urlpatterns = [
    path("points/", PointBalanceAPIView.as_view(), name="loyalty-points"),
    path(
        "points/buy-with-cashback/",
        buy_points_with_cashback,
        name="loyalty-buy-points-with-cashback",
    ),
    path(
        "points/transactions/",
        point_transaction_list,
        name="loyalty-point-transaction-list",
    ),
    path("badges/", badge_list, name="loyalty-badge-list"),
    path("badges/my/", my_badge_list, name="loyalty-my-badge-list"),
]
