from collections import OrderedDict

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils.translation import gettext_lazy as _


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
        _("Seller Profiles"),
        _("List sellers"),
        _("View the seller profile directory."),
    ),
    _permission(
        "users.sellers.retrieve",
        "users.sellers",
        _("Seller Profiles"),
        _("View seller details"),
        _("Open one seller profile record."),
    ),
    _permission(
        "users.sellers.update",
        "users.sellers",
        _("Seller Profiles"),
        _("Update sellers"),
        _("Edit seller profile moderation and correction fields."),
    ),
    _permission(
        "users.customers.list",
        "users.customers",
        _("Customer Profiles"),
        _("List customers"),
        _("View the customer profile directory."),
    ),
    _permission(
        "users.customers.retrieve",
        "users.customers",
        _("Customer Profiles"),
        _("View customer details"),
        _("Open one customer profile record."),
    ),
    _permission(
        "users.customers.update",
        "users.customers",
        _("Customer Profiles"),
        _("Update customers"),
        _("Edit customer profile moderation and correction fields."),
    ),
    _permission(
        "managers.list",
        "managers",
        _("Managers"),
        _("List managers"),
        _("View manager accounts."),
    ),
    _permission(
        "managers.retrieve",
        "managers",
        _("Managers"),
        _("View manager details"),
        _("Open one manager account."),
    ),
    _permission(
        "assets.list",
        "assets",
        _("Assets"),
        _("List assets"),
        _("View birds and market products."),
    ),
    _permission(
        "assets.retrieve",
        "assets",
        _("Assets"),
        _("View asset details"),
        _("Open one bird or product record."),
    ),
    _permission(
        "assets.create",
        "assets",
        _("Assets"),
        _("Create assets"),
        _("Create product or bird asset records."),
    ),
    _permission(
        "assets.update",
        "assets",
        _("Assets"),
        _("Update assets"),
        _("Edit product or bird asset records."),
    ),
    _permission(
        "assets.delete",
        "assets",
        _("Assets"),
        _("Delete assets"),
        _("Delete product or bird asset records."),
    ),
    _permission(
        "assets.moderate",
        "assets",
        _("Assets"),
        _("Moderate assets"),
        _("Perform asset moderation actions exposed by admin endpoints."),
    ),
    _permission(
        "platform_products.list",
        "platform_products",
        _("Platform Products"),
        _("List platform products"),
        _("View platform-owned supply products."),
    ),
    _permission(
        "platform_products.retrieve",
        "platform_products",
        _("Platform Products"),
        _("View platform product details"),
        _("Open one platform-owned supply product."),
    ),
    _permission(
        "platform_products.create",
        "platform_products",
        _("Platform Products"),
        _("Create platform products"),
        _("Can create platform-owned supply products."),
    ),
    _permission(
        "platform_products.update",
        "platform_products",
        _("Platform Products"),
        _("Update platform products"),
        _("Edit platform-owned supply products."),
    ),
    _permission(
        "platform_products.delete",
        "platform_products",
        _("Platform Products"),
        _("Delete platform products"),
        _("Delete or deactivate platform-owned supply products."),
    ),
    _permission(
        "auctions.list",
        "auctions",
        _("Auctions"),
        _("List auctions"),
        _("View auction listings."),
    ),
    _permission(
        "auctions.retrieve",
        "auctions",
        _("Auctions"),
        _("View auction details"),
        _("Open one auction record."),
    ),
    _permission(
        "auctions.cancel",
        "auctions",
        _("Auctions"),
        _("Cancel auctions"),
        _("Cancel an active auction through the manager flow."),
    ),
    _permission(
        "auctions.close",
        "auctions",
        _("Auctions"),
        _("Close auctions"),
        _("Manually close an auction and resolve winners."),
    ),
    _permission(
        "orders.list",
        "orders",
        _("Orders"),
        _("List orders"),
        _("View marketplace orders."),
    ),
    _permission(
        "orders.retrieve",
        "orders",
        _("Orders"),
        _("View order details"),
        _("Open one marketplace order."),
    ),
    _permission(
        "order_items.list",
        "order_items",
        _("Order Items"),
        _("List order items"),
        _("View seller order items."),
    ),
    _permission(
        "order_items.retrieve",
        "order_items",
        _("Order Items"),
        _("View order item details"),
        _("Open one seller order item."),
    ),
    _permission(
        "payments.list",
        "payments",
        _("Payments"),
        _("List payment requests"),
        _("View manual payment requests."),
    ),
    _permission(
        "payments.retrieve",
        "payments",
        _("Payments"),
        _("View payment details"),
        _("Open one manual payment request."),
    ),
    _permission(
        "complaints.list",
        "complaints",
        _("Complaints"),
        _("List complaints"),
        _("View complaint records."),
    ),
    _permission(
        "complaints.retrieve",
        "complaints",
        _("Complaints"),
        _("View complaint details"),
        _("Open one complaint record."),
    ),
    _permission(
        "complaints.mark_in_review",
        "complaints",
        _("Complaints"),
        _("Mark in review"),
        _("Move a complaint into review."),
    ),
    _permission(
        "complaints.resolve",
        "complaints",
        _("Complaints"),
        _("Resolve complaints"),
        _("Resolve an open or in-review complaint."),
    ),
    _permission(
        "complaints.reject",
        "complaints",
        _("Complaints"),
        _("Reject complaints"),
        _("Reject an open or in-review complaint."),
    ),
    _permission(
        "complaints.reopen",
        "complaints",
        _("Complaints"),
        _("Reopen complaints"),
        _("Reopen a resolved or rejected complaint."),
    ),
    _permission(
        "packages.list",
        "packages",
        _("Packages"),
        _("List packages"),
        _("View package plans."),
    ),
    _permission(
        "packages.retrieve",
        "packages",
        _("Packages"),
        _("View package details"),
        _("Open one package plan."),
    ),
    _permission(
        "packages.create",
        "packages",
        _("Packages"),
        _("Create packages"),
        _("Create package plans."),
    ),
    _permission(
        "packages.update",
        "packages",
        _("Packages"),
        _("Update packages"),
        _("Edit package plans."),
    ),
    _permission(
        "packages.delete",
        "packages",
        _("Packages"),
        _("Delete packages"),
        _("Delete package plans."),
    ),
    _permission(
        "seller_packages.list",
        "seller_packages",
        _("Seller Packages"),
        _("List seller packages"),
        _("View seller package requests."),
    ),
    _permission(
        "seller_packages.retrieve",
        "seller_packages",
        _("Seller Packages"),
        _("View seller package details"),
        _("Open one seller package request."),
    ),
    _permission(
        "seller_packages.activate",
        "seller_packages",
        _("Seller Packages"),
        _("Activate seller packages"),
        _("Activate a seller package request."),
    ),
    _permission(
        "seller_packages.cancel",
        "seller_packages",
        _("Seller Packages"),
        _("Cancel seller packages"),
        _("Cancel a seller package request."),
    ),
    _permission(
        "promotions.discount_offers.list",
        "promotions.discount_offers",
        _("Discount Offers"),
        _("List discount offers"),
        _("View admin discount offers."),
    ),
    _permission(
        "promotions.discount_offers.create",
        "promotions.discount_offers",
        _("Discount Offers"),
        _("Create discount offers"),
        _("Create admin discount offers."),
    ),
    _permission(
        "promotions.discount_offers.activate",
        "promotions.discount_offers",
        _("Discount Offers"),
        _("Activate discount offers"),
        _("Activate a discount offer."),
    ),
    _permission(
        "promotions.discount_offers.deactivate",
        "promotions.discount_offers",
        _("Discount Offers"),
        _("Deactivate discount offers"),
        _("Deactivate a discount offer."),
    ),
    _permission(
        "promotions.cashback_offers.list",
        "promotions.cashback_offers",
        _("Cashback Offers"),
        _("List cashback offers"),
        _("View admin cashback offers."),
    ),
    _permission(
        "promotions.cashback_offers.create",
        "promotions.cashback_offers",
        _("Cashback Offers"),
        _("Create cashback offers"),
        _("Create admin cashback offers."),
    ),
    _permission(
        "promotions.cashback_offers.activate",
        "promotions.cashback_offers",
        _("Cashback Offers"),
        _("Activate cashback offers"),
        _("Activate a cashback offer."),
    ),
    _permission(
        "promotions.cashback_offers.deactivate",
        "promotions.cashback_offers",
        _("Cashback Offers"),
        _("Deactivate cashback offers"),
        _("Deactivate a cashback offer."),
    ),
    _permission(
        "cashback.transactions.list",
        "cashback.transactions",
        _("Cashback Transactions"),
        _("List cashback transactions"),
        _("View customer cashback transactions."),
    ),
    _permission(
        "cashback.balance.retrieve",
        "cashback",
        _("Cashback"),
        _("View cashback balance"),
        _("View a customer's cashback balance."),
    ),
    _permission(
        "cashback.adjustments.create",
        "cashback.adjustments",
        _("Cashback Adjustments"),
        _("Create cashback adjustments"),
        _("Create manual cashback adjustments."),
    ),
    _permission(
        "profile_balances.adjust",
        "profile_balances",
        _("Profile Balances"),
        _("Adjust profile balances"),
        _("Create ledger-backed customer or seller balance adjustments."),
    ),
    _permission(
        "lucky_wheel.prizes.list",
        "lucky_wheel.prizes",
        _("Lucky Wheel Prizes"),
        _("List prizes"),
        _("View lucky wheel prize configuration."),
    ),
    _permission(
        "lucky_wheel.prizes.create",
        "lucky_wheel.prizes",
        _("Lucky Wheel Prizes"),
        _("Create prizes"),
        _("Create lucky wheel prize configuration."),
    ),
    _permission(
        "lucky_wheel.prizes.update",
        "lucky_wheel.prizes",
        _("Lucky Wheel Prizes"),
        _("Update prizes"),
        _("Update lucky wheel prize configuration."),
    ),
    _permission(
        "lucky_wheel.spins.list",
        "lucky_wheel.spins",
        _("Lucky Wheel Spins"),
        _("List spins"),
        _("View lucky wheel spin history."),
    ),
    _permission(
        "reward_rules.list",
        "reward_rules",
        _("Reward Rules"),
        _("List reward rules"),
        _("View reward rule configuration."),
    ),
    _permission(
        "reward_rules.retrieve",
        "reward_rules",
        _("Reward Rules"),
        _("View reward rule details"),
        _("View one reward rule configuration."),
    ),
    _permission(
        "reward_rules.update",
        "reward_rules",
        _("Reward Rules"),
        _("Update reward rules"),
        _("Tune values of system-defined reward rules."),
    ),
    _permission(
        "seasons.list",
        "seasons",
        _("Seasons"),
        _("List seasons"),
        _("View reward seasons."),
    ),
    _permission(
        "seasons.create",
        "seasons",
        _("Seasons"),
        _("Create seasons"),
        _("Create reward seasons."),
    ),
    _permission(
        "seasons.update",
        "seasons",
        _("Seasons"),
        _("Update seasons"),
        _("Update reward season configuration."),
    ),
    _permission(
        "seasons.calculate",
        "seasons",
        _("Seasons"),
        _("Calculate winners"),
        _("Calculate season seller insights and winner rewards."),
    ),
    _permission(
        "seasons.finalize",
        "seasons",
        _("Seasons"),
        _("Finalize seasons"),
        _(
            "Finalize a calculated reward season without recalculating rewards."
        ),
    ),
    _permission(
        "seasons.insights.list",
        "seasons.insights",
        _("Season Insights"),
        _("List seller insights"),
        _("View season seller insight snapshots."),
    ),
    _permission(
        "races.list",
        "races",
        _("Races"),
        _("List races"),
        _("View races."),
    ),
    _permission(
        "races.retrieve",
        "races",
        _("Races"),
        _("View race details"),
        _("Open one race."),
    ),
    _permission(
        "races.create",
        "races",
        _("Races"),
        _("Create races"),
        _("Create race records."),
    ),
    _permission(
        "races.update",
        "races",
        _("Races"),
        _("Update races"),
        _("Edit race records."),
    ),
    _permission(
        "races.delete",
        "races",
        _("Races"),
        _("Delete races"),
        _("Delete race records."),
    ),
    _permission(
        "race_results.list",
        "race_results",
        _("Race Results"),
        _("List race results"),
        _("View race results."),
    ),
    _permission(
        "race_results.retrieve",
        "race_results",
        _("Race Results"),
        _("View race result details"),
        _("Open one race result."),
    ),
    _permission(
        "race_results.create",
        "race_results",
        _("Race Results"),
        _("Create race results"),
        _("Create race result records."),
    ),
    _permission(
        "race_results.update",
        "race_results",
        _("Race Results"),
        _("Update race results"),
        _("Edit race result records."),
    ),
    _permission(
        "race_results.delete",
        "race_results",
        _("Race Results"),
        _("Delete race results"),
        _("Delete race result records."),
    ),
    _permission(
        "pedigrees.documents.list",
        "pedigrees.documents",
        _("Pedigree Documents"),
        _("List pedigree documents"),
        _("View pedigree OCR documents."),
    ),
    _permission(
        "pedigrees.documents.retrieve",
        "pedigrees.documents",
        _("Pedigree Documents"),
        _("View pedigree documents"),
        _("Open one pedigree OCR document."),
    ),
    _permission(
        "pedigrees.documents.review",
        "pedigrees.documents",
        _("Pedigree Documents"),
        _("Review pedigree documents"),
        _("Review corrected OCR extraction fields."),
    ),
    _permission(
        "pedigrees.birds.list",
        "pedigrees.birds",
        _("Pedigree Birds"),
        _("List pedigree birds"),
        _("View private pedigree bird records."),
    ),
    _permission(
        "pedigrees.birds.retrieve",
        "pedigrees.birds",
        _("Pedigree Birds"),
        _("View pedigree bird details"),
        _("Open one private pedigree bird record."),
    ),
    _permission(
        "pedigrees.birds.create",
        "pedigrees.birds",
        _("Pedigree Birds"),
        _("Create pedigree birds"),
        _(
            "Create private pedigree bird records where the API supports ownership."
        ),
    ),
    _permission(
        "pedigrees.birds.update",
        "pedigrees.birds",
        _("Pedigree Birds"),
        _("Update pedigree birds"),
        _("Edit private pedigree bird records."),
    ),
    _permission(
        "pedigrees.birds.delete",
        "pedigrees.birds",
        _("Pedigree Birds"),
        _("Delete pedigree birds"),
        _("Delete private pedigree bird records."),
    ),
    _permission(
        "pedigrees.reports.list",
        "pedigrees.reports",
        _("Pedigree Reports"),
        _("List pedigree reports"),
        _("Proxy the external FastReport reports list."),
    ),
    _permission(
        "pedigrees.reports.generate",
        "pedigrees.reports",
        _("Pedigree Reports"),
        _("Generate pedigree reports"),
        _("Synchronously proxy pedigree report generation."),
    ),
    _permission(
        "loft_subscriptions.invoices.list",
        "loft_subscriptions.invoices",
        _("Loft Subscription Invoices"),
        _("List loft subscription invoices"),
        _("View Loft Manager subscription invoices."),
    ),
    _permission(
        "loft_subscriptions.invoices.retrieve",
        "loft_subscriptions.invoices",
        _("Loft Subscription Invoices"),
        _("View loft subscription invoice details"),
        _("Open one Loft Manager subscription invoice."),
    ),
    _permission(
        "loft_subscriptions.payment_requests.list",
        "loft_subscriptions.payment_requests",
        _("Loft Subscription Payments"),
        _("List loft subscription payment requests"),
        _("View Loft Manager subscription payment requests."),
    ),
    _permission(
        "loft_subscriptions.payment_requests.retrieve",
        "loft_subscriptions.payment_requests",
        _("Loft Subscription Payments"),
        _("View loft subscription payment details"),
        _("Open one Loft Manager subscription payment request."),
    ),
    _permission(
        "loft_subscriptions.payment_requests.confirm",
        "loft_subscriptions.payment_requests",
        _("Loft Subscription Payments"),
        _("Confirm loft subscription payments"),
        _("Mark a trusted Loft Manager subscription payment as confirmed."),
    ),
    _permission(
        "feed.blocks.list",
        "feed.blocks",
        _("Feed Blocks"),
        _("List feed blocks"),
        _(
            "View seller/customer block records exposed by moderation endpoints."
        ),
    ),
    _permission(
        "feed.blocks.remove",
        "feed.blocks",
        _("Feed Blocks"),
        _("Remove feed blocks"),
        _(
            "Remove seller/customer block records exposed by moderation endpoints."
        ),
    ),
    _permission(
        "feed.sellers.list",
        "feed.sellers",
        _("Feed Sellers"),
        _("List feed sellers"),
        _("View seller feed entries."),
    ),
    _permission(
        "feed.auctions.list",
        "feed.auctions",
        _("Feed Auctions"),
        _("List auction feed"),
        _("View auction feed entries."),
    ),
    _permission(
        "feed.market.list",
        "feed.market",
        _("Feed Market"),
        _("List market feed"),
        _("View market feed entries."),
    ),
    _permission(
        "ratings.list",
        "ratings",
        _("Ratings"),
        _("List ratings"),
        _("View ratings and comments."),
    ),
    _permission(
        "ratings.retrieve",
        "ratings",
        _("Ratings"),
        _("View rating details"),
        _("Open one rating or comment record."),
    ),
    _permission(
        "ratings.delete",
        "ratings",
        _("Ratings"),
        _("Delete ratings"),
        _("Delete rating or comment records."),
    ),
    _permission(
        "comments.list",
        "comments",
        _("Comments"),
        _("List comments"),
        _("View comment records."),
    ),
    _permission(
        "comments.retrieve",
        "comments",
        _("Comments"),
        _("View comment details"),
        _("Open one comment record."),
    ),
    _permission(
        "comments.delete",
        "comments",
        _("Comments"),
        _("Delete comments"),
        _("Delete comment records."),
    ),
    _permission(
        "notifications.list",
        "notifications",
        _("Notifications"),
        _("List notifications"),
        _("View notification records for the active profile."),
    ),
    _permission(
        "notifications.create",
        "notifications",
        _("Notifications"),
        _("Create notifications"),
        _("Create one manual in-app notification for a selected profile."),
    ),
    _permission(
        "notifications.retrieve",
        "notifications",
        _("Notifications"),
        _("View notification details"),
        _("Open one notification record."),
    ),
    _permission(
        "insights.seller_home.list",
        "insights.seller_home",
        _("Seller Insights"),
        _("View seller home insights"),
        _(
            "View seller home insight summaries when exposed by manager endpoints."
        ),
    ),
)

