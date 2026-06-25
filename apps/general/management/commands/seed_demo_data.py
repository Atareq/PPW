"""
Management command: seed_demo_data

Creates demo customers, packages, lucky-wheel prizes, and grants.
Safe to re-run — all operations are idempotent.

Usage:
    python manage.py seed_demo_data
"""

from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Seed demo data: customers, packages, lucky-wheel prizes and grants"

    def handle(self, *args, **options):
        self._seed_superadmin()
        self._seed_customers()
        self._seed_packages()
        self._seed_lucky_wheel_prizes()
        self._seed_lucky_wheel_grants()
        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    # ── Superadmin ────────────────────────────────────────────────────────────

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
        if not created:
            # Ensure password is set correctly in case account pre-existed
            if not user.check_password("Admin@1234"):
                user.set_password("Admin@1234")
                user.save(update_fields=["password"])

        all_perms = sorted(get_manager_permission_codes())
        profile, profile_created = ManagerProfile.objects.get_or_create(
            user=user,
            defaults={
                "activated": True,
                "is_active": True,
                "is_valid": True,
                "permissions": all_perms,
            },
        )
        if not profile_created:
            # Keep permissions up to date with the catalog
            profile.permissions = all_perms
            profile.activated = True
            profile.is_active = True
            profile.is_valid = True
            profile.save(update_fields=["permissions", "activated", "is_active", "is_valid"])

        action = "Created" if profile_created else "Updated"
        self.stdout.write(
            f"  {action} superadmin: +201000000000 / Admin@1234 "
            f"({len(all_perms)} permissions)"
        )

    # ── Customers ─────────────────────────────────────────────────────────────

    def _seed_customers(self):
        from apps.authentication.models import UserAccount
        from apps.users.models import CustomerProfile

        customers = [
            dict(phone="+201000000007", username="customer2"),
            dict(phone="+201000000008", username="customer3"),
            dict(phone="+201000000009", username="customer4"),
            dict(phone="+201000000010", username="customer5"),
            dict(phone="+201000000011", username="customer6"),
        ]

        for c in customers:
            user, created = UserAccount.objects.get_or_create(
                phone_number=c["phone"],
                defaults={
                    "username": c["username"],
                    "password": make_password("Test@1234"),
                    "is_active": True,
                },
            )
            if created:
                CustomerProfile.objects.create(
                    user=user,
                    country="EG",
                    currency="EGP",
                    activated=True,
                    is_active=True,
                    is_valid=True,
                    anonymous_bid_perk=False,
                    is_golden_bid=False,
                )
                self.stdout.write(f"  Created customer: {c['phone']}")
            else:
                self.stdout.write(f"  Skipped (exists): {c['phone']}")

    # ── Packages ──────────────────────────────────────────────────────────────

    def _seed_packages(self):
        from apps.packages.models import Package

        packages = [
            dict(
                name="Starter",
                description="50 points, good for small sellers just starting out.",
                points=50,
                price="99.00",
                activation_period_days=30,
                auction_cost=5,
                product_cost=5,
            ),
            dict(
                name="Professional",
                description="200 points, ideal for active sellers.",
                points=200,
                price="299.00",
                activation_period_days=60,
                auction_cost=10,
                product_cost=10,
            ),
            dict(
                name="Premium",
                description="500 points, for high-volume sellers.",
                points=500,
                price="599.00",
                activation_period_days=90,
                auction_cost=20,
                product_cost=20,
            ),
        ]

        for p in packages:
            obj, created = Package.objects.get_or_create(
                name=p["name"],
                defaults=p,
            )
            if created:
                self.stdout.write(f"  Created package: {p['name']}")
            else:
                self.stdout.write(f"  Skipped (exists): {p['name']}")

    # ── Lucky Wheel Prizes ────────────────────────────────────────────────────

    def _seed_lucky_wheel_prizes(self):
        from apps.lucky_wheel.models import LuckyWheelPrize, LuckyWheelPrizeType

        prizes = [
            dict(
                label="50 PP Coins",
                description="Win 50 Pigeon Planet coins!",
                prize_type=LuckyWheelPrizeType.PP_COINS,
                weight=20,
                points_amount=50,
            ),
            dict(
                label="100 PP Coins",
                description="Win 100 Pigeon Planet coins!",
                prize_type=LuckyWheelPrizeType.PP_COINS,
                weight=15,
                points_amount=100,
            ),
            dict(
                label="200 PP Coins",
                description="Win 200 Pigeon Planet coins!",
                prize_type=LuckyWheelPrizeType.PP_COINS,
                weight=10,
                points_amount=200,
            ),
            dict(
                label="500 PP Coins",
                description="Win 500 Pigeon Planet coins!",
                prize_type=LuckyWheelPrizeType.PP_COINS,
                weight=5,
                points_amount=500,
            ),
            dict(
                label="Better luck next time",
                description="No prize this time. Try again!",
                prize_type=LuckyWheelPrizeType.NO_PRIZE,
                weight=50,
                points_amount=None,
            ),
        ]

        for p in prizes:
            obj, created = LuckyWheelPrize.objects.get_or_create(
                label=p["label"],
                defaults=p,
            )
            if created:
                self.stdout.write(f"  Created prize: {p['label']}")
            else:
                self.stdout.write(f"  Skipped (exists): {p['label']}")

    # ── Lucky Wheel Grants ────────────────────────────────────────────────────

    def _seed_lucky_wheel_grants(self):
        from apps.lucky_wheel.services import LuckyWheelGrantService
        from apps.lucky_wheel.models import LuckyWheelGrantSourceType
        from apps.users.models import CustomerProfile, SellerProfile

        # Grant to all active customer profiles
        for customer in CustomerProfile.objects.filter(
            activated=True, is_valid=True, deleted__isnull=True
        ):
            grant = LuckyWheelGrantService.grant_attempt(
                profile=customer,
                source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
                source_id="seed",
                attempts=3,
                idempotency_key=f"seed:manager_grant:customer:{customer.pk}",
            )
            action = "Granted" if grant._state.adding else "Skipped"
            self.stdout.write(
                f"  {action} 3 wheel attempts -> customer profile #{customer.pk}"
            )

        # Grant to all active seller profiles
        for seller in SellerProfile.objects.filter(
            activated=True, is_valid=True, deleted__isnull=True
        ):
            grant = LuckyWheelGrantService.grant_attempt(
                profile=seller,
                source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
                source_id="seed",
                attempts=3,
                idempotency_key=f"seed:manager_grant:seller:{seller.pk}",
            )
            action = "Granted" if grant._state.adding else "Skipped"
            self.stdout.write(
                f"  {action} 3 wheel attempts -> seller profile #{seller.pk}"
            )
