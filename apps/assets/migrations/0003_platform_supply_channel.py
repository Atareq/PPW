from django.db import migrations, models
import django.db.models.deletion


PLATFORM_SUPPLY_USERNAME = "ppw-platform-supplies"
PLATFORM_SUPPLY_PHONE_NUMBER = "+201099999999"
PLATFORM_SUPPLY_NICKNAME = "PPW Platform"


def get_platform_seller_profile(apps):
    UserAccount = apps.get_model("authentication", "UserAccount")
    SellerProfile = apps.get_model("users", "SellerProfile")

    user = UserAccount.objects.filter(
        phone_number=PLATFORM_SUPPLY_PHONE_NUMBER
    ).first()
    if user is None:
        user = UserAccount.objects.create(
            username=PLATFORM_SUPPLY_USERNAME,
            phone_number=PLATFORM_SUPPLY_PHONE_NUMBER,
            password="!",
            is_active=True,
        )

    seller = SellerProfile.objects.filter(user_id=user.pk).first()
    if seller is None:
        seller = SellerProfile.objects.create(
            user=user,
            is_active=False,
            activated=True,
            is_valid=True,
            nickname=PLATFORM_SUPPLY_NICKNAME,
            country="EG",
            currency="EGP",
        )
    return seller


def migrate_existing_supplies_to_platform(apps, schema_editor):
    Asset = apps.get_model("assets", "Asset")
    platform_seller = get_platform_seller_profile(apps)
    Asset.objects.filter(category="supply").update(
        owner_id=platform_seller.pk,
        sale_channel="platform",
        managed_by_id=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0002_remove_useraccount_permissions"),
        ("assets", "0002_initial"),
        ("users", "0002_managerprofile_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="managed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="managed_assets",
                to="users.profile",
            ),
        ),
        migrations.AddField(
            model_name="asset",
            name="sale_channel",
            field=models.CharField(
                choices=[
                    ("seller_market", "Seller Market"),
                    ("platform", "Platform"),
                ],
                default="seller_market",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="asset",
            index=models.Index(
                fields=["sale_channel", "category"],
                name="assets_asse_sale_ch_e0c546_idx",
            ),
        ),
        migrations.RunPython(
            migrate_existing_supplies_to_platform,
            migrations.RunPython.noop,
        ),
    ]