_CATALOG_BY_CODE = {
    entry["code"]: entry for entry in MANAGER_PERMISSION_CATALOG
}
if len(_CATALOG_BY_CODE) != len(MANAGER_PERMISSION_CATALOG):
    raise ImproperlyConfigured(
        "Duplicate manager permission codes are not allowed."
    )


# Kept for legacy imports and migrations. New authorization does not use levels.
MANAGER_PERMISSION_LEVELS = ("list", "edit", "full_control")


def build_manager_permission_code(module: str, action: str) -> str:
    return f"{module}.{action}"


def split_manager_permission_code(permission_code: str) -> tuple[str, str]:
    try:
        namespace, action = permission_code.rsplit(".", 1)
    except ValueError as exc:
        raise ValidationError(
            {
                "permissions": [
                    _("Invalid permission code: %(permission)s")
                    % {"permission": permission_code}
                ]
            }
        ) from exc
    return namespace, action


def is_manager_permission_allowed(code) -> bool:
    return isinstance(code, str) and code in _CATALOG_BY_CODE


def get_manager_permission_codes() -> set[str]:
    return set(_CATALOG_BY_CODE)


def get_manager_permission_catalog() -> list[dict[str, str]]:
    return [
        {
            "code": entry["code"],
            "group_key": entry["group_key"],
            "group_label": str(entry["group_label"]),
            "label": str(entry["label"]),
            "description": str(entry["description"]),
        }
        for entry in MANAGER_PERMISSION_CATALOG
    ]


