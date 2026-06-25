from django.urls import path

from apps.lucky_wheel.views import (
    AdminLuckyWheelGrantViewSet,
    AdminLuckyWheelPrizeViewSet,
    AdminLuckyWheelSpinViewSet,
    LuckyWheelCurrentViewSet,
    LuckyWheelSpinViewSet,
    MyLuckyWheelSpinViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
PARTIAL_UPDATE_ACTION = {"patch": "partial_update"}

urlpatterns = [
    path(
        "current/",
        LuckyWheelCurrentViewSet.as_view(LIST_ACTION),
        name="lucky-wheel-current",
    ),
    path(
        "spin/",
        LuckyWheelSpinViewSet.as_view(CREATE_ACTION),
        name="lucky-wheel-spin",
    ),
    path(
        "spins/my/",
        MyLuckyWheelSpinViewSet.as_view(LIST_ACTION),
        name="my-lucky-wheel-spin-list",
    ),
    path(
        "admin/prizes/",
        AdminLuckyWheelPrizeViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-lucky-wheel-prize-list",
    ),
    path(
        "admin/prizes/<int:pk>/",
        AdminLuckyWheelPrizeViewSet.as_view({**PARTIAL_UPDATE_ACTION, "delete": "destroy"}),
        name="admin-lucky-wheel-prize-detail",
    ),
    path(
        "admin/prizes/reset/",
        AdminLuckyWheelPrizeViewSet.as_view({"delete": "reset"}),
        name="admin-lucky-wheel-prize-reset",
    ),
    path(
        "admin/spins/",
        AdminLuckyWheelSpinViewSet.as_view(LIST_ACTION),
        name="admin-lucky-wheel-spin-list",
    ),
    path(
        "admin/grants/",
        AdminLuckyWheelGrantViewSet.as_view(LIST_ACTION),
        name="admin-lucky-wheel-grant-list",
    ),
    path(
        "admin/grants/grant/",
        AdminLuckyWheelGrantViewSet.as_view({"post": "grant"}),
        name="admin-lucky-wheel-grant-create",
    ),
    path(
        "admin/grants/<int:pk>/revoke/",
        AdminLuckyWheelGrantViewSet.as_view({"delete": "revoke"}),
        name="admin-lucky-wheel-grant-revoke",
    ),
]
