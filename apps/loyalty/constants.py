from dataclasses import dataclass
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.users.constants import ProfileType


class PointTransactionType(models.TextChoices):
    EARN = "earn", _("Earn")
    SPEND = "spend", _("Spend")
    EXPIRE = "expire", _("Expire")


class RewardSourceType(models.TextChoices):
    ONBOARDING = "onboarding", _("Onboarding")
    LUCKY_WHEEL = "lucky_wheel", _("Lucky Wheel")
    AUCTION_COMPLETED = "auction_completed", _("Auction Completed")
    # Pre-BE-REW-08 market deal rewards; kept for historical ledger rows.
    ORDER_COMPLETED_AMOUNT = "order_completed_amount", _("Order Completed Amount")
    ORDER_COMPLETED_BONUS = "order_completed_bonus", _("Order Completed Bonus")
    DEAL_COMPLETED = "deal_completed", _("Deal Completed")
    DEAL_COMPLETION_BONUS = "deal_completion_bonus", _("Deal Completion Bonus")
    SUCCESSFUL_SALE = "successful_sale", _("Successful Sale")
    MUTUAL_RATING = "mutual_rating", _("Mutual Rating")
    AUCTION_POSTED = "auction_posted", _("Auction Posted")
    ACTIVE_AUCTION_BIDDER = "active_auction_bidder", _("Active Auction Bidder")
    RATING_RECEIVED = "rating_received", _("Rating Received")
    # Pre-BE-REF-01 redeem-time inviter reward; kept for historical rows.
    REFERRAL_ACCEPTED = "referral_accepted", _("Referral Accepted")
    REFERRAL_APPROVED = "referral_approved", _("Referral Approved")
    REFERRAL_FIRST_BID = "referral_first_bid", _("Referral First Bid")
    FIRST_BID_COMPLETED = "first_bid_completed", _("First Bid Completed")
    SEASON_WINNER = "season_winner", _("Season Winner")
    CASHBACK_POINTS_PURCHASE = (
        "cashback_points_purchase",
        _("Cashback Points Purchase"),
    )
    MANAGER_ADJUSTMENT = "manager_adjustment", _("Manager Adjustment")


class BadgeType(models.TextChoices):
    FIRST_ORDER = "first_order", _("First Order")
    POINTS_STARTER = "points_starter", _("Points Starter")
    SELLER_STARTER = "seller_starter", _("Seller Starter")
    WELCOME_BUYER = "WELCOME_BUYER", _("Welcome Buyer")
    WELCOME_SELLER = "WELCOME_SELLER", _("Welcome Seller")
    FIRST_BIDDER = "FIRST_BIDDER", _("First Bidder")
    LAST_MINUTE_FIGHTER = "LAST_MINUTE_FIGHTER", _("Last Minute Fighter")
    AUCTION_PURCHASE_CONFIRMED = (
        "AUCTION_PURCHASE_CONFIRMED",
        _("Auction Purchase Confirmed"),
    )
    MARKET_PURCHASE_CONFIRMED = (
        "MARKET_PURCHASE_CONFIRMED",
        _("Market Purchase Confirmed"),
    )
    REVIEWER_BUYER = "REVIEWER_BUYER", _("Reviewer Buyer")
    LOYAL_FOLLOWER = "LOYAL_FOLLOWER", _("Loyal Follower")
    FIRST_AUCTION_PUBLISHED = (
        "FIRST_AUCTION_PUBLISHED",
        _("First Auction Published"),
    )
    FIRST_MARKET_ITEM_LISTED = (
        "FIRST_MARKET_ITEM_LISTED",
        _("First Market Item Listed"),
    )
    FIRST_AUCTION_SALE_CONFIRMED = (
        "FIRST_AUCTION_SALE_CONFIRMED",
        _("First Auction Sale Confirmed"),
    )
    FIRST_MARKET_SALE_CONFIRMED = (
        "FIRST_MARKET_SALE_CONFIRMED",
        _("First Market Sale Confirmed"),
    )
    TRUSTED_BUYER = "TRUSTED_BUYER", _("Trusted Buyer")
    TRUSTED_SELLER = "TRUSTED_SELLER", _("Trusted Seller")