def get_manager_permission_groups() -> list[dict[str, object]]:
    groups: OrderedDict[str, dict[str, object]] = OrderedDict()
    for entry in MANAGER_PERMISSION_CATALOG:
        group = groups.setdefault(
            entry["group_key"],
            {
                "key": entry["group_key"],
                "label": str(entry["group_label"]),
                "permissions": [],
            },
        )
        group["permissions"].append(
            {
                "code": entry["code"],
                "label": str(entry["label"]),
                "description": str(entry["description"]),
            }
        )
    return [
        {
            "key": group["key"],
            "label": group["label"],
            "permissions": [
                permission.copy() for permission in group["permissions"]
            ],
        }
        for group in groups.values()
    ]


def get_manager_permission_choices() -> list[tuple[str, str]]:
    return [
        (entry["code"], f"{str(entry['group_label'])} - {str(entry['label'])}")
        for entry in MANAGER_PERMISSION_CATALOG
    ]


def validate_manager_permissions(permissions) -> list[str]:
    if permissions is None:
        return []

    if not isinstance(permissions, list):
        raise ValidationError(
            {"permissions": [_("Permissions must be a list.")]}
        )

    invalid_codes = []

    for permission in permissions:
        if not isinstance(permission, str):
            raise ValidationError(
                {"permissions": [_("Each permission must be a string.")]}
            )
        if not is_manager_permission_allowed(permission):
            invalid_codes.append(permission)

    if invalid_codes:
        raise ValidationError(
            {
                "permissions": [
                    _("Invalid permission code: %(permission)s")
                    % {"permission": permission}
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
