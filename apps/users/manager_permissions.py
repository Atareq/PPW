from collections import OrderedDict

from django.core.exceptions import ValidationError


def _permission(
    code: str,
    group_key: str,
    group_label: str,
    label: str,
    description: str,
) -> dict[str, str]:
    return {
        "code": code,
        "group_key": group_key,
        "group_label": group_label,
        "label": label,
        "description": description,
    }


MANAGER_PERMISSION_CATALOG: tuple[dict[str, str], ...] = (
    _permission(
        "users.sellers.list",
        "users.sellers",
        "Seller Profiles",
        "List sellers",
        "View the seller profile directory.",
    ),
    _permission(
        "users.sellers.retrieve",
        "users.sellers",
        "Seller Profiles",
        "View seller details",
        "Open one seller profile record.",
    ),
    _permission(
        "users.sellers.update",
        "users.sellers",
        "Seller Profiles",
        "Update sellers",
        "Edit seller profile moderation and correction fields.",
    ),
    _permission(
        "users.customers.list",
        "users.customers",
        "Customer Profiles",
        "List customers",
        "View the customer profile directory.",
    ),
    _permission(
        "users.customers.retrieve",
        "users.customers",
        "Customer Profiles",
        "View customer details",
        "Open one customer profile record.",
    ),
    _permission(
        "users.customers.update",
        "users.customers",
        "Customer Profiles",
        "Update customers",
        "Edit customer profile moderation and correction fields.",
    ),
    _permission(
        "managers.list",
        "managers",
        "Managers",
        "List managers",
        "View manager accounts.",
    ),
    _permission(
        "managers.retrieve",
        "managers",
        "Managers",
        "View manager details",
        "Open one manager account.",
    ),
    _permission(
        "assets.list",
        "assets",
        "Assets",
        "List assets",
        "View birds and market products.",
    ),
    _permission(
        "assets.retrieve",
        "assets",
        "Assets",
        "View asset details",
        "Open one bird or product record.",
    ),
    _permission(
        "assets.create",
        "assets",
        "Assets",
        "Create assets",
        "Create product or bird asset records.",
    ),
    _permission(
        "assets.update",
        "assets",
        "Assets",
        "Update assets",
        "Edit product or bird asset records.",
    ),
    _permission(
        "assets.delete",
        "assets",
        "Assets",
        "Delete assets",
        "Delete product or bird asset records.",
    ),
    _permission(
        "assets.moderate",
        "assets",
        "Assets",
        "Moderate assets",
        "Perform asset moderation actions exposed by admin endpoints.",
    ),
    _permission(
        "platform_products.list",
        "platform_products",
        "Platform Products",
        "List platform products",
        "View platform-owned supply products.",
    ),
    _permission(
        "platform_products.retrieve",
        "platform_products",
        "Platform Products",
        "View platform product details",
        "Open one platform-owned supply product.",
    ),
    _permission(
        "platform_products.create",
        "platform_products",
        "Platform Products",
        "Create platform products",
        "Can create platform-owned supply products.",
    ),
    _permission(
        "platform_products.update",
        "platform_products",
        "Platform Products",
        "Update platform products",
        "Edit platform-owned supply products.",
    ),
    _permission(
        "platform_products.delete",
        "platform_products",
        "Platform Products",
        "Delete platform products",
        "Delete or deactivate platform-owned supply products.",
    ),
    _permission(
        "auctions.list",
        "auctions",
        "Auctions",
        "List auctions",
        "View auction listings.",
    ),
    _permission(
        "auctions.retrieve",
        "auctions",
        "Auctions",
        "View auction details",
        "Open one auction record.",
    ),
    _permission(
        "auctions.cancel",
        "auctions",
        "Auctions",
        "Cancel auctions",
        "Cancel an active auction through the manager flow.",
    ),
    _permission(
        "auctions.close",
        "auctions",
        "Auctions",
        "Close auctions",
        "Manually close an auction and resolve winners.",
    ),
    _permission(
        "orders.list",
        "orders",
        "Orders",
        "List orders",
        "View marketplace orders.",
    ),
    _permission(
        "orders.retrieve",
        "orders",
        "Orders",
        "View order details",
        "Open one marketplace order.",
    ),
    _permission(
        "order_items.list",
        "order_items",
        "Order Items",
        "List order items",
        "View seller order items.",
    ),
    _permission(
        "order_items.retrieve",
        "order_items",
        "Order Items",
        "View order item details",
        "Open one seller order item.",
    ),
    _permission(
        "payments.list",
        "payments",
        "Payments",
        "List payment requests",
        "View manual payment requests.",
    ),
    _permission(
        "payments.retrieve",
        "payments",
        "Payments",
        "View payment details",
        "Open one manual payment request.",
    ),
    _permission(
        "complaints.list",
        "complaints",
        "Complaints",
        "List complaints",
        "View complaint records.",
    ),
    _permission(
        "complaints.retrieve",
        "complaints",
        "Complaints",
        "View complaint details",
        "Open one complaint record.",
    ),
    _permission(
        "complaints.mark_in_review",
        "complaints",
        "Complaints",
        "Mark in review",
        "Move a complaint into review.",
    ),
    _permission(
        "complaints.resolve",
        "complaints",
        "Complaints",
        "Resolve complaints",
        "Resolve an open or in-review complaint.",
    ),
    _permission(
        "complaints.reject",
        "complaints",
        "Complaints",
        "Reject complaints",
        "Reject an open or in-review complaint.",
    ),
    _permission(
        "complaints.reopen",
        "complaints",
        "Complaints",
        "Reopen complaints",
        "Reopen a resolved or rejected complaint.",
    ),
    _permission(
        "packages.list",
        "packages",
        "Packages",
        "List packages",
        "View package plans.",
    ),
    _permission(
        "packages.retrieve",
        "packages",
        "Packages",
        "View package details",
        "Open one package plan.",
    ),
    _permission(
        "packages.create",
        "packages",
        "Packages",
        "Create packages",
        "Create package plans.",
    ),
    _permission(
        "packages.update",
        "packages",
        "Packages",
        "Update packages",
        "Edit package plans.",
    ),
    _permission(
        "packages.delete",
        "packages",
        "Packages",
        "Delete packages",
        "Delete package plans.",
    ),
    _permission(
        "seller_packages.list",
        "seller_packages",
        "Seller Packages",
        "List seller packages",
        "View seller package requests.",
    ),
    _permission(
        "seller_packages.retrieve",
        "seller_packages",
        "Seller Packages",
        "View seller package details",
        "Open one seller package request.",
    ),
    _permission(
        "seller_packages.activate",
        "seller_packages",
        "Seller Packages",
        "Activate seller packages",
        "Activate a seller package request.",
    ),
    _permission(
        "seller_packages.cancel",
        "seller_packages",
        "Seller Packages",
        "Cancel seller packages",
        "Cancel a seller package request.",
    ),
    _permission(
        "promotions.discount_offers.list",
        "promotions.discount_offers",
        "Discount Offers",
        "List discount offers",
        "View admin discount offers.",
    ),
    _permission(
        "promotions.discount_offers.create",
        "promotions.discount_offers",
        "Discount Offers",
        "Create discount offers",
        "Create admin discount offers.",
    ),
    _permission(
        "promotions.discount_offers.activate",
        "promotions.discount_offers",
        "Discount Offers",
        "Activate discount offers",
        "Activate a discount offer.",
    ),
    _permission(
        "promotions.discount_offers.deactivate",
        "promotions.discount_offers",
        "Discount Offers",
        "Deactivate discount offers",
        "Deactivate a discount offer.",
    ),
    _permission(
        "promotions.cashback_offers.list",
        "promotions.cashback_offers",
        "Cashback Offers",
        "List cashback offers",
        "View admin cashback offers.",
    ),
    _permission(
        "promotions.cashback_offers.create",
        "promotions.cashback_offers",
        "Cashback Offers",
        "Create cashback offers",
        "Create admin cashback offers.",
    ),
    _permission(
        "promotions.cashback_offers.activate",
        "promotions.cashback_offers",
        "Cashback Offers",
        "Activate cashback offers",
        "Activate a cashback offer.",
    ),
    _permission(
        "promotions.cashback_offers.deactivate",
        "promotions.cashback_offers",
        "Cashback Offers",
        "Deactivate cashback offers",
        "Deactivate a cashback offer.",
    ),
    _permission(
        "cashback.transactions.list",
        "cashback.transactions",
        "Cashback Transactions",
        "List cashback transactions",
        "View customer cashback transactions.",
    ),
    _permission(
        "cashback.balance.retrieve",
        "cashback",
        "Cashback",
        "View cashback balance",
        "View a customer's cashback balance.",
    ),
    _permission(
        "cashback.adjustments.create",
        "cashback.adjustments",
        "Cashback Adjustments",
        "Create cashback adjustments",
        "Create manual cashback adjustments.",
    ),
    _permission(
        "races.list",
        "races",
        "Races",
        "List races",
        "View races.",
    ),
    _permission(
        "races.retrieve",
        "races",
        "Races",
        "View race details",
        "Open one race.",
    ),
    _permission(
        "races.create",
        "races",
        "Races",
        "Create races",
        "Create race records.",
    ),
    _permission(
        "races.update",
        "races",
        "Races",
        "Update races",
        "Edit race records.",
    ),
    _permission(
        "races.delete",
        "races",
        "Races",
        "Delete races",
        "Delete race records.",
    ),
    _permission(
        "race_results.list",
        "race_results",
        "Race Results",
        "List race results",
        "View race results.",
    ),
    _permission(
        "race_results.retrieve",
        "race_results",
        "Race Results",
        "View race result details",
        "Open one race result.",
    ),
    _permission(
        "race_results.create",
        "race_results",
        "Race Results",
        "Create race results",
        "Create race result records.",
    ),
    _permission(
        "race_results.update",
        "race_results",
        "Race Results",
        "Update race results",
        "Edit race result records.",
    ),
    _permission(
        "race_results.delete",
        "race_results",
        "Race Results",
        "Delete race results",
        "Delete race result records.",
    ),
    _permission(
        "pedigrees.documents.list",
        "pedigrees.documents",
        "Pedigree Documents",
        "List pedigree documents",
        "View pedigree OCR documents.",
    ),
    _permission(
        "pedigrees.documents.retrieve",
        "pedigrees.documents",
        "Pedigree Documents",
        "View pedigree documents",
        "Open one pedigree OCR document.",
    ),
    _permission(
        "pedigrees.documents.review",
        "pedigrees.documents",
        "Pedigree Documents",
        "Review pedigree documents",
        "Review corrected OCR extraction fields.",
    ),
    _permission(
        "feed.blocks.list",
        "feed.blocks",
        "Feed Blocks",
        "List feed blocks",
        "View seller/customer block records exposed by moderation endpoints.",
    ),
    _permission(
        "feed.blocks.remove",
        "feed.blocks",
        "Feed Blocks",
        "Remove feed blocks",
        "Remove seller/customer block records exposed by moderation endpoints.",
    ),
    _permission(
        "feed.sellers.list",
        "feed.sellers",
        "Feed Sellers",
        "List feed sellers",
        "View seller feed entries.",
    ),
    _permission(
        "feed.auctions.list",
        "feed.auctions",
        "Feed Auctions",
        "List auction feed",
        "View auction feed entries.",
    ),
    _permission(
        "feed.market.list",
        "feed.market",
        "Feed Market",
        "List market feed",
        "View market feed entries.",
    ),
    _permission(
        "ratings.list",
        "ratings",
        "Ratings",
        "List ratings",
        "View ratings and comments.",
    ),
    _permission(
        "ratings.retrieve",
        "ratings",
        "Ratings",
        "View rating details",
        "Open one rating or comment record.",
    ),
    _permission(
        "ratings.delete",
        "ratings",
        "Ratings",
        "Delete ratings",
        "Delete rating or comment records.",
    ),
    _permission(
        "comments.list",
        "comments",
        "Comments",
        "List comments",
        "View comment records.",
    ),
    _permission(
        "comments.retrieve",
        "comments",
        "Comments",
        "View comment details",
        "Open one comment record.",
    ),
    _permission(
        "comments.delete",
        "comments",
        "Comments",
        "Delete comments",
        "Delete comment records.",
    ),
    _permission(
        "notifications.list",
        "notifications",
        "Notifications",
        "List notifications",
        "View notification records for the active profile.",
    ),
    _permission(
        "notifications.create",
        "notifications",
        "Notifications",
        "Create notifications",
        "Create one manual in-app notification for a selected profile.",
    ),
    _permission(
        "notifications.retrieve",
        "notifications",
        "Notifications",
        "View notification details",
        "Open one notification record.",
    ),
    _permission(
        "insights.seller_home.list",
        "insights.seller_home",
        "Seller Insights",
        "View seller home insights",
        "View seller home insight summaries when exposed by manager endpoints.",
    ),
)

