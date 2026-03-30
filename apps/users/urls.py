from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.users.views import (
    SellerProfileAdminViewSet,
    CustomerProfileAdminViewSet,
    SellerProfileMobileViewSet,
    CustomerProfileMobileViewSet,
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
    path("", include(router.urls)),
]
