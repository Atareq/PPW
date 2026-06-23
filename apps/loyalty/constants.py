from django.db import models

from apps.users.constants import ProfileType


class PointTransactionType(models.TextChoices):
    EARN = "earn", "Earn"
    SPEND = "spend", "Spend"
    EXPIRE = "expire", "Expire"


class RewardSourceType(models.TextChoices):
    ONBOARDING = "onboarding", "Onboarding"
    LUCKY_WHEEL = "lucky_wheel", "Lucky Wheel"
    AUCTION_COMPLETED = "auction_completed", "Auction Completed"
    ORDER_COMPLETED_AMOUNT = "order_completed_amount", "Order Completed Amount"
    ORDER_COMPLETED_BONUS = "order_completed_bonus", "Order Completed Bonus"
    AUCTION_POSTED = "auction_posted", "Auction Posted"
    RATING_RECEIVED = "rating_received", "Rating Received"
    REFERRAL_ACCEPTED = "referral_accepted", "Referral Accepted"
    FIRST_BID_COMPLETED = "first_bid_completed", "First Bid Completed"
    SEASON_WINNER = "season_winner", "Season Winner"
    CASHBACK_POINTS_PURCHASE = (
        "cashback_points_purchase",
        "Cashback Points Purchase",
    )
    MANAGER_ADJUSTMENT = "manager_adjustment", "Manager Adjustment"


class BadgeType(models.TextChoices):
    FIRST_ORDER = "first_order", "First Order"
    POINTS_STARTER = "points_starter", "Points Starter"
    SELLER_STARTER = "seller_starter", "Seller Starter"
    WELCOME_BUYER = "WELCOME_BUYER", "Welcome Buyer"
    WELCOME_SELLER = "WELCOME_SELLER", "Welcome Seller"
    FIRST_BIDDER = "FIRST_BIDDER", "First Bidder"
    LAST_MINUTE_FIGHTER = "LAST_MINUTE_FIGHTER", "Last Minute Fighter"
    AUCTION_PURCHASE_CONFIRMED = (
        "AUCTION_PURCHASE_CONFIRMED",
        "Auction Purchase Confirmed",
    )
    MARKET_PURCHASE_CONFIRMED = (
        "MARKET_PURCHASE_CONFIRMED",
        "Market Purchase Confirmed",
    )
    REVIEWER_BUYER = "REVIEWER_BUYER", "Reviewer Buyer"
    LOYAL_FOLLOWER = "LOYAL_FOLLOWER", "Loyal Follower"
    FIRST_AUCTION_PUBLISHED = (
        "FIRST_AUCTION_PUBLISHED",
        "First Auction Published",
    )
    FIRST_MARKET_ITEM_LISTED = (
        "FIRST_MARKET_ITEM_LISTED",
        "First Market Item Listed",
    )
    FIRST_AUCTION_SALE_CONFIRMED = (
        "FIRST_AUCTION_SALE_CONFIRMED",
        "First Auction Sale Confirmed",
    )
    FIRST_MARKET_SALE_CONFIRMED = (
        "FIRST_MARKET_SALE_CONFIRMED",
        "First Market Sale Confirmed",
    )
    TRUSTED_BUYER = "TRUSTED_BUYER", "Trusted Buyer"
    TRUSTED_SELLER = "TRUSTED_SELLER", "Trusted Seller"


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
    BadgeType.WELCOME_BUYER: {
        "badge_type": BadgeType.WELCOME_BUYER,
        "name": "Welcome Buyer",
        "description": "Starter reward for a new customer profile.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "customer_profile_created",
        "criteria_value": {"points": 100},
        "icon_url": "badges/welcome_buyer.svg",
        "is_active": True,
    },
    BadgeType.WELCOME_SELLER: {
        "badge_type": BadgeType.WELCOME_SELLER,
        "name": "Welcome Seller",
        "description": "Starter reward for a new seller profile.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "seller_profile_created",
        "criteria_value": {"points": 100},
        "icon_url": "badges/welcome_seller.svg",
        "is_active": True,
    },
    BadgeType.FIRST_BIDDER: {
        "badge_type": BadgeType.FIRST_BIDDER,
        "name": "First Bidder",
        "description": "Awarded after a customer's first accepted bid.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "bid_placed",
        "criteria_value": {"points": 10, "first_valid_bid": True},
        "icon_url": "badges/first_bidder.svg",
        "is_active": True,
    },
    BadgeType.LAST_MINUTE_FIGHTER: {
        "badge_type": BadgeType.LAST_MINUTE_FIGHTER,
        "name": "Last Minute Fighter",
        "description": "Temporary badge for accepted bids in the final five minutes.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "bid_placed",
        "criteria_value": {"points": 5, "final_minutes": 5, "valid_days": 7},
        "icon_url": "badges/last_minute_fighter.svg",
        "is_active": True,
    },
    BadgeType.AUCTION_PURCHASE_CONFIRMED: {
        "badge_type": BadgeType.AUCTION_PURCHASE_CONFIRMED,
        "name": "Auction Purchase Confirmed",
        "description": "Awarded when an auction payment request is seller-approved.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "auction_payment_request_approved",
        "criteria_value": {"points": 50},
        "icon_url": "badges/auction_purchase_confirmed.svg",
        "is_active": True,
    },
    BadgeType.MARKET_PURCHASE_CONFIRMED: {
        "badge_type": BadgeType.MARKET_PURCHASE_CONFIRMED,
        "name": "Market Purchase Confirmed",
        "description": "Awarded when a market payment request is seller-approved.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "market_payment_request_approved",
        "criteria_value": {"points": 30},
        "icon_url": "badges/market_purchase_confirmed.svg",
        "is_active": True,
    },
    BadgeType.REVIEWER_BUYER: {
        "badge_type": BadgeType.REVIEWER_BUYER,
        "name": "Reviewer Buyer",
        "description": "Awarded after a customer's first valid rating.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "rating_created",
        "criteria_value": {"points": 10},
        "icon_url": "badges/reviewer_buyer.svg",
        "is_active": True,
    },
    BadgeType.LOYAL_FOLLOWER: {
        "badge_type": BadgeType.LOYAL_FOLLOWER,
        "name": "Loyal Follower",
        "description": "Awarded after a customer follows their first seller.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "seller_followed",
        "criteria_value": {"points": 5},
        "icon_url": "badges/loyal_follower.svg",
        "is_active": True,
    },
    BadgeType.FIRST_AUCTION_PUBLISHED: {
        "badge_type": BadgeType.FIRST_AUCTION_PUBLISHED,
        "name": "First Auction Published",
        "description": "Awarded after a seller publishes their first auction.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "auction_published",
        "criteria_value": {"points": 25},
        "icon_url": "badges/first_auction_published.svg",
        "is_active": True,
    },
    BadgeType.FIRST_MARKET_ITEM_LISTED: {
        "badge_type": BadgeType.FIRST_MARKET_ITEM_LISTED,
        "name": "First Market Item Listed",
        "description": "Awarded after a seller creates their first market listing.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "market_item_listed",
        "criteria_value": {"points": 25},
        "icon_url": "badges/first_market_item_listed.svg",
        "is_active": True,
    },
    BadgeType.FIRST_AUCTION_SALE_CONFIRMED: {
        "badge_type": BadgeType.FIRST_AUCTION_SALE_CONFIRMED,
        "name": "First Auction Sale Confirmed",
        "description": "Awarded after a seller approves their first auction payment request.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "auction_payment_request_approved",
        "criteria_value": {"points": 75},
        "icon_url": "badges/first_auction_sale_confirmed.svg",
        "is_active": True,
    },
    BadgeType.FIRST_MARKET_SALE_CONFIRMED: {
        "badge_type": BadgeType.FIRST_MARKET_SALE_CONFIRMED,
        "name": "First Market Sale Confirmed",
        "description": "Awarded after a seller approves their first market payment request.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "market_payment_request_approved",
        "criteria_value": {"points": 50},
        "icon_url": "badges/first_market_sale_confirmed.svg",
        "is_active": True,
    },
    BadgeType.TRUSTED_BUYER: {
        "badge_type": BadgeType.TRUSTED_BUYER,
        "name": "Trusted Buyer",
        "description": "Aggregate badge for customers with ten confirmed purchases.",
        "eligible_profile_type": ProfileType.CUSTOMER,
        "criteria_type": "approved_payment_request_count",
        "criteria_value": {"threshold": 10, "points": 100, "valid_days": 90},
        "icon_url": "badges/trusted_buyer.svg",
        "is_active": True,
    },
    BadgeType.TRUSTED_SELLER: {
        "badge_type": BadgeType.TRUSTED_SELLER,
        "name": "Trusted Seller",
        "description": "Aggregate badge for sellers with ten confirmed sales.",
        "eligible_profile_type": ProfileType.SELLER,
        "criteria_type": "approved_payment_request_count",
        "criteria_value": {"threshold": 10, "points": 150, "valid_days": 90},
        "icon_url": "badges/trusted_seller.svg",
        "is_active": True,
    },
}


