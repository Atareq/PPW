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
    "PAYMENT_REQUEST": [
        "list",
        "create",
    ],
    "ASSET_COMMENT": [
        "list",
        "create",
    ],
    "SELLER_COMMENT": [
        "list",
        "create",
    ],
    "ASSET_RATING": [
        "list",
    ],
    "SELLER_RATING": [
        "list",
    ],
    "RACE": [
        "list",
        "retrieve",
    ],
    "RACE_RESULT": [
        "list",
        "retrieve",
    ],
    "PACKAGE": [
        "list",
    ],
    "MY_SELLER_PACKAGE": [
        "list",
        "create",
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
    "PAYMENT_REQUEST": [
        "create",
        "list",
        "partial_update",
    ],
    "ASSET_COMMENT": [
        "list",
        "create",
    ],
    "SELLER_COMMENT": [
        "list",
        "create",
    ],
    "ASSET_RATING": [
        "list",
        "create",
    ],
    "SELLER_RATING": [
        "list",
        "create",
    ],
    "RACE": [
        "list",
        "retrieve",
    ],
    "RACE_RESULT": [
        "list",
        "retrieve",
    ],
}