_CATALOG_BY_CODE = {entry["code"]: entry for entry in MANAGER_PERMISSION_CATALOG}


# Kept for legacy imports and migrations. New authorization does not use levels.
MANAGER_PERMISSION_LEVELS = ("list", "edit", "full_control")


def build_manager_permission_code(module: str, action: str) -> str:
    return f"{module}.{action}"


def split_manager_permission_code(permission_code: str) -> tuple[str, str]:
    try:
        namespace, action = permission_code.rsplit(".", 1)
    except ValueError as exc:
        raise ValidationError(
            {"permissions": [f"Invalid permission code: {permission_code}"]}
        ) from exc
    return namespace, action


def is_manager_permission_allowed(code) -> bool:
    return isinstance(code, str) and code in _CATALOG_BY_CODE


def get_manager_permission_codes() -> set[str]:
    return set(_CATALOG_BY_CODE)


def get_manager_permission_catalog() -> list[dict[str, str]]:
    return [entry.copy() for entry in MANAGER_PERMISSION_CATALOG]


def get_manager_permission_groups() -> list[dict[str, object]]:
    groups: OrderedDict[str, dict[str, object]] = OrderedDict()
    for entry in MANAGER_PERMISSION_CATALOG:
        group = groups.setdefault(
            entry["group_key"],
            {
                "key": entry["group_key"],
                "label": entry["group_label"],
                "permissions": [],
            },
        )
        group["permissions"].append(
            {
                "code": entry["code"],
                "label": entry["label"],
                "description": entry["description"],
            }
        )
    return [
        {
            "key": group["key"],
            "label": group["label"],
            "permissions": [permission.copy() for permission in group["permissions"]],
        }
        for group in groups.values()
    ]