class RewardEventType:
    CUSTOMER_PROFILE_CREATED = "customer_profile_created"
    SELLER_PROFILE_CREATED = "seller_profile_created"
    AUCTION_PUBLISHED = "auction_published"
    MARKET_ITEM_LISTED = "market_item_listed"
    BID_PLACED = "bid_placed"
    AUCTION_PAYMENT_REQUEST_CREATED = "auction_payment_request_created"
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


REWARD_POINTS = {
    BadgeType.WELCOME_BUYER: 100,
    BadgeType.WELCOME_SELLER: 100,
    BadgeType.FIRST_BIDDER: 10,
    BadgeType.LAST_MINUTE_FIGHTER: 5,
    BadgeType.AUCTION_PURCHASE_CONFIRMED: 50,
    BadgeType.MARKET_PURCHASE_CONFIRMED: 30,
    BadgeType.REVIEWER_BUYER: 10,
    BadgeType.LOYAL_FOLLOWER: 5,
    BadgeType.FIRST_AUCTION_PUBLISHED: 25,
    BadgeType.FIRST_MARKET_ITEM_LISTED: 25,
    BadgeType.FIRST_AUCTION_SALE_CONFIRMED: 75,
    BadgeType.FIRST_MARKET_SALE_CONFIRMED: 50,
    BadgeType.TRUSTED_BUYER: 100,
    BadgeType.TRUSTED_SELLER: 150,
}

LAST_MINUTE_BID_WINDOW_MINUTES = 5
LAST_MINUTE_BADGE_VALID_DAYS = 7
TRUSTED_BUYER_PURCHASE_THRESHOLD = 10
TRUSTED_SELLER_SALE_THRESHOLD = 10
TRUSTED_BADGE_VALID_DAYS = 90
AWARD_LAST_MINUTE_POINTS_PER_BID = True
