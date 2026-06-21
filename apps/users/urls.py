from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.users.views import (
    CustomerProfileAdminViewSet,
    CustomerProfileMobileViewSet,
    ManagerPermissionRegistryViewSet,
    ManagerPermissionUpdateViewSet,
    ManagerProfileAdminViewSet,
    SellerProfileAdminViewSet,
    SellerProfileMobileViewSet,
)

router = DefaultRouter()

# Dashboard / admin
router.register(
    "admin/sellers", SellerProfileAdminViewSet, basename="admin-sellers"
)
router.register(
    "admin/customers", CustomerProfileAdminViewSet, basename="admin-customers"
)

# Mobile / user
router.register(
    "mobile/sellers", SellerProfileMobileViewSet, basename="mobile-sellers"
)
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
    path("", include(router.urls)),
]