def get_manager_permission_choices() -> list[tuple[str, str]]:
    return [
        (entry["code"], f"{entry['group_label']} - {entry['label']}")
        for entry in MANAGER_PERMISSION_CATALOG
    ]


def validate_manager_permissions(permissions) -> list[str]:
    if permissions is None:
        return []

    if not isinstance(permissions, list):
        raise ValidationError({"permissions": ["Permissions must be a list."]})

    invalid_codes = []

    for permission in permissions:
        if not isinstance(permission, str):
            raise ValidationError(
                {"permissions": ["Each permission must be a string."]}
            )
        if not is_manager_permission_allowed(permission):
            invalid_codes.append(permission)

    if invalid_codes:
        raise ValidationError(
            {
                "permissions": [
                    f"Invalid permission code: {permission}"
                    for permission in invalid_codes
                ]
            }
        )

    return permissions


def normalize_manager_permissions(permissions) -> list[str]:
    validated_permissions = validate_manager_permissions(permissions)
    selected_codes = set(validated_permissions)
    return [
        entry["code"]
        for entry in MANAGER_PERMISSION_CATALOG
        if entry["code"] in selected_codes
    ]


def permission_level_satisfies(
    granted_permission: str, required_permission: str
) -> bool:
    return (
        is_manager_permission_allowed(granted_permission)
        and granted_permission == required_permission
    )
