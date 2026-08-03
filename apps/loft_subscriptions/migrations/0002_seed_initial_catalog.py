from decimal import Decimal

from django.db import migrations


def seed_initial_catalog(apps, schema_editor):
    SubscriptionDurationOption = apps.get_model(
        "loft_subscriptions",
        "SubscriptionDurationOption",
    )
    PedigreeTemplatePackage = apps.get_model(
        "loft_subscriptions",
        "PedigreeTemplatePackage",
    )
    SubscriptionAddon = apps.get_model(
        "loft_subscriptions",
        "SubscriptionAddon",
    )
    AIQuotaPackage = apps.get_model(
        "loft_subscriptions",
        "AIQuotaPackage",
    )

    duration_rows = [
        {
            "code": "trial_3_days",
            "name": "3-Day Free Trial",
            "duration_days": 3,
            "duration_months": None,
            "price": Decimal("0.00"),
            "is_trial": True,
            "sort_order": 10,
        },
        {
            "code": "month_1",
            "name": "One Month",
            "duration_days": None,
            "duration_months": 1,
            "price": Decimal("500.00"),
            "is_trial": False,
            "sort_order": 20,
        },
        {
            "code": "month_3",
            "name": "Three Months",
            "duration_days": None,
            "duration_months": 3,
            "price": Decimal("900.00"),
            "is_trial": False,
            "sort_order": 30,
        },
        {
            "code": "month_6",
            "name": "Six Months",
            "duration_days": None,
            "duration_months": 6,
            "price": Decimal("1200.00"),
            "is_trial": False,
            "sort_order": 40,
        },
        {
            "code": "month_9",
            "name": "Nine Months",
            "duration_days": None,
            "duration_months": 9,
            "price": Decimal("1500.00"),
            "is_trial": False,
            "sort_order": 50,
        },
        {
            "code": "month_12",
            "name": "One Year",
            "duration_days": None,
            "duration_months": 12,
            "price": Decimal("1700.00"),
            "is_trial": False,
            "sort_order": 60,
        },
    ]
    for row in duration_rows:
        SubscriptionDurationOption.objects.update_or_create(
            code=row["code"],
            defaults={"currency": "EGP", "is_active": True, **row},
        )

    package_rows = [
        ("shapes_1", "One Shape", 1, False, "0.00", 10),
        ("shapes_3", "Three Shapes", 3, False, "200.00", 20),
        ("shapes_5", "Five Shapes", 5, False, "450.00", 30),
        ("shapes_7", "Seven Shapes", 7, False, "600.00", 40),
        ("shapes_10", "Ten Shapes", 10, False, "850.00", 50),
        ("shapes_unlimited", "Unlimited Shapes", None, True, "999.00", 60),
    ]
    for code, name, limit, is_unlimited, price, sort_order in package_rows:
        PedigreeTemplatePackage.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "template_limit": limit,
                "is_unlimited": is_unlimited,
                "price": Decimal(price),
                "currency": "EGP",
                "is_active": True,
                "sort_order": sort_order,
            },
        )

    addon_rows = [
        ("cloud_backup", "Cloud Backup", "", "79.00", True, True, 10),
        ("medical_records", "Medical Records", "", "79.00", True, True, 20),
        ("qr_code_pedigree", "QR Code Pedigree", "", "99.00", True, True, 30),
        ("loft_multi_user", "Loft Multi User", "", "99.00", False, False, 100),
        (
            "professional_reports",
            "Professional Reports",
            "",
            "99.00",
            False,
            False,
            110,
        ),
        ("pedigree_3d_tree", "Pedigree 3D Tree", "", "199.00", False, False, 120),
        (
            "unlimited_image_archive",
            "Unlimited Image Archive",
            "",
            "79.00",
            False,
            False,
            130,
        ),
        ("additional_storage", "Additional Storage", "", "49.00", False, False, 140),
        (
            "pedigree_direct_link",
            "Pedigree Direct Link",
            "",
            "49.00",
            False,
            False,
            150,
        ),
        (
            "custom_pedigree_template",
            "Custom Pedigree Template",
            "",
            "500.00",
            False,
            False,
            160,
        ),
    ]
    for (
        code,
        name,
        description,
        price,
        is_active,
        is_implemented,
        sort_order,
    ) in addon_rows:
        SubscriptionAddon.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "description": description,
                "price": Decimal(price),
                "currency": "EGP",
                "is_active": is_active,
                "is_implemented": is_implemented,
                "sort_order": sort_order,
            },
        )

    ai_rows = [
        ("ai_100", "100 AI Operations", 100, "99.00", 10),
        ("ai_300", "300 AI Operations", 300, "249.00", 20),
        ("ai_1000", "1000 AI Operations", 1000, "699.00", 30),
    ]
    for code, name, operation_count, price, sort_order in ai_rows:
        AIQuotaPackage.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "operation_count": operation_count,
                "price": Decimal(price),
                "currency": "EGP",
                "is_active": True,
                "sort_order": sort_order,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("loft_subscriptions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_initial_catalog, migrations.RunPython.noop),
    ]
