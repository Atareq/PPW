SELLER_PERMISSIONS = {
    "BIRD": [
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "destroy",
    ],
    "PRODUCT_ASSET": [
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "destroy",
    ],
    "AUCTION": [
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "destroy",
        "my_auctions",
        "active",
        "ended",
        "publish",
        "ending_soon",
        "cancel",
    ],
    "ORDER_ITEM": [
        "list",
        "approve",
        "reject",
    ],
}

CUSTOMER_PERMISSIONS = {
    "BIRD": [
        "list",
        "retrieve",
        "update",
        "partial_update",
        "destroy",
    ],
    "PRODUCT_ASSET": [
        "list",
        "retrieve",
    ],
    "CART": [
        "retrieve",
        "post",
        "patch",
        "remove_cart_item",
        "delete",
    ],
    "CHECKOUT": [
        "post",
    ],
    "AUCTION": [
        "list",
        "retrieve",
        "active",
    ],
    "ORDER": [
        "list",
        "retrieve",
        "create",
    ],
}
