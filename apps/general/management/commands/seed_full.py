"""
Management command: seed_full

Full demo seed — idempotent, safe to re-run.

Creates
-------
  - 1 superadmin  (+201000000000 / Admin@1234, all manager permissions)
  - 3 catalog packages  (Starter / Professional / Premium)
  - 5 lucky-wheel prize tiers
  - 5 users, each with a SellerProfile (active) + CustomerProfile
  - Active AssetSellerPackage for every seller
  - PointWallet + initial earn transaction for every user
  - WELCOME_SELLER + WELCOME_BUYER badge awards per user
  - 5 lucky-wheel grants (5 attempts each) per profile
  - 3–4 birds per seller  (17 total, ring numbers globally unique)
  - 6 auctions covering all five types: single, multi, pair, breeding, racing
  - Cross-seller bids from customer profiles on active auctions
  - 3 races (2024 + 2025) with ranked results referencing seeded bird ring numbers

Accounts
--------
  Superadmin  +201000000000  Admin@1234
  Sellers/Customers:
    +201000000001  seller1  Test@1234  (EG / EGP)
    +201000000002  seller2  Test@1234  (AE / AED)
    +201000000003  seller3  Test@1234  (SA / SAR)
    +201000000004  seller4  Test@1234  (KW / KWD)
    +201000000005  seller5  Test@1234  (QA / QAR)

Usage
-----
    python manage.py seed_full
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

PASSWORD = "Test@1234"

# ── Catalog data ──────────────────────────────────────────────────────────────

PACKAGES = [
    dict(name="Starter",      description="50 pts — good for new sellers.",     points=50,  price="99.00",  activation_period_days=30, auction_cost=5,  product_cost=5),
    dict(name="Professional", description="200 pts — ideal for active sellers.", points=200, price="299.00", activation_period_days=60, auction_cost=10, product_cost=10),
    dict(name="Premium",      description="500 pts — for high-volume sellers.",  points=500, price="599.00", activation_period_days=90, auction_cost=20, product_cost=20),
]

LUCKY_PRIZES = [
    dict(label="50 PP Coins",           prize_type="pp_coins", weight=20, points_amount=50,  description="Win 50 PP Coins!"),
    dict(label="100 PP Coins",          prize_type="pp_coins", weight=15, points_amount=100, description="Win 100 PP Coins!"),
    dict(label="200 PP Coins",          prize_type="pp_coins", weight=10, points_amount=200, description="Win 200 PP Coins!"),
    dict(label="500 PP Coins",          prize_type="pp_coins", weight=5,  points_amount=500, description="Win 500 PP Coins!"),
    dict(label="Better luck next time", prize_type="no_prize", weight=50, points_amount=None, description="No prize this time. Try again!"),
]

# ── User specs ────────────────────────────────────────────────────────────────

USER_SPECS = [
    dict(phone="+201000000001", username="seller1", country="EG", currency="EGP", nickname="EG Pigeons"),
    dict(phone="+201000000002", username="seller2", country="AE", currency="AED", nickname="Gulf Wings"),
    dict(phone="+201000000003", username="seller3", country="SA", currency="SAR", nickname="Arabian Flyers"),
    dict(phone="+201000000004", username="seller4", country="KW", currency="KWD", nickname="Kuwait Loft"),
    dict(phone="+201000000005", username="seller5", country="QA", currency="QAR", nickname="Doha Pigeons"),
]

# Package assigned to each seller (by USER_SPECS index)
SELLER_PACKAGE_NAMES = ["Starter", "Professional", "Premium", "Starter", "Professional"]

# ── Bird data (indexed by seller, then bird within that seller) ───────────────

SELLER_BIRDS = [
    # seller index 0  (EG)
    [
        dict(name="Sky King",     ring_number="EG-2024-001", gender="male",   colour="White",      stamina_ability="excellent", flying_speed=Decimal("90.00"), achievements="National race top 3 2024",          price=Decimal("2500.00")),
        dict(name="Desert Rose",  ring_number="EG-2024-002", gender="female", colour="Brown",      stamina_ability="verygood",  flying_speed=Decimal("78.20"), achievements="",                                  price=Decimal("1800.00")),
        dict(name="Sand Storm",   ring_number="EG-2024-003", gender="young",  colour="Grey",       stamina_ability="good",      flying_speed=None,             achievements="",                                  price=Decimal("900.00")),
        dict(name="Silver Arrow", ring_number="EG-2024-004", gender="male",   colour="Silver",     stamina_ability="excellent", flying_speed=Decimal("93.00"), achievements="Regional champion 2023 and 2024",   price=Decimal("3500.00")),
    ],
    # seller index 1  (AE)
    [
        dict(name="Gulf Pride",   ring_number="AE-2024-001", gender="male",   colour="White",      stamina_ability="excellent", flying_speed=Decimal("88.00"), achievements="UAE championship winner 2024",       price=Decimal("4000.00")),
        dict(name="Gulf Queen",   ring_number="AE-2024-002", gender="female", colour="Cream",      stamina_ability="verygood",  flying_speed=Decimal("81.50"), achievements="",                                  price=Decimal("2200.00")),
        dict(name="Oasis Flyer",  ring_number="AE-2024-003", gender="male",   colour="Grey-Blue",  stamina_ability="verygood",  flying_speed=Decimal("79.00"), achievements="",                                  price=Decimal("1600.00")),
    ],
    # seller index 2  (SA)
    [
        dict(name="Arabian Wind", ring_number="SA-2024-001", gender="male",   colour="White",      stamina_ability="excellent", flying_speed=Decimal("92.50"), achievements="KSA open champion 2023",            price=Decimal("5000.00")),
        dict(name="Arabian Moon", ring_number="SA-2024-002", gender="female", colour="Brown",      stamina_ability="excellent", flying_speed=Decimal("85.00"), achievements="KSA breed champion 2023",           price=Decimal("3200.00")),
        dict(name="Young Riyadh", ring_number="SA-2024-003", gender="young",  colour="Grey",       stamina_ability="good",      flying_speed=None,             achievements="",                                  price=Decimal("750.00")),
        dict(name="Desert Eagle", ring_number="SA-2024-004", gender="male",   colour="Dark Brown", stamina_ability="verygood",  flying_speed=Decimal("80.00"), achievements="",                                  price=Decimal("1400.00")),
    ],
    # seller index 3  (KW)
    [
        dict(name="Kuwait Star",  ring_number="KW-2024-001", gender="male",   colour="White",      stamina_ability="excellent", flying_speed=Decimal("87.00"), achievements="GCC open champion 2024",            price=Decimal("4500.00")),
        dict(name="Kuwait Pearl", ring_number="KW-2024-002", gender="female", colour="Cream",      stamina_ability="verygood",  flying_speed=Decimal("82.00"), achievements="",                                  price=Decimal("2800.00")),
        dict(name="Bay Glider",   ring_number="KW-2024-003", gender="male",   colour="Blue",       stamina_ability="good",      flying_speed=Decimal("75.00"), achievements="",                                  price=Decimal("1200.00")),
    ],
    # seller index 4  (QA)
    [
        dict(name="Doha Racer",   ring_number="QA-2024-001", gender="male",   colour="White",      stamina_ability="excellent", flying_speed=Decimal("91.00"), achievements="Qatar national champion 2024",      price=Decimal("6000.00")),
        dict(name="Pearl Diver",  ring_number="QA-2024-002", gender="female", colour="Silver",     stamina_ability="excellent", flying_speed=Decimal("86.00"), achievements="Regional female champion 2024",     price=Decimal("3800.00")),
        dict(name="Lusail Wing",  ring_number="QA-2024-003", gender="young",  colour="White",      stamina_ability="verygood",  flying_speed=None,             achievements="",                                  price=Decimal("1100.00")),
    ],
]

# ── Auction specs ─────────────────────────────────────────────────────────────
#
# items  — list of dict(bird_idx, paired_idx, starting_price)
#          bird_idx / paired_idx are indices into SELLER_BIRDS[seller_idx]
#
# bids   — list of dict(item_idx, bidder_idx, amount)
#          item_idx  : index into the items list (default 0)
#          bidder_idx: index into users[] — that user's CustomerProfile bids

AUCTION_SPECS = [
    dict(
        seller_idx=0,
        auction_type="single",
        title="Sky King — Open Auction",
        description="Champion-grade male pigeon. National race top 3 in 2024. Starting at 1,000 EGP.",
        days_before_start=0, hours_before_start=6,
        days_until_end=3,
        status="active",
        min_bid_increment=Decimal("100.00"),
        buy_now_enabled=True,
        buy_now_price=Decimal("3000.00"),
        tags="champion,race,male,EG",
        items=[dict(bird_idx=0, paired_idx=None, starting_price=Decimal("1000.00"))],
        bids=[
            dict(item_idx=0, bidder_idx=1, amount=Decimal("1100.00")),
            dict(item_idx=0, bidder_idx=2, amount=Decimal("1250.00")),
            dict(item_idx=0, bidder_idx=3, amount=Decimal("1400.00")),
        ],
    ),
    dict(
        seller_idx=0,
        auction_type="pair",
        title="Desert Pair — Breeding Couple (Ended)",
        description="Male + female pair ready for breeding. Excellent genetics.",
        days_before_start=10, hours_before_start=0,
        days_until_end=-2,
        status="ended",
        min_bid_increment=Decimal("150.00"),
        buy_now_enabled=False,
        buy_now_price=None,
        tags="pair,breeding,EG",
        items=[dict(bird_idx=1, paired_idx=2, starting_price=Decimal("2000.00"))],
        bids=[
            dict(item_idx=0, bidder_idx=4, amount=Decimal("2200.00")),
        ],
    ),
    dict(
        seller_idx=1,
        auction_type="multi",
        title="Gulf Wings Collection — 3 Birds",
        description="Three premium Gulf pigeons — bid on each independently. UAE championship lineage.",
        days_before_start=0, hours_before_start=12,
        days_until_end=5,
        status="active",
        min_bid_increment=Decimal("200.00"),
        buy_now_enabled=False,
        buy_now_price=None,
        tags="multi,Gulf,UAE,collection",
        items=[
            dict(bird_idx=0, paired_idx=None, starting_price=Decimal("2000.00")),
            dict(bird_idx=1, paired_idx=None, starting_price=Decimal("1200.00")),
            dict(bird_idx=2, paired_idx=None, starting_price=Decimal("800.00")),
        ],
        bids=[
            dict(item_idx=0, bidder_idx=0, amount=Decimal("2200.00")),
            dict(item_idx=0, bidder_idx=2, amount=Decimal("2500.00")),
            dict(item_idx=1, bidder_idx=3, amount=Decimal("1400.00")),
        ],
    ),
    dict(
        seller_idx=2,
        auction_type="breeding",
        title="Arabian Champions Breeding Pair",
        description="Top-tier breeding pair — both championship lineage. Rare opportunity.",
        days_before_start=0, hours_before_start=2,
        days_until_end=7,
        status="active",
        min_bid_increment=Decimal("500.00"),
        buy_now_enabled=True,
        buy_now_price=Decimal("12000.00"),
        tags="breeding,champion,SA,rare",
        items=[dict(bird_idx=0, paired_idx=1, starting_price=Decimal("5000.00"))],
        bids=[
            dict(item_idx=0, bidder_idx=0, amount=Decimal("5500.00")),
            dict(item_idx=0, bidder_idx=4, amount=Decimal("6200.00")),
        ],
    ),
    dict(
        seller_idx=3,
        auction_type="racing",
        title="Kuwait Star — Racing Champion",
        description="GCC open champion 2024. Perfect for racing loft buyers.",
        days_before_start=0, hours_before_start=1,
        days_until_end=2,
        status="active",
        min_bid_increment=Decimal("250.00"),
        buy_now_enabled=False,
        buy_now_price=None,
        tags="racing,champion,KW",
        items=[dict(bird_idx=0, paired_idx=None, starting_price=Decimal("3000.00"))],
        bids=[
            dict(item_idx=0, bidder_idx=1, amount=Decimal("3250.00")),
            dict(item_idx=0, bidder_idx=2, amount=Decimal("3600.00")),
            dict(item_idx=0, bidder_idx=4, amount=Decimal("4000.00")),
        ],
    ),
    dict(
        seller_idx=4,
        auction_type="single",
        title="Doha Racer — Qatar National Champion (Ended)",
        description="Qatar national champion 2024. Ended auction — history reference.",
        days_before_start=20, hours_before_start=0,
        days_until_end=-5,
        status="ended",
        min_bid_increment=Decimal("500.00"),
        buy_now_enabled=True,
        buy_now_price=Decimal("9000.00"),
        tags="champion,QA,racing",
        items=[dict(bird_idx=0, paired_idx=None, starting_price=Decimal("4000.00"))],
        bids=[
            dict(item_idx=0, bidder_idx=0, amount=Decimal("4500.00")),
            dict(item_idx=0, bidder_idx=1, amount=Decimal("5000.00")),
            dict(item_idx=0, bidder_idx=2, amount=Decimal("5800.00")),
            dict(item_idx=0, bidder_idx=3, amount=Decimal("6500.00")),
        ],
    ),
]

# ── Race data ─────────────────────────────────────────────────────────────────
#
# days_ago              : how many days before today the race was released
# arrival_offset_hours  : hours after release_datetime the bird arrived

RACE_SPECS = [
    dict(
        title="Cairo Classic 2024",
        station_name="Cairo Racing Station",
        season_year=2024,
        days_ago=90,
        weather_condition="Clear skies, light NW wind 15 km/h, visibility excellent",
        total_birds=10,
        competitors_count=5,
        planned_distance_km=Decimal("500.000"),
        coordinate_x="30.0444",
        coordinate_y="31.2357",
        notes="Annual classic race from Aswan to Cairo.",
        results=[
            dict(rank=1, competitor_name="محمد علي",      bird_ring_number="EG-2024-001", distance_km=Decimal("500.000"), speed=Decimal("90.500"), arrival_offset_hours=5.5,  basket_number=12),
            dict(rank=2, competitor_name="أحمد حسين",     bird_ring_number="EG-2024-004", distance_km=Decimal("500.000"), speed=Decimal("88.200"), arrival_offset_hours=5.7,  basket_number=4),
            dict(rank=3, competitor_name="عبدالله محمد",   bird_ring_number="SA-2024-001", distance_km=Decimal("500.000"), speed=Decimal("85.700"), arrival_offset_hours=5.9,  basket_number=7),
            dict(rank=4, competitor_name="خالد عمر",      bird_ring_number="KW-2024-001", distance_km=Decimal("500.000"), speed=Decimal("82.100"), arrival_offset_hours=6.1,  basket_number=3),
            dict(rank=5, competitor_name="يوسف أحمد",     bird_ring_number="QA-2024-001", distance_km=Decimal("500.000"), speed=Decimal("79.800"), arrival_offset_hours=6.3,  basket_number=9),
        ],
    ),
    dict(
        title="Gulf Open 2024",
        station_name="Dubai Racing Club",
        season_year=2024,
        days_ago=60,
        weather_condition="Partly cloudy, SE wind 10 km/h, temperature 28°C",
        total_birds=8,
        competitors_count=4,
        planned_distance_km=Decimal("350.000"),
        coordinate_x="25.2048",
        coordinate_y="55.2708",
        notes="Gulf regional open race, hosted by Dubai Racing Club.",
        results=[
            dict(rank=1, competitor_name="Ali Hassan",      bird_ring_number="AE-2024-001", distance_km=Decimal("350.000"), speed=Decimal("95.200"), arrival_offset_hours=3.7,  basket_number=1),
            dict(rank=2, competitor_name="Omar Khalid",     bird_ring_number="QA-2024-002", distance_km=Decimal("350.000"), speed=Decimal("91.500"), arrival_offset_hours=3.8,  basket_number=5),
            dict(rank=3, competitor_name="Fahad Al-Rashid", bird_ring_number="KW-2024-002", distance_km=Decimal("350.000"), speed=Decimal("88.700"), arrival_offset_hours=3.9,  basket_number=8),
            dict(rank=4, competitor_name="Ahmad Al-Farsi",  bird_ring_number="SA-2024-002", distance_km=Decimal("350.000"), speed=Decimal("84.300"), arrival_offset_hours=4.2,  basket_number=2),
        ],
    ),
    dict(
        title="National Championship 2025",
        station_name="International Racing Federation — Cairo",
        season_year=2025,
        days_ago=14,
        weather_condition="Clear, light NE wind 8 km/h, perfect racing conditions",
        total_birds=15,
        competitors_count=8,
        planned_distance_km=Decimal("750.000"),
        coordinate_x="30.0444",
        coordinate_y="31.2357",
        notes="International federation national championship across Arab countries.",
        results=[
            dict(rank=1, competitor_name="عبدالله محمد",   bird_ring_number="SA-2024-001", distance_km=Decimal("750.000"), speed=Decimal("93.800"), arrival_offset_hours=8.0,  basket_number=3),
            dict(rank=2, competitor_name="محمد علي",       bird_ring_number="EG-2024-001", distance_km=Decimal("750.000"), speed=Decimal("90.100"), arrival_offset_hours=8.3,  basket_number=7),
            dict(rank=3, competitor_name="يوسف أحمد",     bird_ring_number="QA-2024-001", distance_km=Decimal("750.000"), speed=Decimal("88.900"), arrival_offset_hours=8.4,  basket_number=1),
            dict(rank=4, competitor_name="Ali Hassan",      bird_ring_number="AE-2024-001", distance_km=Decimal("750.000"), speed=Decimal("85.600"), arrival_offset_hours=8.8,  basket_number=11),
            dict(rank=5, competitor_name="خالد عمر",       bird_ring_number="KW-2024-001", distance_km=Decimal("750.000"), speed=Decimal("82.400"), arrival_offset_hours=9.1,  basket_number=4),
            dict(rank=6, competitor_name="أحمد حسين",      bird_ring_number="EG-2024-004", distance_km=Decimal("750.000"), speed=Decimal("79.200"), arrival_offset_hours=9.5,  basket_number=9),
            dict(rank=7, competitor_name="Ahmad Al-Farsi",  bird_ring_number="SA-2024-002", distance_km=Decimal("750.000"), speed=Decimal("76.100"), arrival_offset_hours=9.9,  basket_number=6),
            dict(rank=8, competitor_name="Fatima Al-Zahra", bird_ring_number="AE-2024-002", distance_km=Decimal("750.000"), speed=Decimal("72.800"), arrival_offset_hours=10.3, basket_number=14),
        ],
    ),
]


# ── Non-bird product pool (Supply / Accessory / Feed / Supplement) ────────────
#
# 5 items per type.  Each seller gets a rotating window of 3 from each pool,
# so products are shared across sellers (realistic marketplace behaviour).
# Rotation: seller[i] -> indices [i%5, (i+1)%5, (i+2)%5]

PRODUCT_POOL = {
    "supply": [
        dict(title="Racing Loft - 10 Section Wood",          description="Handcrafted wooden racing loft, 10 sections, pressure-treated, easy assembly.",                         count=1,  price=Decimal("850.00")),
        dict(title="Transport Basket - 12 Bird Plastic",     description="Durable ventilated plastic basket for 12 birds, approved for official races.",                           count=5,  price=Decimal("45.00")),
        dict(title="Nesting Box Set - 6 Pairs Wood",         description="Smooth-finish wooden nesting boxes, set of 6 pairs, easy to clean and disinfect.",                      count=3,  price=Decimal("120.00")),
        dict(title="Galvanized Wire Mesh Panel 1m x 2m",     description="Rust-resistant galvanized wire mesh for loft construction, 1m x 2m panels.",                            count=10, price=Decimal("25.00")),
        dict(title="Perch Bar Set - 20 Wooden Perches",      description="Natural smooth-wood perch bars, set of 20, suitable for all pigeon sizes.",                              count=4,  price=Decimal("35.00")),
    ],
    "accessory": [
        dict(title="Aluminium Leg Rings 2024 Size 7 - 100pcs", description="Official 2024 season aluminium leg rings, size 7, engraved, pack of 100.",                           count=10, price=Decimal("15.00")),
        dict(title="Electronic Timing Band Set - 10pcs",      description="Electronic timing bands compatible with major racing clock systems, set of 10.",                       count=5,  price=Decimal("200.00")),
        dict(title="Automatic Water Dispenser 5L",             description="Gravity-fed 5L water dispenser with anti-spill base, BPA-free plastic.",                              count=8,  price=Decimal("28.00")),
        dict(title="Drinker Cup Set - 50pcs",                  description="Universal-fit plastic drinker cups, easy clip-on, dishwasher safe, pack of 50.",                      count=20, price=Decimal("18.00")),
        dict(title="Digital Pigeon Racing Clock",              description="Professional digital racing clock, records up to 5,000 arrivals, USB data export.",                   count=2,  price=Decimal("350.00")),
    ],
    "feed": [
        dict(title="Racing Mix Premium - 20kg",               description="High-energy racing blend: barley, corn, wheat, maple peas. Ideal for training season.",                count=50, price=Decimal("40.00")),
        dict(title="Breeding Mix Deluxe - 20kg",              description="Nutrient-rich breeding blend: maple peas, safflower, corn, sunflower. High protein.",                  count=30, price=Decimal("45.00")),
        dict(title="Depuration Mix - 10kg",                   description="Light post-race recovery mix: barley 70%, safflower 30%. Supports fast detox.",                        count=40, price=Decimal("22.00")),
        dict(title="Sports Mix Plus - 25kg",                  description="High-performance sports blend with added B-vitamins, ideal for race season.",                          count=25, price=Decimal("55.00")),
        dict(title="Natural Corn Blend - 10kg",               description="Pure non-GMO sun-dried corn blend, excellent energy supplement between races.",                         count=60, price=Decimal("18.00")),
    ],
    "supplement": [
        dict(title="Vitamin E + Selenium 250ml",              description="Essential vitamin E and selenium liquid supplement, boosts muscle recovery and performance.",          count=30, price=Decimal("25.00")),
        dict(title="Electrolyte Rehydration Powder 500g",     description="Fast-acting electrolyte formula for post-race rehydration, watermelon flavour.",                       count=25, price=Decimal("30.00")),
        dict(title="Probiotics Powder 100g",                  description="Multi-strain probiotic blend for gut health and immunity, mix in drinking water.",                     count=40, price=Decimal("20.00")),
        dict(title="Grit and Minerals Mix 1kg",               description="Essential grit and mineral blend: calcium, magnesium, phosphorus, trace elements.",                   count=35, price=Decimal("15.00")),
        dict(title="Calcium Block 500g",                      description="Pure calcium block for bone density and egg shell quality, long-lasting hardened form.",               count=50, price=Decimal("10.00")),
    ],
}

# ── Promotion offer data ──────────────────────────────────────────────────────

DISCOUNT_OFFERS_DATA = [
    dict(
        title="Welcome Discount",
        description="10% off any purchase for new users.",
        value_type="percentage",
        value=Decimal("10.00"),
        target_type="all",
        trigger_type="onboarding",
        minimum_order_price=Decimal("0.00"),
        priority=10,
    ),
    dict(
        title="Bird Lovers Deal",
        description="15% off any bird purchase. Minimum order 500.",
        value_type="percentage",
        value=Decimal("15.00"),
        target_type="category",
        category="bird",
        trigger_type="manager_grant",
        minimum_order_price=Decimal("500.00"),
        priority=5,
    ),
]

CASHBACK_OFFERS_DATA = [
    dict(
        title="First Purchase Cashback",
        description="5% cashback on your first order (up to 50 back).",
        value_type="percentage",
        value=Decimal("5.00"),
        target_type="all",
        trigger_type="onboarding",
        minimum_order_price=Decimal("0.00"),
        priority=1,
        allowed_redemption_amount=Decimal("50.00"),
    ),
]

# ── Rating / review text pool ─────────────────────────────────────────────────

ASSET_REVIEW_TEXTS = [
    "Excellent pigeon, exactly as described. Highly recommend this seller!",
    "Very good bird, arrived in great condition. Would buy again.",
    "Good quality pigeon, priced fairly. Seller was very responsive.",
    "Outstanding bird! Championship bloodline confirmed. Very impressed.",
    "Amazing pigeon, exceeded my expectations. Top seller!",
    "Healthy and active bird. Ring number verified. Smooth transaction.",
    "Lovely pigeon, beautiful colour and strong stamina. Very satisfied!",
]

SELLER_REVIEW_TEXTS = [
    "Excellent seller! Very professional, pigeon arrived in perfect condition.",
    "Trustworthy seller. Honest about the bird condition. Will buy again.",
    "Good experience. Fast communication and smooth transaction overall.",
    "Great seller! The pigeon matched the description and photos exactly.",
    "Top seller in the region. Highly recommend to all pigeon enthusiasts.",
]

# ═════════════════════════════════════════════════════════════════════════════
class Command(BaseCommand):
    help = (
        "Full seed: superadmin, dual-profile users, packages, points, birds, "
        "auctions (all 5 types), races, promotions, ratings, complaints."
    )

    def handle(self, *args, **options):
        with transaction.atomic():
            self._section("Superadmin & Catalog")
            self._seed_superadmin()
            packages = self._seed_packages()
            self._seed_lucky_wheel_prizes()

            self._section("Users (seller + customer per account)")
            users = self._seed_users()

            self._section("Seller Packages")
            self._seed_seller_packages(users, packages)

            self._section("Points Wallets & Core Badges")
            self._seed_points_and_badges(users)

            self._section("Lucky Wheel Grants")
            self._seed_lucky_wheel_grants(users)

            self._section("Birds")
            birds_by_seller = self._seed_birds(users)

            self._section("Products (supply / accessory / feed / supplement)")
            self._seed_products(users)

            self._section("Auctions & Bids")
            self._seed_auctions(users, birds_by_seller)

            self._section("Races")
            self._seed_races()

            self._section("Extra Activity Badges")
            self._seed_extra_badges(users)

            self._section("Promotions & User Grants")
            self._seed_promotions(users)

            self._section("Ratings & Comments")
            self._seed_ratings(users, birds_by_seller)

            self._section("Payment Requests & Complaints")
            self._seed_payment_requests_and_complaints(users)

        self.stdout.write(self.style.SUCCESS("\nseed_full complete.\n"))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _section(self, label):
        self.stdout.write(f"\n-- {label} {'-' * max(0, 55 - len(label))}")

    def _ok(self, msg):
        self.stdout.write(f"  [+] {msg}")

    def _skip(self, msg):
        self.stdout.write(f"  [ ] {msg}")

    # ── superadmin ────────────────────────────────────────────────────────────

    def _seed_superadmin(self):
        from apps.authentication.models import UserAccount
        from apps.users.manager_permissions import get_manager_permission_codes
        from apps.users.models import ManagerProfile

        user, created = UserAccount.objects.get_or_create(
            phone_number="+201000000000",
            defaults={
                "username": "admin",
                "password": make_password("Admin@1234"),
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if not created and not user.check_password("Admin@1234"):
            user.set_password("Admin@1234")
            user.save(update_fields=["password"])

        all_perms = sorted(get_manager_permission_codes())
        profile, _ = ManagerProfile.objects.get_or_create(
            user=user,
            defaults=dict(activated=True, is_active=True, is_valid=True, permissions=all_perms),
        )
        profile.permissions = all_perms
        profile.activated = True
        profile.is_active = True
        profile.is_valid = True
        profile.save(update_fields=["permissions", "activated", "is_active", "is_valid"])
        self._ok(f"Superadmin +201000000000 / Admin@1234  ({len(all_perms)} perms)")

    # ── packages ──────────────────────────────────────────────────────────────

    def _seed_packages(self):
        from apps.packages.models import Package

        result = {}
        for p in PACKAGES:
            obj = Package.objects.filter(name=p["name"]).first()
            if obj is None:
                obj = Package.objects.create(**p)
                self._ok(f"Package: {p['name']}")
            else:
                self._skip(f"Package exists: {p['name']}")
            result[p["name"]] = obj
        return result

    # ── lucky wheel prizes ────────────────────────────────────────────────────

    def _seed_lucky_wheel_prizes(self):
        from apps.lucky_wheel.models import LuckyWheelPrize

        for p in LUCKY_PRIZES:
            obj = LuckyWheelPrize.objects.filter(label=p["label"]).first()
            if obj is None:
                LuckyWheelPrize.objects.create(**{**p, "is_active": True})
                self._ok(f"Prize: {p['label']}")
            else:
                self._skip(f"Prize exists: {p['label']}")

    # ── users ─────────────────────────────────────────────────────────────────

    def _seed_users(self):
        from apps.authentication.models import UserAccount
        from apps.users.models import CustomerProfile, SellerProfile

        users = []
        for spec in USER_SPECS:
            user, u_created = UserAccount.objects.get_or_create(
                phone_number=spec["phone"],
                defaults=dict(
                    username=spec["username"],
                    password=make_password(PASSWORD),
                    is_active=True,
                ),
            )

            # SellerProfile — the primary active profile for seeding
            seller, s_created = SellerProfile.objects.get_or_create(
                user=user,
                defaults=dict(
                    activated=True,
                    is_active=True,
                    is_valid=True,
                    country=spec["country"],
                    currency=spec["currency"],
                    nickname=spec["nickname"],
                    balance=Decimal("0.00"),
                ),
            )

            # CustomerProfile — secondary, inactive
            customer, c_created = CustomerProfile.objects.get_or_create(
                user=user,
                defaults=dict(
                    activated=True,
                    is_active=False,
                    is_valid=True,
                    country=spec["country"],
                    currency=spec["currency"],
                    anonymous_bid_perk=False,
                    is_golden_bid=False,
                    balance=Decimal("0.00"),
                    cashback_balance=Decimal("0.00"),
                ),
            )

            tag = "Created" if u_created else "Exists "
            self._ok(f"{tag} {spec['phone']}  seller#{seller.pk}  customer#{customer.pk}")
            users.append(dict(user=user, seller=seller, customer=customer, spec=spec))

        return users

    # ── seller packages ───────────────────────────────────────────────────────

    def _seed_seller_packages(self, users, packages):
        from apps.packages.models import AssetSellerPackage, SellerPackageStatus

        now = timezone.now()
        for i, entry in enumerate(users):
            seller = entry["seller"]
            pkg = packages[SELLER_PACKAGE_NAMES[i]]

            exists = AssetSellerPackage.objects.filter(
                owner=seller,
                status=SellerPackageStatus.ACTIVE,
                activated=True,
                deleted__isnull=True,
            ).first()
            if exists:
                self._skip(f"Seller#{seller.pk} already has active package: {exists.package.name}")
                continue

            sp = AssetSellerPackage.objects.create(
                owner=seller,
                package=pkg,
                remaining_points=pkg.points,
                activated=True,
                activated_at=now - timedelta(days=7),
                expires_at=now + timedelta(days=pkg.activation_period_days - 7),
                status=SellerPackageStatus.ACTIVE,
            )
            self._ok(f"Seller#{seller.pk} ->{pkg.name} package (#{sp.pk}, {pkg.points} pts)")

    # ── points wallets & badge awards ─────────────────────────────────────────

    def _seed_points_and_badges(self, users):
        from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
        from apps.loyalty.constants import BadgeType, PointTransactionType

        INITIAL_POINTS = 500

        for entry in users:
            user = entry["user"]
            seller = entry["seller"]
            customer = entry["customer"]

            # Wallet — a signal may have already created it, so get_or_create
            wallet, _ = PointWallet.objects.get_or_create(
                user=user,
                defaults={"balance": 0},
            )

            if wallet.balance == 0:
                wallet.balance = INITIAL_POINTS
                wallet.save(update_fields=["balance"])

                PointTransaction.objects.get_or_create(
                    idempotency_key=f"seed_full:onboarding:{user.pk}",
                    defaults=dict(
                        wallet=wallet,
                        user=user,
                        profile=seller,
                        transaction_type=PointTransactionType.EARN,
                        points=INITIAL_POINTS,
                        balance_after=INITIAL_POINTS,
                        reason="Welcome bonus — seed_full",
                        source_type="onboarding",
                        source_id="seed_full",
                    ),
                )
                self._ok(f"Wallet #{wallet.pk} ->{INITIAL_POINTS} pts  (user #{user.pk})")
            else:
                self._skip(f"Wallet #{wallet.pk} exists  (balance: {wallet.balance})")

            # Welcome badges
            for profile, badge_type, label in [
                (seller,   BadgeType.WELCOME_SELLER, "WELCOME_SELLER"),
                (customer, BadgeType.WELCOME_BUYER,  "WELCOME_BUYER"),
            ]:
                _, created = BadgeAward.objects.get_or_create(
                    profile=profile,
                    badge_type=badge_type,
                    defaults=dict(
                        source_type="onboarding",
                        source_id="seed_full",
                        idempotency_key=f"seed_full:badge:{badge_type}:{profile.pk}",
                    ),
                )
                if created:
                    self._ok(f"Badge {label} ->profile #{profile.pk}")
                else:
                    self._skip(f"Badge {label} exists for profile #{profile.pk}")

    # ── lucky wheel grants ────────────────────────────────────────────────────

    def _seed_lucky_wheel_grants(self, users):
        from apps.lucky_wheel.models import LuckyWheelGrant, LuckyWheelGrantSourceType
        from apps.lucky_wheel.services import LuckyWheelGrantService

        for entry in users:
            for role, profile in [("seller", entry["seller"]), ("customer", entry["customer"])]:
                key = f"seed_full:grant:{role}:{profile.pk}"
                already = LuckyWheelGrant.objects.filter(idempotency_key=key).exists()
                LuckyWheelGrantService.grant_attempt(
                    profile=profile,
                    source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
                    source_id="seed_full",
                    attempts=5,
                    idempotency_key=key,
                )
                if already:
                    self._skip(f"Grant exists for {role} #{profile.pk}")
                else:
                    self._ok(f"5 wheel attempts ->{role} #{profile.pk}")

    # ── birds ─────────────────────────────────────────────────────────────────

    def _seed_birds(self, users):
        from apps.assets.models import Bird

        birds_by_seller = []
        for i, entry in enumerate(users):
            seller = entry["seller"]
            seller_birds = []
            for spec in SELLER_BIRDS[i]:
                bird, created = Bird.objects.get_or_create(
                    ring_number=spec["ring_number"],
                    defaults=dict(
                        owner=seller,
                        title=spec["name"],
                        description=(
                            f"{spec['name']} — {spec['colour']} {spec['gender']} pigeon. "
                            f"{spec['achievements']}"
                        ).rstrip(". "),
                        price=spec["price"],
                        count=1,
                        is_market_listed=True,
                        is_for_auction=True,
                        name=spec["name"],
                        gender=spec["gender"],
                        colour=spec["colour"],
                        stamina_ability=spec["stamina_ability"],
                        flying_speed=spec.get("flying_speed"),
                        achievements=spec.get("achievements", ""),
                    ),
                )
                seller_birds.append(bird)
                if created:
                    self._ok(f"Bird {spec['ring_number']}  {spec['name']}  ->seller#{seller.pk}")
                else:
                    self._skip(f"Bird exists: {spec['ring_number']}")
            birds_by_seller.append(seller_birds)

        return birds_by_seller

    # ── non-bird products ─────────────────────────────────────────────────────

    def _seed_products(self, users):
        from apps.assets.models import Accessory, Feed, Supplement, Supply

        MODEL_MAP = {
            "supply":     Supply,
            "accessory":  Accessory,
            "feed":       Feed,
            "supplement": Supplement,
        }
        WINDOW = 3  # products each seller gets from each pool

        n = len(PRODUCT_POOL["supply"])  # 5

        for i, entry in enumerate(users):
            seller = entry["seller"]
            for asset_type, Model in MODEL_MAP.items():
                pool = PRODUCT_POOL[asset_type]
                # Rotating window: [i%n, (i+1)%n, (i+2)%n]
                indices = [(i + offset) % n for offset in range(WINDOW)]
                for idx in indices:
                    spec = pool[idx]
                    # Idempotency: one listing per (owner, title)
                    if Model.objects.filter(
                        owner=seller,
                        title=spec["title"],
                        deleted__isnull=True,
                    ).exists():
                        self._skip(
                            f"Exists [{asset_type}] seller#{seller.pk}: {spec['title'][:40]}"
                        )
                        continue

                    Model.objects.create(
                        owner=seller,
                        title=spec["title"],
                        description=spec["description"],
                        count=spec["count"],
                        price=spec["price"],
                        is_market_listed=True,
                        is_for_auction=False,
                        status="available",
                    )
                    self._ok(
                        f"[{asset_type:10}] seller#{seller.pk}: {spec['title'][:45]}"
                    )

    # ── auctions & bids ───────────────────────────────────────────────────────

    def _seed_auctions(self, users, birds_by_seller):
        from apps.auctions.models import (
            Auction,
            AuctionItem,
            AuctionItemStatus,
            AuctionStatus,
            Bid,
        )

        now = timezone.now()

        for spec in AUCTION_SPECS:
            seller = users[spec["seller_idx"]]["seller"]

            if Auction.objects.filter(owner=seller, title=spec["title"]).exists():
                self._skip(f"Auction exists: {spec['title'][:55]}")
                continue

            start_time = now - timedelta(
                days=spec["days_before_start"],
                hours=spec["hours_before_start"],
            )
            end_time = now + timedelta(days=spec["days_until_end"])

            auction = Auction.objects.create(
                owner=seller,
                title=spec["title"],
                description=spec["description"],
                status=spec["status"],
                auction_type=spec["auction_type"],
                start_time=start_time,
                end_time=end_time,
                min_bid_increment=spec["min_bid_increment"],
                buy_now_enabled=spec["buy_now_enabled"],
                buy_now_price=spec.get("buy_now_price"),
                tags=spec["tags"],
                is_visible=True,
            )

            # Auction items
            seller_birds = birds_by_seller[spec["seller_idx"]]
            item_status = (
                AuctionItemStatus.ACTIVE
                if spec["status"] == AuctionStatus.ACTIVE
                else AuctionItemStatus.UNSOLD
            )
            created_items = []
            for item_spec in spec["items"]:
                bird = seller_birds[item_spec["bird_idx"]]
                paired_bird = (
                    seller_birds[item_spec["paired_idx"]]
                    if item_spec["paired_idx"] is not None
                    else None
                )
                item = AuctionItem.objects.create(
                    auction=auction,
                    bird=bird,
                    paired_bird=paired_bird,
                    status=item_status,
                    starting_price=item_spec["starting_price"],
                    current_price=item_spec["starting_price"],
                )
                created_items.append(item)

            # Bids — bidder is always the CustomerProfile of the given user index
            for bid_spec in spec.get("bids", []):
                item_idx = bid_spec.get("item_idx", 0)
                item = created_items[item_idx]
                bidder = users[bid_spec["bidder_idx"]]["customer"]
                Bid.objects.create(
                    item=item,
                    bidder=bidder,
                    amount=bid_spec["amount"],
                )
                if bid_spec["amount"] > item.current_price:
                    item.current_price = bid_spec["amount"]
                    item.save(update_fields=["current_price"])

            self._ok(
                f"Auction [{spec['auction_type']:8}][{spec['status']:6}]: {spec['title'][:45]}"
                f"  ({len(spec['items'])} items, {len(spec.get('bids', []))} bids)"
            )

    # ── races ─────────────────────────────────────────────────────────────────

    def _seed_races(self):
        from apps.races.models import Race, RaceResult

        now = timezone.now()

        for spec in RACE_SPECS:
            race, r_created = Race.objects.get_or_create(
                title=spec["title"],
                season_year=spec["season_year"],
                defaults=dict(
                    station_name=spec["station_name"],
                    release_datetime=now - timedelta(days=spec["days_ago"]),
                    weather_condition=spec["weather_condition"],
                    total_birds=spec["total_birds"],
                    competitors_count=spec["competitors_count"],
                    planned_distance_km=spec["planned_distance_km"],
                    coordinate_x=spec["coordinate_x"],
                    coordinate_y=spec["coordinate_y"],
                    notes=spec["notes"],
                ),
            )

            if not r_created:
                self._skip(f"Race exists: {spec['title']}")
                continue

            release_dt = race.release_datetime
            for result_spec in spec["results"]:
                arrival_dt = release_dt + timedelta(hours=result_spec["arrival_offset_hours"])
                RaceResult(
                    race=race,
                    rank=result_spec["rank"],
                    competitor_name=result_spec["competitor_name"],
                    bird_ring_number=result_spec["bird_ring_number"],
                    distance_km=result_spec["distance_km"],
                    speed=result_spec["speed"],
                    arrival_datetime=arrival_dt,
                    basket_number=result_spec.get("basket_number"),
                ).save()

            self._ok(
                f"Race {spec['season_year']} -- {spec['title']}"
                f"  ({len(spec['results'])} results)"
            )

    # ── extra activity badges ─────────────────────────────────────────────────

    def _seed_extra_badges(self, users):
        from apps.loyalty.models import BadgeAward
        from apps.loyalty.constants import BadgeType

        SELLER_BADGES = [
            BadgeType.SELLER_STARTER,
            BadgeType.FIRST_AUCTION_PUBLISHED,
            BadgeType.FIRST_MARKET_ITEM_LISTED,
        ]
        CUSTOMER_BADGES = [
            BadgeType.FIRST_BIDDER,
            BadgeType.POINTS_STARTER,
        ]

        for entry in users:
            for badge_type in SELLER_BADGES:
                key = f"seed_full:badge:{badge_type}:{entry['seller'].pk}"
                if not BadgeAward.objects.filter(
                    profile=entry["seller"], badge_type=badge_type
                ).exists():
                    BadgeAward.objects.create(
                        profile=entry["seller"],
                        badge_type=badge_type,
                        source_type="seed_full",
                        source_id="extra_badges",
                        idempotency_key=key,
                    )
                    self._ok(f"Badge {badge_type} ->seller #{entry['seller'].pk}")
                else:
                    self._skip(f"Badge {badge_type} exists for seller #{entry['seller'].pk}")

            for badge_type in CUSTOMER_BADGES:
                key = f"seed_full:badge:{badge_type}:{entry['customer'].pk}"
                if not BadgeAward.objects.filter(
                    profile=entry["customer"], badge_type=badge_type
                ).exists():
                    BadgeAward.objects.create(
                        profile=entry["customer"],
                        badge_type=badge_type,
                        source_type="seed_full",
                        source_id="extra_badges",
                        idempotency_key=key,
                    )
                    self._ok(f"Badge {badge_type} ->customer #{entry['customer'].pk}")
                else:
                    self._skip(f"Badge {badge_type} exists for customer #{entry['customer'].pk}")

    # ── promotions & user grants ──────────────────────────────────────────────

    def _seed_promotions(self, users):
        from apps.promotions.models import (
            CashbackOffer,
            DiscountOffer,
            UserPromotionGrant,
            UserPromotionGrantStatus,
            UserPromotionGrantType,
        )

        now = timezone.now()

        # Create DiscountOffers
        discount_objs = {}
        for d in DISCOUNT_OFFERS_DATA:
            obj = DiscountOffer.objects.filter(title=d["title"]).first()
            if obj is None:
                obj = DiscountOffer.objects.create(**d)
                self._ok(f"DiscountOffer: {d['title']}")
            else:
                self._skip(f"DiscountOffer exists: {d['title']}")
            discount_objs[d["title"]] = obj

        # Create CashbackOffers
        cashback_objs = {}
        for c in CASHBACK_OFFERS_DATA:
            obj = CashbackOffer.objects.filter(title=c["title"]).first()
            if obj is None:
                obj = CashbackOffer.objects.create(**c)
                self._ok(f"CashbackOffer: {c['title']}")
            else:
                self._skip(f"CashbackOffer exists: {c['title']}")
            cashback_objs[c["title"]] = obj

        welcome_discount = discount_objs["Welcome Discount"]
        bird_deal = discount_objs["Bird Lovers Deal"]
        cashback = cashback_objs["First Purchase Cashback"]

        # Grant promotions to every customer
        for entry in users:
            customer = entry["customer"]
            user = entry["user"]

            grants = [
                dict(
                    key=f"seed_full:promo:welcome_discount:{customer.pk}",
                    grant_type=UserPromotionGrantType.DISCOUNT,
                    discount_offer=welcome_discount,
                    cashback_offer=None,
                    source_type="onboarding",
                    label="Welcome Discount",
                    days=30,
                ),
                dict(
                    key=f"seed_full:promo:bird_deal:{customer.pk}",
                    grant_type=UserPromotionGrantType.DISCOUNT,
                    discount_offer=bird_deal,
                    cashback_offer=None,
                    source_type="manager_grant",
                    label="Bird Lovers Deal",
                    days=60,
                ),
                dict(
                    key=f"seed_full:promo:cashback:{customer.pk}",
                    grant_type=UserPromotionGrantType.CASHBACK,
                    discount_offer=None,
                    cashback_offer=cashback,
                    source_type="onboarding",
                    label="First Purchase Cashback",
                    days=30,
                ),
            ]

            for g in grants:
                if UserPromotionGrant.objects.filter(idempotency_key=g["key"]).exists():
                    self._skip(f"{g['label']} grant exists for customer #{customer.pk}")
                    continue
                UserPromotionGrant.objects.create(
                    user=user,
                    profile=customer,
                    grant_type=g["grant_type"],
                    discount_offer=g["discount_offer"],
                    cashback_offer=g["cashback_offer"],
                    source_type=g["source_type"],
                    source_id="seed_full",
                    status=UserPromotionGrantStatus.ACTIVE,
                    expires_at=now + timedelta(days=g["days"]),
                    idempotency_key=g["key"],
                )
                self._ok(f"{g['label']} grant ->customer #{customer.pk}")

    # ── ratings & comments ────────────────────────────────────────────────────

    def _seed_ratings(self, users, birds_by_seller):
        from apps.ratings.models import AssetComment, AssetRating, SellerComment, SellerRating

        n = len(users)

        for i, entry in enumerate(users):
            rater_customer = entry["customer"]
            # Rotate: customer[i] rates seller[(i+1) % n] and their birds
            j = (i + 1) % n
            rated_seller = users[j]["seller"]
            rated_seller_birds = birds_by_seller[j]

            # Asset ratings — up to 3 birds from the rated seller
            for k, bird in enumerate(rated_seller_birds[:3]):
                if AssetRating.objects.filter(
                    owner=rater_customer, asset=bird, deleted__isnull=True
                ).exists():
                    self._skip(f"AssetRating exists: customer#{rater_customer.pk} on {bird.ring_number}")
                    continue

                stars = [5, 4, 4, 5, 3][i % 5]
                text = ASSET_REVIEW_TEXTS[(i * 3 + k) % len(ASSET_REVIEW_TEXTS)]

                comment = AssetComment.objects.create(
                    owner=rater_customer,
                    asset=bird,
                    text=text,
                    is_rating_comment=True,
                )
                AssetRating.objects.create(
                    owner=rater_customer,
                    asset=bird,
                    stars=stars,
                    comment=comment,
                )
                self._ok(
                    f"AssetRating {stars}* customer#{rater_customer.pk} on {bird.ring_number}"
                )

            # Seller rating — one per (customer, seller) pair
            if SellerRating.objects.filter(
                owner=rater_customer, seller=rated_seller, deleted__isnull=True
            ).exists():
                self._skip(f"SellerRating exists: customer#{rater_customer.pk} on seller#{rated_seller.pk}")
                continue

            stars = [5, 4, 5, 4, 5][i % 5]
            text = SELLER_REVIEW_TEXTS[i % len(SELLER_REVIEW_TEXTS)]

            seller_comment = SellerComment.objects.create(
                owner=rater_customer,
                seller=rated_seller,
                text=text,
                is_rating_comment=True,
            )
            SellerRating.objects.create(
                owner=rater_customer,
                seller=rated_seller,
                stars=stars,
                comment=seller_comment,
            )
            self._ok(
                f"SellerRating {stars}* customer#{rater_customer.pk} on seller#{rated_seller.pk}"
            )

    # ── payment requests & complaints ─────────────────────────────────────────

    def _seed_payment_requests_and_complaints(self, users):
        from apps.auctions.models import Auction, AuctionStatus
        from apps.complaints.models import (
            Complaint,
            ComplaintEvent,
            ComplaintEventType,
            ComplaintStatus,
            ComplaintType,
        )
        from apps.payments.models import AuctionPaymentRequest, PaymentRequestStatus

        seller_pks = [entry["seller"].pk for entry in users]

        ended_auctions = (
            Auction.objects.filter(
                status=AuctionStatus.ENDED,
                owner__in=seller_pks,
                deleted__isnull=True,
            )
            .prefetch_related("items__bids")
        )

        payment_requests = []

        for auction in ended_auctions:
            seller_profile = auction.owner
            for item in auction.items.filter(deleted__isnull=True):
                # Skip if payment request already exists for this item
                try:
                    pr = AuctionPaymentRequest.objects.get(auction_item=item)
                    self._skip(f"PaymentRequest exists for AuctionItem #{item.pk}")
                    payment_requests.append(pr)
                    continue
                except AuctionPaymentRequest.DoesNotExist:
                    pass

                # Use highest bid as the winning amount
                highest_bid = (
                    item.bids.filter(deleted__isnull=True).order_by("-amount").first()
                )
                if highest_bid is None:
                    self._skip(f"No bids on AuctionItem #{item.pk} — skipping")
                    continue

                buyer_profile = highest_bid.bidder
                pr = AuctionPaymentRequest.objects.create(
                    auction_item=item,
                    buyer=buyer_profile,
                    seller=seller_profile,
                    asset=item.bird,
                    amount=highest_bid.amount,
                    status=PaymentRequestStatus.PENDING,
                    buyer_note="Winning bid payment — seed_full",
                )
                payment_requests.append(pr)
                self._ok(
                    f"AuctionPaymentRequest #{pr.pk}: buyer#{buyer_profile.pk}"
                    f" -> seller#{seller_profile.pk}  amount={pr.amount}"
                )

        # One complaint per payment request
        complaint_types = [ComplaintType.OTHER, ComplaintType.POST_SALE]
        for idx, pr in enumerate(payment_requests):
            active_exists = Complaint.objects.filter(
                payment_request=pr,
                status__in=[ComplaintStatus.OPEN, ComplaintStatus.IN_REVIEW],
                deleted__isnull=True,
            ).exists()
            if active_exists:
                self._skip(f"Complaint exists for PaymentRequest #{pr.pk}")
                continue

            ctype = complaint_types[idx % len(complaint_types)]
            complaint = Complaint.objects.create(
                payment_request=pr,
                created_by=pr.buyer,
                buyer=pr.buyer,
                seller=pr.seller,
                complaint_type=ctype,
                status=ComplaintStatus.OPEN,
                title=f"Dispute on auction item #{pr.auction_item.pk}",
                description=(
                    "Buyer reports the bird's condition does not match the listing. "
                    "Requesting admin review and resolution."
                ),
            )
            ComplaintEvent.objects.create(
                complaint=complaint,
                event_type=ComplaintEventType.CREATED,
                actor=pr.buyer,
                message="Complaint filed by winning bidder via seed_full.",
            )
            self._ok(
                f"Complaint #{complaint.pk} ({ctype}) for PaymentRequest #{pr.pk}"
            )
