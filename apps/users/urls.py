from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.users.views import (
    CustomerProfileAdminViewSet,
    CustomerProfileMobileViewSet,
    ManagerPermissionRegistryViewSet,
    ManagerPermissionUpdateViewSet,
    ManagerProfileAdminViewSet,
    ProfileBalanceAdjustmentViewSet,
    SellerProfileAdminViewSet,
    SellerProfileMobileViewSet,
    SellerRoomFollowViewSet,
    SellerRoomUnfollowViewSet,
    SellerRoomViewSet,
)

CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
DETAIL_ACTIONS = {
    "get": "retrieve",
    "put": "update",
    "patch": "partial_update",
    "delete": "destroy",
}


def build_seller_room_urlpatterns(*, url_prefix: str = "", name_prefix: str = "seller-room"):
    return [
        path(
            f"{url_prefix}",
            SellerRoomViewSet.as_view(LIST_CREATE_ACTION),
            name=f"{name_prefix}-list",
        ),
        path(
            f"{url_prefix}<int:pk>/",
            SellerRoomViewSet.as_view(DETAIL_ACTIONS),
            name=f"{name_prefix}-detail",
        ),
        path(
            f"{url_prefix}<int:pk>/follow/",
            SellerRoomFollowViewSet.as_view(CREATE_ACTION),
            name=f"{name_prefix}-follow",
        ),
        path(
            f"{url_prefix}<int:pk>/unfollow/",
            SellerRoomUnfollowViewSet.as_view(CREATE_ACTION),
            name=f"{name_prefix}-unfollow",
        ),
    ]


seller_room_urlpatterns = build_seller_room_urlpatterns()

router = DefaultRouter()

# Dashboard / admin
router.register("admin/sellers", SellerProfileAdminViewSet, basename="admin-sellers")
router.register(
    "admin/customers", CustomerProfileAdminViewSet, basename="admin-customers"
)

# Mobile / user
router.register("mobile/sellers", SellerProfileMobileViewSet, basename="mobile-sellers")
router.register(
    "mobile/customers",
    CustomerProfileMobileViewSet,
    basename="mobile-customers",
)


urlpatterns = [
    path(
        "admin/managers/",
        ManagerProfileAdminViewSet.as_view({"get": "list", "post": "create"}),
        name="admin-manager-list",
    ),
    path(
        "admin/managers/<int:pk>/",
        ManagerProfileAdminViewSet.as_view(
            {
                "get": "retrieve",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="admin-manager-detail",
    ),
    path(
        "admin/manager-permissions/",
        ManagerPermissionRegistryViewSet.as_view({"get": "list"}),
        name="admin-manager-permission-list",
    ),
    path(
        "admin/managers/<int:pk>/permissions/",
        ManagerPermissionUpdateViewSet.as_view({"patch": "partial_update"}),
        name="admin-manager-permission-update",
    ),
    path(
        "admin/profile-balance-adjustments/",
        ProfileBalanceAdjustmentViewSet.as_view({"post": "create"}),
        name="admin-profile-balance-adjustment-create",
    ),
    *build_seller_room_urlpatterns(
        url_prefix="mobile/seller-rooms/",
        name_prefix="mobile-seller-room",
    ),
    path("", include(router.urls)),
]
