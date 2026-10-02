from django.urls import path

from apps.loyalty.views import (
    BadgeViewSet,
    BuyPointsWithCashbackViewSet,
    PointBalanceAPIView,
    PointTransactionViewSet,
    RewardRuleAdminViewSet,
)

LIST_ACTION = {"get": "list"}
RETRIEVE_PARTIAL_UPDATE_ACTION = {"get": "retrieve", "patch": "partial_update"}

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
    path(
        "admin/reward-rules/",
        RewardRuleAdminViewSet.as_view(LIST_ACTION),
        name="loyalty-admin-reward-rule-list",
    ),
    path(
        "admin/reward-rules/<str:code>/",
        RewardRuleAdminViewSet.as_view(RETRIEVE_PARTIAL_UPDATE_ACTION),
        name="loyalty-admin-reward-rule-detail",
    ),
]
