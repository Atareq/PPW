from django.urls import path

from apps.orders.views import (
    CartAPIView,
    CartClearAPIView,
    CartItemCreateAPIView,
    CartItemDetailAPIView,
    CheckoutAPIView,
    OrderItemViewSet,
    OrderViewSet,
)

order_list = OrderViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
order_detail = OrderViewSet.as_view(
    {
        "get": "retrieve",
    }
)
order_item_list = OrderItemViewSet.as_view(
    {
        "get": "list",
    }
)
order_item_approve = OrderItemViewSet.as_view(
    {
        "post": "approve",
    }
)
order_item_reject = OrderItemViewSet.as_view(
    {
        "post": "reject",
    }
)

urlpatterns = [
    path("cart/", CartAPIView.as_view(), name="cart-detail"),
    path(
        "cart/items/",
        CartItemCreateAPIView.as_view(),
        name="cart-item-create",
    ),
    path(
        "cart/items/<int:pk>/",
        CartItemDetailAPIView.as_view(),
        name="cart-item-detail",
    ),
    path("cart/clear/", CartClearAPIView.as_view(), name="cart-clear"),
    path("checkout/", CheckoutAPIView.as_view(), name="checkout"),
    path("", order_list, name="order-list"),
    path("items/", order_item_list, name="order-item-list"),
    path(
        "items/<int:pk>/approve/",
        order_item_approve,
        name="order-item-approve",
    ),
    path(
        "items/<int:pk>/reject/", order_item_reject, name="order-item-reject"
    ),
    path("<int:pk>/", order_detail, name="order-detail"),
]
