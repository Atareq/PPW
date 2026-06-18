"""
python manage.py seed_assets

Seeds demo assets owned by the demo seller:
  - 5 birds (mix of genders, some with parent links)
  - 2 supplies
  - 2 accessories
  - 2 feeds
  - 2 supplements

Safe to run multiple times — skips assets whose key field already exists.
Depends on seed_all having been run first (needs the demo seller profile).
"""

import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.assets.models import (
    Accessory,
    AssetStatus,
    Bird,
    Feed,
    Supplement,
    Supply,
)
from apps.users.models import SellerProfile

SELLER_PHONE = "+201000000007"

BIRDS = [
    {
        "ring_number": "EG-2024-001",
        "name": "Storm",
        "gender": "male",
        "colour": "Blue Bar",
        "birthday": datetime.date(2024, 3, 10),
        "achievements": "1st place regional race 2024",
        "flying_speed": Decimal("80.50"),
        "stamina_ability": "excellent",
        "title": "Racing Pigeon — Storm",
        "description": "Top-performing male racer with strong endurance.",
        "price": Decimal("1200.00"),
        "is_market_listed": True,
        "is_for_auction": False,
    },
    {
        "ring_number": "EG-2024-002",
        "name": "Luna",
        "gender": "female",
        "colour": "White",
        "birthday": datetime.date(2024, 4, 5),
        "achievements": "Strong breeding female, 3 prize-winning offspring",
        "flying_speed": Decimal("75.00"),
        "stamina_ability": "verygood",
        "title": "Breeding Pigeon — Luna",
        "description": "Excellent breeding female with prize-winning lineage.",
        "price": Decimal("900.00"),
        "is_market_listed": True,
        "is_for_auction": False,
    },
    {
        "ring_number": "EG-2024-003",
        "name": "Blaze",
        "gender": "male",
        "colour": "Red Checker",
        "birthday": datetime.date(2023, 11, 20),
        "achievements": "2nd place national race 2023",
        "flying_speed": Decimal("82.30"),
        "stamina_ability": "excellent",
        "title": "Racing Pigeon — Blaze",
        "description": "Proven racer with top national ranking.",
        "price": Decimal("1500.00"),
        "is_market_listed": False,
        "is_for_auction": True,
    },
    {
        "ring_number": "EG-2025-001",
        "name": "Nova",
        "gender": "young",
        "colour": "Blue Checker",
        "birthday": datetime.date(2025, 1, 15),
        "achievements": "",
        "flying_speed": None,
        "stamina_ability": "good",
        "title": "Young Pigeon — Nova",
        "description": "Young bird with promising genetics from Storm x Luna.",
        "price": Decimal("400.00"),
        "is_market_listed": True,
        "is_for_auction": False,
        "_father_ring": "EG-2024-001",
        "_mother_ring": "EG-2024-002",
    },
    {
        "ring_number": "EG-2025-002",
        "name": "Ash",
        "gender": "young",
        "colour": "Dark Checker",
        "birthday": datetime.date(2025, 2, 3),
        "achievements": "",
        "flying_speed": None,
        "stamina_ability": "good",
        "title": "Young Pigeon — Ash",
        "description": "Young bird from Blaze lineage, high potential.",
        "price": Decimal("350.00"),
        "is_market_listed": True,
        "is_for_auction": False,
        "_father_ring": "EG-2024-003",
    },
]

SUPPLIES = [
    {
        "title": "Racing Band Set (50 pcs)",
        "description": "Lightweight aluminium racing bands, set of 50.",
        "price": Decimal("25.00"),
        "count": 50,
        "is_market_listed": True,
    },
    {
        "title": "Loft Nesting Box",
        "description": "Wooden nesting box, 40x30x30 cm, easy assembly.",
        "price": Decimal("60.00"),
        "count": 10,
        "is_market_listed": True,
    },
]

