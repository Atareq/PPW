from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0007_orderitem_promotions"),
        ("promotions", "0001_initial"),
        ("users", "0005_customerprofile_cashback_balance"),
    ]

    operations = [
        migrations.CreateModel(
            name="CashbackTransaction",
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
                    models.DateTimeField(
                        db_index=True,
                        editable=False,
                        null=True,
                    ),
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
                    "transaction_type",
                    models.CharField(
                        choices=[
                            ("earned", "Earned"),
                            ("redeemed", "Redeemed"),
                            ("refunded", "Refunded"),
                            ("admin_adjustment", "Admin Adjustment"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(decimal_places=2, max_digits=10),
                ),
                (
                    "balance_after",
                    models.DecimalField(decimal_places=2, max_digits=10),
                ),
                ("note", models.TextField(blank=True)),
                (
                    "idempotency_key",
                    models.CharField(max_length=255, unique=True),
                ),
                (
                    "adjusted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cashback_adjustments",
                        to="users.profile",
                    ),
                ),
                (
                    "cashback_offer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transactions",
                        to="promotions.cashbackoffer",
                    ),
                ),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cashback_transactions",
                        to="users.customerprofile",
                    ),
                ),
                (
                    "order_item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cashback_transactions",
                        to="orders.orderitem",
                    ),
                ),
            ],
            options={
                "ordering": ["-created"],
                "indexes": [
                    models.Index(
                        fields=["customer", "-created"],
                        name="promotions_cashback_customer_created_idx",
                    ),
                    models.Index(
                        fields=["transaction_type"],
                        name="promotions_cashback_type_idx",
                    ),
                    models.Index(
                        fields=["order_item"],
                        name="promotions_cashback_order_item_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(("balance_after__gte", 0)),
                        name="promotions_cashback_balance_after_gte_0",
                    ),
                    models.CheckConstraint(
                        check=(
                            (
                                models.Q(
                                    ("transaction_type", "admin_adjustment")
                                )
                                & ~models.Q(("amount", 0))
                            )
                            | (
                                ~models.Q(
                                    ("transaction_type", "admin_adjustment")
                                )
                                & models.Q(("amount__gt", 0))
                            )
                        ),
                        name="promotions_cashback_amount_valid",
                    ),
                ],
            },
        ),
    ]
