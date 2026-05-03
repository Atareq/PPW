from django.urls import path

from apps.orders.views import (
    ActiveCartViewSet,
    CheckoutViewSet,
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
cart_detail = ActiveCartViewSet.as_view({"get": "retrieve"})
cart_item_create = ActiveCartViewSet.as_view({"post": "post"})
cart_item_detail = ActiveCartViewSet.as_view(
    {
        "patch": "patch",
        "delete": "remove_cart_item",
    }
)
cart_clear = ActiveCartViewSet.as_view({"delete": "delete"})
checkout = CheckoutViewSet.as_view({"post": "post"})

urlpatterns = [
    path("cart/", cart_detail, name="cart-detail"),
    path("cart/items/", cart_item_create, name="cart-item-create"),
    path("cart/items/<int:pk>/", cart_item_detail, name="cart-item-detail"),
    path("cart/clear/", cart_clear, name="cart-clear"),
    path("checkout/", checkout, name="checkout"),
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
