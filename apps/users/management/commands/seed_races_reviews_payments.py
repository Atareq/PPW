"""
python manage.py seed_races_reviews_payments

Seeds:
  Races (2):
    - Spring Classic 2024 (past) — results include demo birds Storm & Blaze
    - Winter Sprint 2025 (recent) — results include Blaze & Nova

  Reviews:
    - AssetRatings on birds (5-star and 4-star) from demo customer
    - AssetComments attached to those ratings
    - SellerRating on demo seller from demo customer

  Payments:
    - MarketPaymentRequest (approved) for the completed order's items
    - MarketPaymentRequest (pending)  for the pending order's items
    - AuctionPaymentRequest (pending) for the active auction bid on Blaze

Depends on seed_all + seed_assets + seed_auctions_orders having run first.
Safe to rerun — skips existing records.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.assets.models import Asset, Bird
from apps.auctions.models import AuctionItem
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.payments.models import (
    AuctionPaymentRequest,
    MarketPaymentRequest,
    PaymentRequestEvent,
    PaymentRequestEventType,
    PaymentRequestStatus,
)
from apps.ratings.models import (
    AssetComment,
    AssetRating,
    SellerComment,
    SellerRating,
)
from apps.races.models import Race, RaceResult
from apps.users.models import CustomerProfile, SellerProfile

SELLER_PHONE = "+201000000007"
CUSTOMER_PHONE = "+201000000006"


class Command(BaseCommand):
    help = "Seed races, reviews, and payments using existing data."

    def handle(self, *args, **options):
        seller = SellerProfile.objects.filter(
            user__phone_number=SELLER_PHONE
        ).first()
        customer = CustomerProfile.objects.filter(
            user__phone_number=CUSTOMER_PHONE
        ).first()

        if not seller or not customer:
            self.stdout.write(self.style.ERROR(
                "Seller or customer not found. Run seed_all first."
            ))
            return

        self._seed_races()
        self._seed_reviews(seller, customer)
        self._seed_payments(seller, customer)
        self.stdout.write(self.style.SUCCESS("Races, reviews, payments seeding complete."))

    # ── Races ─────────────────────────────────────────────────────────────────

    def _seed_races(self):
        now = timezone.now()

        storm = Bird.objects.filter(ring_number="EG-2024-001").first()
        blaze = Bird.objects.filter(ring_number="EG-2024-003").first()
        nova  = Bird.objects.filter(ring_number="EG-2025-001").first()

        self._create_race(
            title="Spring Classic 2024",
            season_year=2024,
            station_name="Cairo Racing Federation — North Station",
            release_datetime=now - timezone.timedelta(days=60),
            weather_condition="Clear sky, light tailwind 12 km/h, temp 22°C",
            total_birds=120,
            competitors_count=35,
            planned_distance_km=Decimal("300.000"),
            coordinate_x="30.0444",
            coordinate_y="31.2357",
            notes="Annual spring classic. Excellent conditions.",
            results=[
                # Our birds placed well
                {"rank": 1, "ring": blaze.ring_number, "name": "Ahmed Saber",       "dist": Decimal("298.450"), "speed": Decimal("82.300"), "offset_h": 3.6,  "basket": 5},
                {"rank": 2, "ring": "SA-2023-088",     "name": "Khalid Mansour",     "dist": Decimal("297.900"), "speed": Decimal("81.750"), "offset_h": 3.65, "basket": 12},
                {"rank": 3, "ring": storm.ring_number, "name": "Demo Seller",        "dist": Decimal("297.100"), "speed": Decimal("80.500"), "offset_h": 3.7,  "basket": 3},
                {"rank": 4, "ring": "EG-2022-044",     "name": "Omar Farouk",        "dist": Decimal("296.500"), "speed": Decimal("79.200"), "offset_h": 3.75, "basket": 8},
                {"rank": 5, "ring": "EG-2023-110",     "name": "Mahmoud Nasser",     "dist": Decimal("295.800"), "speed": Decimal("78.600"), "offset_h": 3.8,  "basket": 17},
            ],
        )

        self._create_race(
            title="Winter Sprint 2025",
            season_year=2025,
            station_name="Cairo Racing Federation — East Station",
            release_datetime=now - timezone.timedelta(days=10),
            weather_condition="Partly cloudy, crosswind 8 km/h, temp 18°C",
            total_birds=85,
            competitors_count=28,
            planned_distance_km=Decimal("200.000"),
            coordinate_x="30.1200",
            coordinate_y="31.4000",
            notes="First race of 2025 season. Young bird category included.",
            results=[
                {"rank": 1, "ring": blaze.ring_number,  "name": "Demo Seller",    "dist": Decimal("199.200"), "speed": Decimal("82.100"), "offset_h": 2.4,  "basket": 4},
                {"rank": 2, "ring": "EG-2023-055",      "name": "Youssef Adel",   "dist": Decimal("198.800"), "speed": Decimal("81.300"), "offset_h": 2.45, "basket": 9},
                {"rank": 3, "ring": nova.ring_number,   "name": "Demo Seller",    "dist": Decimal("198.500"), "speed": Decimal("80.900"), "offset_h": 2.46, "basket": 2},
                {"rank": 4, "ring": "SA-2024-031",      "name": "Faisal Hamdan",  "dist": Decimal("197.900"), "speed": Decimal("79.500"), "offset_h": 2.5,  "basket": 15},
                {"rank": 5, "ring": "EG-2022-077",      "name": "Tamer Sobhi",    "dist": Decimal("197.200"), "speed": Decimal("78.800"), "offset_h": 2.55, "basket": 21},
            ],
        )

    def _create_race(self, title, season_year, station_name, release_datetime,
                     weather_condition, total_birds, competitors_count,
                     planned_distance_km, coordinate_x, coordinate_y, notes, results):
        if Race.objects.filter(title=title, season_year=season_year).exists():
            self.stdout.write(f"  Race '{title}': already exists")
            return

        with transaction.atomic():
            race = Race.objects.create(
                title=title,
                season_year=season_year,
                station_name=station_name,
                release_datetime=release_datetime,
                weather_condition=weather_condition,
                total_birds=total_birds,
                competitors_count=competitors_count,
                planned_distance_km=planned_distance_km,
                coordinate_x=coordinate_x,
                coordinate_y=coordinate_y,
                notes=notes,
            )

            for r in results:
                arrival = release_datetime + timezone.timedelta(hours=r["offset_h"])
                RaceResult.objects.create(
                    race=race,
                    rank=r["rank"],
                    competitor_name=r["name"],
                    bird_ring_number=r["ring"],
                    distance_km=r["dist"],
                    speed=r["speed"],
                    arrival_datetime=arrival,
                    basket_number=r["basket"],
                )

        self.stdout.write(f"  Race '{title}': created ({len(results)} results)")

    # ── Reviews ───────────────────────────────────────────────────────────────

    def _seed_reviews(self, seller, customer):
        ASSET_REVIEWS = [
            {"ring": "EG-2024-001", "stars": 5, "text": "Incredible bird, great speed and temperament. Very happy with Storm."},
            {"ring": "EG-2024-003", "stars": 5, "text": "Blaze is everything advertised — national-level performer. Highly recommended."},
            {"ring": "EG-2024-002", "stars": 4, "text": "Luna is a superb breeder. Already produced strong offspring. Excellent."},
        ]

        for rev in ASSET_REVIEWS:
            bird = Bird.objects.filter(ring_number=rev["ring"]).first()
            if not bird:
                continue
            asset = Asset.objects.filter(pk=bird.asset_ptr_id).first()
            if not asset:
                continue

            if AssetRating.objects.filter(owner=customer, asset=asset).exists():
                self.stdout.write(f"  AssetRating for {rev['ring']}: already exists")
                continue

            with transaction.atomic():
                comment = AssetComment.objects.create(
                    owner=customer,
                    asset=asset,
                    text=rev["text"],
                    is_rating_comment=True,
                )
                AssetRating.objects.create(
                    owner=customer,
                    asset=asset,
                    stars=rev["stars"],
                    comment=comment,
                )
            self.stdout.write(f"  AssetRating for {rev['ring']}: created ({rev['stars']} stars)")

        # Seller rating
        if SellerRating.objects.filter(owner=customer, seller=seller).exists():
            self.stdout.write("  SellerRating: already exists")
        else:
            with transaction.atomic():
                comment = SellerComment.objects.create(
                    owner=customer,
                    seller=seller,
                    text="Excellent seller — birds are exactly as described, "
                         "fast shipping and very communicative.",
                    is_rating_comment=True,
                )
                SellerRating.objects.create(
                    owner=customer,
                    seller=seller,
                    stars=5,
                    comment=comment,
                )
            self.stdout.write("  SellerRating: created (5 stars)")

    # ── Payments ──────────────────────────────────────────────────────────────

    def _seed_payments(self, seller, customer):
        self._seed_market_payments(seller, customer)
        self._seed_auction_payment(seller, customer)

    def _seed_market_payments(self, seller, customer):
        # Approved payments for completed order items
        completed_order = Order.objects.filter(
            owner=customer, status=OrderStatus.COMPLETED
        ).first()
        if completed_order:
            for item in completed_order.items.all():
                if hasattr(item, "payment_request"):
                    self.stdout.write(f"  MarketPayment for order item #{item.pk}: already exists")
                    continue
                self._create_market_payment(
                    buyer=customer,
                    seller=seller,
                    order_item=item,
                    status=PaymentRequestStatus.APPROVED,
                )

        # Pending payment for pending order items
        pending_order = Order.objects.filter(
            owner=customer, status=OrderStatus.PENDING
        ).first()
        if pending_order:
            for item in pending_order.items.all():
                if hasattr(item, "payment_request"):
                    self.stdout.write(f"  MarketPayment for order item #{item.pk}: already exists")
                    continue
                self._create_market_payment(
                    buyer=customer,
                    seller=seller,
                    order_item=item,
                    status=PaymentRequestStatus.PENDING,
                )

    def _create_market_payment(self, buyer, seller, order_item, status):
        now = timezone.now()
        with transaction.atomic():
            pr = MarketPaymentRequest.objects.create(
                buyer=buyer,
                seller=seller,
                asset=order_item.asset,
                amount=order_item.total_price,
                status=status,
                order_item=order_item,
                buyer_note="Payment for market purchase.",
                approved_at=now if status == PaymentRequestStatus.APPROVED else None,
                approved_by=seller if status == PaymentRequestStatus.APPROVED else None,
            )
            PaymentRequestEvent.objects.create(
                payment_request=pr,
                event_type=PaymentRequestEventType.CREATED,
                actor=buyer,
                message="Payment request created via seed.",
            )
            if status == PaymentRequestStatus.APPROVED:
                PaymentRequestEvent.objects.create(
                    payment_request=pr,
                    event_type=PaymentRequestEventType.APPROVED,
                    actor=seller,
                    message="Payment approved via seed.",
                )
        self.stdout.write(
            f"  MarketPayment for order item #{order_item.pk}: created ({status})"
        )

    def _seed_auction_payment(self, seller, customer):
        # Payment for the winning bid on Blaze's auction item
        blaze = Bird.objects.filter(ring_number="EG-2024-003").first()
        if not blaze:
            return

        item = AuctionItem.objects.filter(bird=blaze).first()
        if not item:
            return

        if hasattr(item, "payment_request"):
            self.stdout.write("  AuctionPayment for Blaze: already exists")
            return

        with transaction.atomic():
            pr = AuctionPaymentRequest.objects.create(
                buyer=customer,
                seller=seller,
                asset=blaze,
                amount=Decimal("1300.00"),
                status=PaymentRequestStatus.PENDING,
                auction_item=item,
                buyer_note="Auction bid payment — awaiting seller confirmation.",
            )
            PaymentRequestEvent.objects.create(
                payment_request=pr,
                event_type=PaymentRequestEventType.CREATED,
                actor=customer,
                message="Auction payment request created via seed.",
            )
        self.stdout.write("  AuctionPayment for Blaze: created (pending)")