BADGE_ELIGIBLE_ANY = "any"

# Badge presentation and eligibility only. A badge never implies PP Coins:
# point amounts live in RewardRuleService or the explicit *_POINTS values below,
# and RewardEngine grants points and badges separately.

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
    BadgeType.WELCOME_BUYER: {
        "badge_type": BadgeType.WELCOME_BUYER,
        "name": "Welcome Buyer",
        "description": "Welcome badge for a new customer profile.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "customer_profile_created",
        "criteria_value": {},
        "icon_url": "badges/welcome_buyer.svg",
        "is_active": True,
    },
    BadgeType.WELCOME_SELLER: {
        "badge_type": BadgeType.WELCOME_SELLER,
        "name": "Welcome Seller",
        "description": "Welcome badge for a new seller profile.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "seller_profile_created",
        "criteria_value": {},
        "icon_url": "badges/welcome_seller.svg",
        "is_active": True,
    },
    BadgeType.FIRST_BIDDER: {
        "badge_type": BadgeType.FIRST_BIDDER,
        "name": "First Bidder",
        "description": "Awarded after a customer's first accepted bid.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "bid_placed",
        "criteria_value": {"first_valid_bid": True},
        "icon_url": "badges/first_bidder.svg",
        "is_active": True,
    },
    BadgeType.LAST_MINUTE_FIGHTER: {
        "badge_type": BadgeType.LAST_MINUTE_FIGHTER,
        "name": "Last Minute Fighter",
        "description": "Temporary badge for accepted bids in the final five minutes.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "bid_placed",
        "criteria_value": {"final_minutes": 5, "valid_days": 7},
        "icon_url": "badges/last_minute_fighter.svg",
        "is_active": True,
    },
    BadgeType.AUCTION_PURCHASE_CONFIRMED: {
        "badge_type": BadgeType.AUCTION_PURCHASE_CONFIRMED,
        "name": "Auction Purchase Confirmed",
        "description": "Awarded when a customer completes an auction purchase.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "auction_deal_completed",
        "criteria_value": {},
        "icon_url": "badges/auction_purchase_confirmed.svg",
        "is_active": True,
    },
    BadgeType.MARKET_PURCHASE_CONFIRMED: {
        "badge_type": BadgeType.MARKET_PURCHASE_CONFIRMED,
        "name": "Market Purchase Confirmed",
        "description": "Awarded when a customer completes a market purchase.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "market_deal_completed",
        "criteria_value": {},
        "icon_url": "badges/market_purchase_confirmed.svg",
        "is_active": True,
    },
    BadgeType.REVIEWER_BUYER: {
        "badge_type": BadgeType.REVIEWER_BUYER,
        "name": "Reviewer Buyer",
        "description": "Awarded after a customer's first valid rating.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "rating_created",
        "criteria_value": {},
        "icon_url": "badges/reviewer_buyer.svg",
        "is_active": True,
    },
    BadgeType.LOYAL_FOLLOWER: {
        "badge_type": BadgeType.LOYAL_FOLLOWER,
        "name": "Loyal Follower",
        "description": "Awarded after a customer follows their first seller.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "seller_followed",
        "criteria_value": {},
        "icon_url": "badges/loyal_follower.svg",
        "is_active": True,
    },
    BadgeType.FIRST_AUCTION_PUBLISHED: {
        "badge_type": BadgeType.FIRST_AUCTION_PUBLISHED,
        "name": "First Auction Published",
        "description": "Awarded after a seller publishes their first auction.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "auction_published",
        "criteria_value": {},
        "icon_url": "badges/first_auction_published.svg",
        "is_active": True,
    },
    BadgeType.FIRST_MARKET_ITEM_LISTED: {
        "badge_type": BadgeType.FIRST_MARKET_ITEM_LISTED,
        "name": "First Market Item Listed",
        "description": "Awarded after a seller creates their first market listing.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "market_item_listed",
        "criteria_value": {},
        "icon_url": "badges/first_market_item_listed.svg",
        "is_active": True,
    },
    BadgeType.FIRST_AUCTION_SALE_CONFIRMED: {
        "badge_type": BadgeType.FIRST_AUCTION_SALE_CONFIRMED,
        "name": "First Auction Sale Confirmed",
        "description": "Awarded after a seller's first completed auction sale.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "first_auction_deal_completed",
        "criteria_value": {},
        "icon_url": "badges/first_auction_sale_confirmed.svg",
        "is_active": True,
    },
    BadgeType.FIRST_MARKET_SALE_CONFIRMED: {
        "badge_type": BadgeType.FIRST_MARKET_SALE_CONFIRMED,
        "name": "First Market Sale Confirmed",
        "description": "Awarded after a seller's first completed market sale.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "first_market_deal_completed",
        "criteria_value": {},
        "icon_url": "badges/first_market_sale_confirmed.svg",
        "is_active": True,
    },
    BadgeType.TRUSTED_BUYER: {
        "badge_type": BadgeType.TRUSTED_BUYER,
        "name": "Trusted Buyer",
        "description": "Aggregate badge for customers with ten completed purchases.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "completed_purchase_count",
        "criteria_value": {"threshold": 10, "valid_days": 90},
        "icon_url": "badges/trusted_buyer.svg",
        "is_active": True,
    },
    BadgeType.TRUSTED_SELLER: {
        "badge_type": BadgeType.TRUSTED_SELLER,
        "name": "Trusted Seller",
        "description": "Aggregate badge for sellers with ten completed sales.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "completed_sale_count",
        "criteria_value": {"threshold": 10, "valid_days": 90},
        "icon_url": "badges/trusted_seller.svg",
        "is_active": True,
    },
}


