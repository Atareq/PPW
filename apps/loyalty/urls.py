from django.urls import path

from apps.loyalty.views import (
    BadgeViewSet,
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

urlpatterns = [
    path("points/", PointBalanceAPIView.as_view(), name="loyalty-points"),
    path(
        "points/transactions/",
        point_transaction_list,
        name="loyalty-point-transaction-list",
    ),
    path("badges/", badge_list, name="loyalty-badge-list"),
    path("badges/my/", my_badge_list, name="loyalty-my-badge-list"),
]

