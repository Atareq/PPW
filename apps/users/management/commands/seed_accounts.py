from django.core.management.base import BaseCommand
from django.db import transaction

from apps.authentication.constants import AppPermissions
from apps.authentication.models import UserAccount
from apps.users.models import ManagerProfile


ACCOUNTS = [
    {
        "phone": "+201000000001",
        "username": "superadmin",
        "password": "Admin@1234",
        "first_name": "Super",
        "last_name": "Admin",
        "is_staff": True,
        "is_superuser": True,
        "permissions": list(AppPermissions.ALL),
    },
    {
        "phone": "+201000000002",
        "username": "manager",
        "password": "Manager@1234",
        "first_name": "Manager",
        "last_name": "One",
        "is_staff": True,
        "is_superuser": False,
        "permissions": list(AppPermissions.ALL),
    },
]


class Command(BaseCommand):
    help = "Seed admin and manager accounts for development"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing seed accounts before re-creating them",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            phones = [a["phone"] for a in ACCOUNTS]
            deleted, _ = UserAccount.objects.filter(
                phone_number__in=phones
            ).delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing seed account(s)."))

        for account in ACCOUNTS:
            self._seed(account)

    @transaction.atomic
    def _seed(self, data: dict):
        phone = data["phone"]
        user, created = UserAccount.objects.get_or_create(
            phone_number=phone,
            defaults={
                "username": data["username"],
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "is_staff": data["is_staff"],
                "is_superuser": data["is_superuser"],
                "permissions": data["permissions"],
            },
        )

        if not created:
            self.stdout.write(
                self.style.WARNING(f"Account {phone} already exists — skipping.")
            )
            return

        user.set_password(data["password"])
        user.save(update_fields=["password"])

        profile = ManagerProfile.objects.create(
            user=user,
            is_active=True,
            is_valid=True,
            activated=True,
        )

        label = "superadmin" if data["is_superuser"] else "manager"
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {label}: phone={phone}  password={data['password']}  profile_id={profile.pk}"
            )
        )
