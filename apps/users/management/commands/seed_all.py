"""
python manage.py seed_all

Seeds the production/dev database with:
  - 1 superadmin (Manager)
  - 1 customer user  (matches Flutter sign-in placeholder)
  - 1 seller user
  - 3 packages (Basic / Pro / Elite)

Safe to run multiple times — uses get_or_create throughout.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.authentication.models import UserAccount
from apps.packages.models import Package
from apps.users.constants import CountryOptions, CurrencyOptions
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile


SUPERADMIN = {
    "phone_number": "+201010000001",
    "password": "Pigeon@12345",
    "username": "seed_superadmin",
    "first_name": "Super",
    "last_name": "Admin",
}

CUSTOMER = {
    "phone_number": "+201000000006",
    "password": "Password123!",
    "username": "demo_customer",
    "first_name": "Demo",
    "last_name": "Customer",
}

SELLER = {
    "phone_number": "+201000000007",
    "password": "Password123!",
    "username": "demo_seller",
    "first_name": "Demo",
    "last_name": "Seller",
}

PACKAGES = [
    {
        "name": "Basic",
        "description": "Entry-level package for new sellers.",
        "points": 100,
        "price": Decimal("49.99"),
        "activation_period_days": 30,
        "auction_cost": 10,
        "product_cost": 5,
    },
    {
        "name": "Pro",
        "description": "Professional package with extended quota.",
        "points": 300,
        "price": Decimal("119.99"),
        "activation_period_days": 60,
        "auction_cost": 20,
        "product_cost": 10,
    },
    {
        "name": "Elite",
        "description": "Unlimited-feel package for high-volume sellers.",
        "points": 1000,
        "price": Decimal("299.99"),
        "activation_period_days": 90,
        "auction_cost": 30,
        "product_cost": 15,
    },
]


class Command(BaseCommand):
    help = "Seed superadmin, demo users, and packages."

    def handle(self, *args, **options):
        self._seed_superadmin()
        self._seed_customer()
        self._seed_seller()
        self._seed_packages()
        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    # ── Superadmin ────────────────────────────────────────────────────────────

    def _seed_superadmin(self):
        d = SUPERADMIN
        user, created = UserAccount.objects.get_or_create(
            phone_number=d["phone_number"],
            defaults={
                "username": d["username"],
                "first_name": d["first_name"],
                "last_name": d["last_name"],
                "is_staff": True,
            },
        )
        if created:
            user.set_password(d["password"])
            user.save()

        with transaction.atomic():
            ManagerProfile.objects.get_or_create(
                user=user,
                defaults={
                    "is_active": True,
                    "activated": True,
                    "is_valid": True,
                },
            )

        status = "created" if created else "already exists"
        self.stdout.write(f"  Superadmin ({d['phone_number']}): {status}")

    # ── Customer (Flutter placeholder) ────────────────────────────────────────

    def _seed_customer(self):
        d = CUSTOMER
        user, created = UserAccount.objects.get_or_create(
            phone_number=d["phone_number"],
            defaults={
                "username": d["username"],
                "first_name": d["first_name"],
                "last_name": d["last_name"],
            },
        )
        if created:
            user.set_password(d["password"])
            user.save()

        with transaction.atomic():
            customer, _ = CustomerProfile.objects.get_or_create(
                user=user,
                defaults={
                    "country": CountryOptions.EGYPT,
                    "currency": CurrencyOptions.EGP,
                    "activated": True,
                    "is_active": True,
                    "is_valid": True,
                },
            )
            SellerProfile.objects.get_or_create(
                user=user,
                defaults={
                    "country": CountryOptions.EGYPT,
                    "currency": CurrencyOptions.EGP,
                    "nickname": d["first_name"],
                    "activated": True,
                    "is_active": False,
                    "is_valid": True,
                },
            )

        status = "created" if created else "already exists"
        self.stdout.write(f"  Customer ({d['phone_number']}): {status}")

    # ── Seller ────────────────────────────────────────────────────────────────

    def _seed_seller(self):
        d = SELLER
        user, created = UserAccount.objects.get_or_create(
            phone_number=d["phone_number"],
            defaults={
                "username": d["username"],
                "first_name": d["first_name"],
                "last_name": d["last_name"],
            },
        )
        if created:
            user.set_password(d["password"])
            user.save()

        with transaction.atomic():
            SellerProfile.objects.get_or_create(
                user=user,
                defaults={
                    "country": CountryOptions.EGYPT,
                    "currency": CurrencyOptions.EGP,
                    "nickname": d["first_name"],
                    "activated": True,
                    "is_active": True,
                    "is_valid": True,
                },
            )
            CustomerProfile.objects.get_or_create(
                user=user,
                defaults={
                    "country": CountryOptions.EGYPT,
                    "currency": CurrencyOptions.EGP,
                    "activated": True,
                    "is_active": False,
                    "is_valid": True,
                },
            )

        status = "created" if created else "already exists"
        self.stdout.write(f"  Seller ({d['phone_number']}): {status}")

    # ── Packages ──────────────────────────────────────────────────────────────

    def _seed_packages(self):
        for pkg in PACKAGES:
            _, created = Package.objects.get_or_create(
                name=pkg["name"],
                defaults=pkg,
            )
            status = "created" if created else "already exists"
            self.stdout.write(f"  Package '{pkg['name']}': {status}")
