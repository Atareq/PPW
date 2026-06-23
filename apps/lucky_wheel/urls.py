from django.urls import path

from apps.lucky_wheel.views import (
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
        AdminLuckyWheelPrizeViewSet.as_view(PARTIAL_UPDATE_ACTION),
        name="admin-lucky-wheel-prize-detail",
    ),
    path(
        "admin/spins/",
        AdminLuckyWheelSpinViewSet.as_view(LIST_ACTION),
        name="admin-lucky-wheel-spin-list",
    ),
]
