from django.db import models

from apps.users.constants import ProfileType


class PointTransactionType(models.TextChoices):
    EARN = "earn", "Earn"
    SPEND = "spend", "Spend"
    EXPIRE = "expire", "Expire"


class BadgeType(models.TextChoices):
    FIRST_ORDER = "first_order", "First Order"
    POINTS_STARTER = "points_starter", "Points Starter"
    SELLER_STARTER = "seller_starter", "Seller Starter"


BADGE_ELIGIBLE_ANY = "any"

BADGE_DEFINITIONS = {
    BadgeType.FIRST_ORDER: {
        "badge_type": BadgeType.FIRST_ORDER,
        "name": "First Order",
        "description": "Awarded to customer profiles for completing their first order.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "total_completed_orders",
        "criteria_value": {"threshold": 1},
        "icon_url": "",
        "is_active": True,
    },
    BadgeType.POINTS_STARTER: {
        "badge_type": BadgeType.POINTS_STARTER,
        "name": "Points Starter",
        "description": "Awarded when a profile participates in the points program.",
        "eligible_profile_type": BADGE_ELIGIBLE_ANY,
        "criteria_type": "total_points_earned",
        "criteria_value": {"threshold": 100},
        "icon_url": "",
        "is_active": True,
    },
    BadgeType.SELLER_STARTER: {
        "badge_type": BadgeType.SELLER_STARTER,
        "name": "Seller Starter",
        "description": "Awarded to seller profiles for early seller activity.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "manual",
        "criteria_value": {},
        "icon_url": "",
        "is_active": True,
    },
}