class RewardEventType:
    ACCOUNT_ONBOARDED = "account_onboarded"
    CUSTOMER_PROFILE_CREATED = "customer_profile_created"
    SELLER_PROFILE_CREATED = "seller_profile_created"
    AUCTION_PUBLISHED = "auction_published"
    MARKET_ITEM_LISTED = "market_item_listed"
    BID_PLACED = "bid_placed"
    DEAL_COMPLETED = "deal_completed"
    MUTUAL_RATING_COMPLETED = "mutual_rating_completed"
    REFERRAL_APPROVED = "referral_approved"
    AUCTION_PAYMENT_REQUEST_CREATED = "auction_payment_request_created"
    # Deprecated as events: deal rewards run on DEAL_COMPLETED. The two
    # *_APPROVED values stay because historical reward keys contain them.
    AUCTION_PAYMENT_REQUEST_APPROVED = "auction_payment_request_approved"
    AUCTION_PAYMENT_REQUEST_REJECTED = "auction_payment_request_rejected"
    ORDER_ITEM_APPROVED = "order_item_approved"
    MARKET_PAYMENT_REQUEST_CREATED = "market_payment_request_created"
    MARKET_PAYMENT_REQUEST_APPROVED = "market_payment_request_approved"
    MARKET_PAYMENT_REQUEST_REJECTED = "market_payment_request_rejected"
    RATING_CREATED = "rating_created"
    SELLER_FOLLOWED = "seller_followed"
    COMPLAINT_CREATED = "complaint_created"
    COMPLAINT_RESOLVED = "complaint_resolved"


