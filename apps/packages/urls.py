from django.urls import path

from apps.packages.views import (
    ManagerSellerPackageViewSet,
    MySellerPackageViewSet,
    PackageViewSet,
)

package_list = PackageViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
package_manager_detail = PackageViewSet.as_view(
    {
        "patch": "partial_update",
    }
)
my_seller_package_list = MySellerPackageViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
my_seller_package_cancel = MySellerPackageViewSet.as_view(
    {
        "post": "cancel",
    }
)
manager_seller_package_list = ManagerSellerPackageViewSet.as_view(
    {
        "get": "list",
    }
)
manager_seller_package_detail = ManagerSellerPackageViewSet.as_view(
    {
        "patch": "partial_update",
    }
)

urlpatterns = [
    path("packages/", package_list, name="package-list"),
    path(
        "packages/manager/packages/<int:pk>/",
        package_manager_detail,
        name="package-manager-detail",
    ),
    path(
        "packages/my-packages/",
        my_seller_package_list,
        name="my-seller-package-list",
    ),
    path(
        "packages/my-packages/<int:pk>/cancel/",
        my_seller_package_cancel,
        name="my-seller-package-cancel",
    ),
    path(
        "seller-packages/",
        manager_seller_package_list,
        name="manager-seller-package-list",
    ),
    path(
        "seller-packages/<int:pk>/",
        manager_seller_package_detail,
        name="manager-seller-package-detail",
    ),
]
