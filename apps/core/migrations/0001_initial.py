# Generated manually for the mobile notifications module.

from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Notification",
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
                    "deleted",
                    models.DateTimeField(db_index=True, editable=False, null=True),
                ),
                (
                    "deleted_by_cascade",
                    models.BooleanField(default=False, editable=False),
                ),
                (
                    "created",
                    django_extensions.db.fields.CreationDateTimeField(
                        auto_now_add=True,
                        verbose_name="created",
                    ),
                ),
                (
                    "modified",
                    django_extensions.db.fields.ModificationDateTimeField(
                        auto_now=True,
                        verbose_name="modified",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("order", "Order update"),
                            ("auction_won", "Auction won"),
                            ("new_bid", "New bid on item"),
                            ("bid_accepted", "Bid accepted"),
                            ("new_badge", "Badge awarded"),
                            ("payment", "Payment update"),
                            ("chat", "New message"),
                        ],
                        max_length=30,
                    ),
                ),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField(blank=True, default="")),
                ("is_read", models.BooleanField(db_index=True, default=False)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "profile_type",
                    models.CharField(blank=True, default="All", max_length=20),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="users.profile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created"],
            },
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(
                fields=["profile", "is_read"],
                name="core_notifi_profile_2252f5_idx",
            ),
        ),
    ]