class RewardRuleCode(models.TextChoices):
    ACCOUNT_ONBOARDING = "ACCOUNT_ONBOARDING", _("Account Onboarding")
    AUCTION_PUBLISHED = "AUCTION_PUBLISHED", _("Auction Published")
    ACTIVE_AUCTION_BIDDER = "ACTIVE_AUCTION_BIDDER", _("Active Auction Bidder")
    DEAL_COMPLETED = "DEAL_COMPLETED", _("Deal Completed")
    DEAL_COMPLETION_BONUS = "DEAL_COMPLETION_BONUS", _("Deal Completion Bonus")
    SUCCESSFUL_SALE = "SUCCESSFUL_SALE", _("Successful Sale")
    POSITIVE_RATING = "POSITIVE_RATING", _("Positive Rating")
    MUTUAL_RATING = "MUTUAL_RATING", _("Mutual Rating")
    REFERRAL_APPROVED = "REFERRAL_APPROVED", _("Referral Approved")
    REFERRAL_FIRST_BID = "REFERRAL_FIRST_BID", _("Referral First Bid")


REWARD_RULE_TUNABLE_FIELDS = (
    "enabled",
    "points",
    "multiplier",
    "threshold",
    "configuration",
)
REWARD_RULE_CONFIGURATION_MAX_KEYS = 20
# DEAL_COMPLETED configuration key prefix for PP per currency unit, e.g.
# "points_per_unit_EGP". Currencies without a key earn no deal points.
DEAL_POINTS_RATE_KEY_PREFIX = "points_per_unit_"


def _reward_rule(
    *,
    points=None,
    multiplier=None,
    threshold=None,
    configuration=None,
    required_fields=(),
    implemented=False,
):
    return {
        "enabled": True,
        "points": points,
        "multiplier": multiplier,
        "threshold": threshold,
        "configuration": configuration or {},
        # Fields a manager may not clear, because the rule needs them to run.
        "required_fields": required_fields,
        # Whether backend code currently reads this rule when awarding.
        "is_implemented": implemented,
    }


# Canonical defaults. RewardRuleConfig rows override them; nothing else should
# hold these values. POSITIVE_RATING is a pending product value, so it has no
# points yet.
DEFAULT_REWARD_RULES = {
    RewardRuleCode.ACCOUNT_ONBOARDING: _reward_rule(
        points=100,
        required_fields=("points",),
        implemented=True,
    ),
    RewardRuleCode.AUCTION_PUBLISHED: _reward_rule(
        points=50,
        required_fields=("points",),
    ),
    RewardRuleCode.ACTIVE_AUCTION_BIDDER: _reward_rule(
        points=50,
        threshold=5,
        required_fields=("points", "threshold"),
        implemented=True,
    ),
    # deal points = floor(amount × rate[currency]) × multiplier, per party.
    RewardRuleCode.DEAL_COMPLETED: _reward_rule(
        multiplier=Decimal("2"),
        configuration={
            f"{DEAL_POINTS_RATE_KEY_PREFIX}EGP": 1,
            f"{DEAL_POINTS_RATE_KEY_PREFIX}USD": 1,
        },
        required_fields=("multiplier",),
        implemented=True,
    ),
    RewardRuleCode.DEAL_COMPLETION_BONUS: _reward_rule(
        points=100,
        required_fields=("points",),
        implemented=True,
    ),
    # Seller only, every completed deal; separate from the first-sale incentive.
    RewardRuleCode.SUCCESSFUL_SALE: _reward_rule(
        points=100,
        required_fields=("points",),
        implemented=True,
    ),
    RewardRuleCode.POSITIVE_RATING: _reward_rule(),
    RewardRuleCode.MUTUAL_RATING: _reward_rule(
        points=20,
        required_fields=("points",),
        implemented=True,
    ),
    RewardRuleCode.REFERRAL_APPROVED: _reward_rule(
        points=30,
        required_fields=("points",),
        implemented=True,
    ),
    RewardRuleCode.REFERRAL_FIRST_BID: _reward_rule(
        points=200,
        required_fields=("points",),
        implemented=True,
    ),
}


