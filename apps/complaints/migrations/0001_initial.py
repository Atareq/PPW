from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("payments", "0001_initial"),
        ("users", "0005_customerprofile_cashback_balance"),
    ]

    operations = [
        migrations.CreateModel(
            name="Complaint",
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
                    "complaint_type",
                    models.CharField(
                        choices=[
                            ("post_sale", "Post Sale"),
                            ("payment_rejected", "Payment Rejected"),
                            ("other", "Other"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("in_review", "In Review"),
                            ("resolved", "Resolved"),
                            ("rejected", "Rejected"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("admin_note", models.TextField(blank=True)),
                (
                    "reviewed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "resolved_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "cancelled_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "buyer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="complaints_as_buyer",
                        to="users.profile",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_complaints",
                        to="users.profile",
                    ),
                ),
                (
                    "payment_request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="complaints",
                        to="payments.paymentrequest",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_complaints",
                        to="users.profile",
                    ),
                ),
                (
                    "seller",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="complaints_as_seller",
                        to="users.profile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created"],
                "indexes": [
                    models.Index(
                        fields=["payment_request"],
                        name="complaint_payment_idx",
                    ),
                    models.Index(
                        fields=["created_by"],
                        name="complaint_creator_idx",
                    ),
                    models.Index(fields=["buyer"], name="complaint_buyer_idx"),
                    models.Index(fields=["seller"], name="complaint_seller_idx"),
                    models.Index(fields=["status"], name="complaint_status_idx"),
                    models.Index(
                        fields=["complaint_type"],
                        name="complaint_type_idx",
                    ),
                    models.Index(fields=["created"], name="complaint_created_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("deleted__isnull", True),
                            ("status__in", ["open", "in_review"]),
                        ),
                        fields=("payment_request",),
                        name="complaints_one_active_per_pr",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ComplaintEvent",
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
                    "event_type",
                    models.CharField(
                        choices=[
                            ("complaint_created", "Complaint Created"),
                            ("complaint_in_review", "Complaint In Review"),
                            ("complaint_resolved", "Complaint Resolved"),
                            ("complaint_rejected", "Complaint Rejected"),
                            ("complaint_cancelled", "Complaint Cancelled"),
                        ],
                        max_length=40,
                    ),
                ),
                ("message", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="complaint_events",
                        to="users.profile",
                    ),
                ),
                (
                    "complaint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="complaints.complaint",
                    ),
                ),
            ],
            options={
                "ordering": ["-created"],
                "indexes": [
                    models.Index(
                        fields=["complaint", "event_type"],
                        name="complaint_event_type_idx",
                    ),
                    models.Index(
                        fields=["actor"],
                        name="complaint_event_actor_idx",
                    ),
                    models.Index(
                        fields=["created"],
                        name="complaint_event_created_idx",
                    ),
                ],
            },
        ),
    ]
