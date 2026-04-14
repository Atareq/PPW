SELLER_PERMISSIONS = {
    "BIRD": [
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
}

CUSTOMER_PERMISSIONS = {
    "BIRD": [
        "list",
        "retrieve",
        "update",
        "partial_update",
        "destroy",
    ],
    "AUCTION": [
        "list",
        "retrieve",
        "active",

    ],
}