ACCESSORIES = [
    {
        "title": "Pigeon Carrier Basket",
        "description": "Ventilated wicker basket, fits up to 6 pigeons.",
        "price": Decimal("45.00"),
        "count": 5,
        "is_market_listed": True,
    },
    {
        "title": "GPS Tracker Harness",
        "description": "Lightweight GPS harness for race tracking.",
        "price": Decimal("120.00"),
        "count": 8,
        "is_market_listed": True,
    },
]

FEEDS = [
    {
        "title": "Racing Mix Feed (5 kg)",
        "description": "High-energy blend of maize, peas, and barley.",
        "price": Decimal("18.00"),
        "count": 100,
        "is_market_listed": True,
    },
    {
        "title": "Breeding Mix Feed (5 kg)",
        "description": "Protein-rich mix for breeding season.",
        "price": Decimal("20.00"),
        "count": 80,
        "is_market_listed": True,
    },
]

SUPPLEMENTS = [
    {
        "title": "Electrolyte Powder (500 g)",
        "description": "Post-race recovery electrolytes, dissolves in water.",
        "price": Decimal("15.00"),
        "count": 60,
        "is_market_listed": True,
    },
    {
        "title": "Vitamin B-Complex (250 ml)",
        "description": "Liquid vitamin complex for stamina and health.",
        "price": Decimal("22.00"),
        "count": 40,
        "is_market_listed": True,
    },
]


class Command(BaseCommand):
    help = "Seed demo assets (birds, supplies, accessories, feeds, supplements)."

    def handle(self, *args, **options):
        seller = self._get_seller()
        if not seller:
            self.stdout.write(self.style.ERROR(
                f"Seller profile for {SELLER_PHONE} not found. "
                "Run seed_all first."
            ))
            return

        self._seed_birds(seller)
        self._seed_simple(Supply, SUPPLIES, seller, "Supply")
        self._seed_simple(Accessory, ACCESSORIES, seller, "Accessory")
        self._seed_simple(Feed, FEEDS, seller, "Feed")
        self._seed_simple(Supplement, SUPPLEMENTS, seller, "Supplement")
        self.stdout.write(self.style.SUCCESS("Asset seeding complete."))

    def _get_seller(self):
        return SellerProfile.objects.filter(user__phone_number=SELLER_PHONE).first()

    def _seed_birds(self, seller):
        ring_to_bird = {}

        for data in BIRDS:
            father_ring = data.pop("_father_ring", None)
            mother_ring = data.pop("_mother_ring", None)

            if Bird.objects.filter(ring_number=data["ring_number"]).exists():
                bird = Bird.objects.get(ring_number=data["ring_number"])
                ring_to_bird[data["ring_number"]] = bird
                self.stdout.write(f"  Bird {data['ring_number']}: already exists")
                continue

            bird = Bird(
                owner=seller,
                status=AssetStatus.AVAILABLE,
                **{k: v for k, v in data.items()},
            )
            bird.save()
            ring_to_bird[data["ring_number"]] = bird
            self.stdout.write(f"  Bird {data['ring_number']}: created")

        # Wire up parent links after all birds exist
        for data_ring, bird in ring_to_bird.items():
            updated = False
            for bdata in BIRDS:
                if bdata.get("ring_number") != data_ring:
                    continue
                father_ring = bdata.get("_father_ring")
                mother_ring = bdata.get("_mother_ring")
                if father_ring and ring_to_bird.get(father_ring):
                    bird.father = ring_to_bird[father_ring]
                    updated = True
                if mother_ring and ring_to_bird.get(mother_ring):
                    bird.mother = ring_to_bird[mother_ring]
                    updated = True
            if updated:
                bird.save()

    def _seed_simple(self, Model, items, seller, label):
        for data in items:
            if Model.objects.filter(owner=seller, title=data["title"]).exists():
                self.stdout.write(f"  {label} '{data['title']}': already exists")
                continue
            obj = Model(owner=seller, status=AssetStatus.AVAILABLE, **data)
            obj.save()
            self.stdout.write(f"  {label} '{data['title']}': created")
