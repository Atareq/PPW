from django.urls import path

from apps.prizes.views import (
    AdminPrizeAwardViewSet,
    AdminPrizeClaimDeliverViewSet,
    AdminPrizeClaimReviewViewSet,
    AdminPrizeClaimViewSet,
    MyPrizeAwardClaimViewSet,
    MyPrizeAwardViewSet,
)

LIST_ACTION = {"get": "list"}
RETRIEVE_ACTION = {"get": "retrieve"}
CREATE_ACTION = {"post": "create"}

urlpatterns = [
    path(
        "my-awards/",
        MyPrizeAwardViewSet.as_view(LIST_ACTION),
        name="my-prize-award-list",
    ),
    path(
        "my-awards/<int:pk>/",
        MyPrizeAwardViewSet.as_view(RETRIEVE_ACTION),
        name="my-prize-award-detail",
    ),
    path(
        "my-awards/<int:pk>/claim/",
        MyPrizeAwardClaimViewSet.as_view(CREATE_ACTION),
        name="my-prize-award-claim",
    ),
    path(
        "admin/awards/",
        AdminPrizeAwardViewSet.as_view(LIST_ACTION),
        name="admin-prize-award-list",
    ),
    path(
        "admin/awards/<int:pk>/",
        AdminPrizeAwardViewSet.as_view(RETRIEVE_ACTION),
        name="admin-prize-award-detail",
    ),
    path(
        "admin/claims/",
        AdminPrizeClaimViewSet.as_view(LIST_ACTION),
        name="admin-prize-claim-list",
    ),
    path(
        "admin/claims/<int:pk>/",
        AdminPrizeClaimViewSet.as_view(RETRIEVE_ACTION),
        name="admin-prize-claim-detail",
    ),
    path(
        "admin/claims/<int:pk>/review/",
        AdminPrizeClaimReviewViewSet.as_view(CREATE_ACTION),
        name="admin-prize-claim-review",
    ),
    path(
        "admin/claims/<int:pk>/deliver/",
        AdminPrizeClaimDeliverViewSet.as_view(CREATE_ACTION),
        name="admin-prize-claim-deliver",
    ),
]


