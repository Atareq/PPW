from django.core.management.base import BaseCommand

from apps.authentication.models import UserAccount
from apps.users.constants import CountryOptions, CurrencyOptions
from apps.users.models import CustomerProfile, SellerProfile


class Command(BaseCommand):
    help = "Seed demo accounts for development (matches Flutter login placeholder credentials)"

    def handle(self, *args, **options):
        self._create_demo_user(
            phone="+201000000006",
            password="Password123!",
            username="demo_user",
            first_name="Demo",
            last_name="User",
            country=CountryOptions.EGYPT,
            currency=CurrencyOptions.EGP,
            active_profile="customer",
        )
        self.stdout.write(self.style.SUCCESS("Demo accounts seeded successfully."))

    def _create_demo_user(self, phone, password, username, first_name, last_name, country, currency, active_profile):
        user, created = UserAccount.objects.get_or_create(
            phone_number=phone,
            defaults={
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
            },
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(f"  Created user: {phone}")
        else:
            self.stdout.write(f"  User already exists: {phone}")

        # Customer profile
        customer, _ = CustomerProfile.objects.get_or_create(
            user=user,
            defaults={
                "country": country,
                "currency": currency,
                "activated": True,
                "is_active": active_profile == "customer",
                "is_valid": True,
            },
        )

        # Seller profile
        seller, _ = SellerProfile.objects.get_or_create(
            user=user,
            defaults={
                "country": country,
                "currency": currency,
                "nickname": first_name,
                "activated": True,
                "is_active": active_profile == "seller",
                "is_valid": True,
            },
        )

        # Ensure exactly one profile is active
        if active_profile == "customer":
            CustomerProfile.objects.filter(pk=customer.pk).update(is_active=True)
            SellerProfile.objects.filter(pk=seller.pk).update(is_active=False)
        else:
            SellerProfile.objects.filter(pk=seller.pk).update(is_active=True)
            CustomerProfile.objects.filter(pk=customer.pk).update(is_active=False)

        self.stdout.write(f"  Active profile: {active_profile}")
