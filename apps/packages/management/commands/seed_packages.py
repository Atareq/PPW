from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.packages.models import Package

PACKAGES = [
    {
        "name": "باقة المبتدئ",
        "description": (
            "مثالية للبائعين الجدد الذين يرغبون في تجربة المنصة. "
            "تتيح لك نشر مزادات ومنتجات بتكلفة منخفضة خلال شهر كامل."
        ),
        "points": 50,
        "price": Decimal("99.00"),
        "activation_period_days": 30,
        "auction_cost": 5,
        "product_cost": 2,
        "is_available": True,
    },
    {
        "name": "الباقة الفضية",
        "description": (
            "خيار متوازن للبائعين النشطين. "
            "نقاط كافية لإدارة عدد أكبر من المزادات والمنتجات على مدى شهرين."
        ),
        "points": 150,
        "price": Decimal("249.00"),
        "activation_period_days": 60,
        "auction_cost": 10,
        "product_cost": 5,
        "is_available": True,
    },
    {
        "name": "الباقة الذهبية",
        "description": (
            "للبائعين المحترفين الذين يديرون نشاطاً تجارياً متكاملاً. "
            "نقاط وفيرة وصلاحية ممتدة لثلاثة أشهر بسعر مميز."
        ),
        "points": 400,
        "price": Decimal("499.00"),
        "activation_period_days": 90,
        "auction_cost": 20,
        "product_cost": 8,
        "is_available": True,
    },
    {
        "name": "الباقة البلاتينية",
        "description": (
            "الباقة الأعلى مستوى على المنصة. "
            "مخصصة للتجار الكبار الذين يحتاجون إلى طاقة استيعابية عالية "
            "وأولوية في النشر لمدة ستة أشهر."
        ),
        "points": 1000,
        "price": Decimal("999.00"),
        "activation_period_days": 180,
        "auction_cost": 40,
        "product_cost": 15,
        "is_available": True,
    },
]


class Command(BaseCommand):
    help = "Seed initial Package plans (Arabic). Safe to re-run — skips existing names."

    def handle(self, *args, **kwargs):
        created_count = 0
        skipped_count = 0

        for data in PACKAGES:
            pkg, created = Package.objects.get_or_create(
                name=data["name"],
                defaults={k: v for k, v in data.items() if k != "name"},
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  [+] created: {pkg.name}"))
            else:
                skipped_count += 1
                self.stdout.write(self.style.WARNING(f"  [=] already exists: {pkg.name}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {created_count} created, {skipped_count} skipped."
            )
        )
