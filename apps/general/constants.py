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
    "AUCTION_ITEM": [
        "list",
        "retrieve",
        "place_bid",
        "buy_now",
    ],
    "ORDER": [
        "list",
        "retrieve",
    ],
    "ORDER_ITEM": [
        "list",
        "retrieve",
        "approve",
        "reject",
    ],
    "PAYMENT_REQUEST": [
        "list",
        "create",
    ],
    "OWNERSHIP_TRANSFER": [
        "list",
        "retrieve",
        "create",
    ],
    "COMPLAINT": [
        "list",
        "retrieve",
        "create",
    ],
    "CHAT": [
        "list",
        "create",
    ],
    "FEED_BLOCK": [
        "list",
        "create",
        "destroy",
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
    "DISCOUNT_OFFER": [
        "list",
    ],
    "CASHBACK_OFFER": [
        "list",
    ],
    "PEDIGREE_DOCUMENT": [
        "list",
        "create",
        "retrieve",
        "partial_update",
    ],
    "NOTIFICATION": [
        "list",
        "create",
    ],
    "ANNOUNCEMENT": [
        "list",
        "retrieve",
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
        "ending_soon",
    ],
    "AUCTION_ITEM": [
        "list",
        "retrieve",
        "place_bid",
        "buy_now",
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
    "OWNERSHIP_TRANSFER": [
        "list",
        "retrieve",
    ],
    "COMPLAINT": [
        "list",
        "retrieve",
        "create",
    ],
    "CHAT": [
        "list",
        "create",
    ],
    "FEED_BLOCK": [
        "list",
        "create",
        "destroy",
    ],
    "ASSET_COMMENT": [
        "list",
        "create",
        "destroy",
    ],
    "SELLER_COMMENT": [
        "list",
        "create",
        "destroy",
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
    "DISCOUNT_OFFER": [
        "list",
    ],
    "CASHBACK_OFFER": [
        "list",
    ],
    "CASHBACK": [
        "list",
        "retrieve",
    ],
    "PEDIGREE_DOCUMENT": [
        "list",
        "create",
        "retrieve",
        "partial_update",
    ],
    "NOTIFICATION": [
        "list",
        "create",
    ],
    "ANNOUNCEMENT": [
        "list",
        "retrieve",
    ],
}
