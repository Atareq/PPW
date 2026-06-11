from decimal import Decimal

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("assets", "0006_remove_asset_thumbnail_url"),
        ("users", "0005_customerprofile_cashback_balance"),
    ]

    operations = [
        migrations.CreateModel(
            name="CashbackOffer",
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
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                (
                    "value_type",
                    models.CharField(
                        choices=[
                            ("percentage", "Percentage"),
                            ("fixed_amount", "Fixed Amount"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "value",
                    models.DecimalField(decimal_places=2, max_digits=10),
                ),
                (
                    "target_type",
                    models.CharField(
                        choices=[
                            ("all", "All"),
                            ("category", "Category"),
                            ("asset", "Asset"),
                        ],
                        default="all",
                        max_length=20,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("bird", "Bird"),
                            ("supply", "Supply"),
                            ("accessory", "Accessory"),
                            ("feed", "Feed"),
                            ("supplement", "Supplement"),
                        ],
                        max_length=20,
                        null=True,
                    ),
                ),
                (
                    "minimum_order_price",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=10,
                        validators=[
                            django.core.validators.MinValueValidator(0)
                        ],
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("priority", models.IntegerField(default=0)),
                (
                    "allowed_redemption_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=10,
                        validators=[
                            django.core.validators.MinValueValidator(0)
                        ],
                    ),
                ),
                (
                    "asset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="%(class)ss",
                        to="assets.asset",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_%(class)ss",
                        to="users.profile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created"],
                "indexes": [
                    models.Index(
                        fields=["is_active", "target_type"],
                        name="promotions_cashback_active_target_idx",
                    ),
                    models.Index(
                        fields=["priority", "-created"],
                        name="promotions_cashback_priority_created_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(("value__gt", 0)),
                        name="promotions_cashback_value_gt_0",
                    ),
                    models.CheckConstraint(
                        check=(
                            models.Q(("value_type", "fixed_amount"))
                            | models.Q(("value__lte", 100))
                        ),
                        name="promotions_cashback_percentage_lte_100",
                    ),
                    models.CheckConstraint(
                        check=models.Q(("minimum_order_price__gte", 0)),
                        name="promotions_cashback_min_order_gte_0",
                    ),
                    models.CheckConstraint(
                        check=models.Q(
                            ("allowed_redemption_amount__gte", 0)
                        ),
                        name="promotions_cashback_redemption_gte_0",
                    ),
                    models.CheckConstraint(
                        check=(
                            models.Q(
                                ("asset__isnull", True),
                                ("category__isnull", True),
                                ("target_type", "all"),
                            )
                            | models.Q(
                                ("asset__isnull", False),
                                ("category__isnull", True),
                                ("target_type", "asset"),
                            )
                            | models.Q(
                                ("asset__isnull", True),
                                ("category__isnull", False),
                                ("target_type", "category"),
                            )
                        ),
                        name="promotions_cashback_target_shape",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="DiscountOffer",
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
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                (
                    "value_type",
                    models.CharField(
                        choices=[
                            ("percentage", "Percentage"),
                            ("fixed_amount", "Fixed Amount"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "value",
                    models.DecimalField(decimal_places=2, max_digits=10),
                ),
                (
                    "target_type",
                    models.CharField(
                        choices=[
                            ("all", "All"),
                            ("category", "Category"),
                            ("asset", "Asset"),
                        ],
                        default="all",
                        max_length=20,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("bird", "Bird"),
                            ("supply", "Supply"),
                            ("accessory", "Accessory"),
                            ("feed", "Feed"),
                            ("supplement", "Supplement"),
                        ],
                        max_length=20,
                        null=True,
                    ),
                ),
                (
                    "minimum_order_price",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=10,
                        validators=[
                            django.core.validators.MinValueValidator(0)
                        ],
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("priority", models.IntegerField(default=0)),
                (
                    "asset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="%(class)ss",
                        to="assets.asset",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_%(class)ss",
                        to="users.profile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created"],
                "indexes": [
                    models.Index(
                        fields=["is_active", "target_type"],
                        name="promotions_discount_active_target_idx",
                    ),
                    models.Index(
                        fields=["priority", "-created"],
                        name="promotions_discount_priority_created_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(("value__gt", 0)),
                        name="promotions_discount_value_gt_0",
                    ),
                    models.CheckConstraint(
                        check=(
                            models.Q(("value_type", "fixed_amount"))
                            | models.Q(("value__lte", 100))
                        ),
                        name="promotions_discount_percentage_lte_100",
                    ),
                    models.CheckConstraint(
                        check=models.Q(("minimum_order_price__gte", 0)),
                        name="promotions_discount_min_order_gte_0",
                    ),
                    models.CheckConstraint(
                        check=(
                            models.Q(
                                ("asset__isnull", True),
                                ("category__isnull", True),
                                ("target_type", "all"),
                            )
                            | models.Q(
                                ("asset__isnull", False),
                                ("category__isnull", True),
                                ("target_type", "asset"),
                            )
                            | models.Q(
                                ("asset__isnull", True),
                                ("category__isnull", False),
                                ("target_type", "category"),
                            )
                        ),
                        name="promotions_discount_target_shape",
                    ),
                ],
            },
        ),
    ]
