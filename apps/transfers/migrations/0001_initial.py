import django.db.models.deletion
import django_extensions.db.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("assets", "0001_initial"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="OwnershipTransfer",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created",
                    django_extensions.db.fields.CreationDateTimeField(
                        auto_now_add=True, verbose_name="created"
                    ),
                ),
                (
                    "modified",
                    django_extensions.db.fields.ModificationDateTimeField(
                        auto_now=True, verbose_name="modified"
                    ),
                ),
                ("deleted", models.DateTimeField(db_index=True, editable=False, null=True)),
                (
                    "transfer_type",
                    models.CharField(
                        choices=[
                            ("direct", "Direct Transfer"),
                            ("market", "Market Purchase"),
                            ("auction", "Auction Purchase"),
                        ],
                        max_length=20,
                    ),
                ),
                ("note", models.TextField(blank=True, default="")),
                (
                    "asset",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ownership_transfers",
                        to="assets.asset",
                    ),
                ),
                (
                    "from_profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sent_transfers",
                        to="users.profile",
                    ),
                ),
                (
                    "to_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="received_transfers",
                        to="users.profile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created"],
            },
        ),
    ]