# PP Coin amounts that some events pay alongside a badge. They are point
# reward values, not badge configuration: callers pass them explicitly. The
# account onboarding amount is the ACCOUNT_ONBOARDING reward rule.
FIRST_BID_POINTS = 10
LAST_MINUTE_BID_POINTS = 5
AUCTION_PURCHASE_POINTS = 50
MARKET_PURCHASE_POINTS = 30
FIRST_RATING_POINTS = 10
FIRST_SELLER_FOLLOW_POINTS = 5
FIRST_AUCTION_PUBLISHED_POINTS = 25
FIRST_MARKET_ITEM_LISTED_POINTS = 25
FIRST_AUCTION_SALE_POINTS = 75
FIRST_MARKET_SALE_POINTS = 50
TRUSTED_BUYER_POINTS = 100
TRUSTED_SELLER_POINTS = 150

LAST_MINUTE_BID_WINDOW_MINUTES = 5
LAST_MINUTE_BADGE_VALID_DAYS = 7
TRUSTED_BUYER_PURCHASE_THRESHOLD = 10
TRUSTED_SELLER_SALE_THRESHOLD = 10
TRUSTED_BADGE_VALID_DAYS = 90
AWARD_LAST_MINUTE_POINTS_PER_BID = True


# ============================================================================
# BE-LTY-01A: Permanent Buyer & Seller Levels Foundation
# ============================================================================
LEVEL_METRIC_CURRENCY_BLOCKER_CODE = "LEVEL METRIC CURRENCY UNIT — BLOCKED"
LEVEL_BENEFIT_BLOCKER_CODE = "LEVEL BENEFIT — BLOCKED"


@dataclass(frozen=True)
class PermanentLevelSpec:
    """Verified static threshold, level name, and display-only benefit metadata (BE-LTY-01A)."""

    level_number: int
    code: str
    threshold: Decimal
    level_name: str
    benefit_label: str
    benefit_status: str = "BLOCKED"


# Ordered ascending by threshold; evaluation resolves highest inclusive threshold.
SELLER_LEVEL_TABLE: tuple[PermanentLevelSpec, ...] = (
    PermanentLevelSpec(
        level_number=1,
        code="verified_account",
        threshold=Decimal("100"),
        level_name="حساب موثّق",
        benefit_label="شارة + ثقة",
    ),
    PermanentLevelSpec(
        level_number=2,
        code="certified_seller",
        threshold=Decimal("500"),
        level_name="بائع معتمد",
        benefit_label="شارة + ترتيب أعلى",
    ),
    PermanentLevelSpec(
        level_number=3,
        code="golden_seller",
        threshold=Decimal("1000"),
        level_name="بائع ذهبي",
        benefit_label="حجل ذهبي + إبراز",
    ),
    PermanentLevelSpec(
        level_number=4,
        code="elite_seller",
        threshold=Decimal("5000"),
        level_name="بائع نخبة",
        benefit_label="Banner + عمولة أقل",
    ),
    PermanentLevelSpec(
        level_number=5,
        code="legendary_seller",
        threshold=Decimal("10000"),
        level_name="أسطوري",
        benefit_label="توثيق دائم + دعم P",
    ),
)

BUYER_LEVEL_TABLE: tuple[PermanentLevelSpec, ...] = (
    PermanentLevelSpec(
        level_number=1,
        code="trusted_buyer",
        threshold=Decimal("100"),
        level_name="مشتري موثوق",
        benefit_label="مزايدة بدون عربون",
    ),
    PermanentLevelSpec(
        level_number=2,
        code="active_buyer",
        threshold=Decimal("500"),
        level_name="مشتري نشط",
        benefit_label="خصم 2%",
    ),
    PermanentLevelSpec(
        level_number=3,
        code="golden_buyer",
        threshold=Decimal("1000"),
        level_name="مشتري ذهبي",
        benefit_label="كاش باك 1%",
    ),
    PermanentLevelSpec(
        level_number=4,
        code="ep_buyer",
        threshold=Decimal("5000"),
        level_name="مشتري EP",
        benefit_label="مهلة سداد أطول",
    ),
    PermanentLevelSpec(
        level_number=5,
        code="elite_buyer",
        threshold=Decimal("10000"),
        level_name="Elite Buyer",
        benefit_label="دعوات خاصة + شحن مجاني",
    ),
)

