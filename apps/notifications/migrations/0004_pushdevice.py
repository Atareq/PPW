from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields


class Migration(migrations.Migration):
    dependencies = [
        ("authentication", "0001_initial"),
        ("notifications", "0003_alter_notification_notification_type"),
        ("users", "0005_profile_profile_data_last_changed_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PushDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("deleted", models.DateTimeField(db_index=True, editable=False, null=True)),
                ("deleted_by_cascade", models.BooleanField(default=False, editable=False)),
                ("created", django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name="created")),
                ("modified", django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name="modified")),
                ("token", models.TextField(unique=True)),
                ("platform", models.CharField(choices=[("android", "Android"), ("ios", "iOS")], max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_devices", to="users.profile")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_devices", to="authentication.useraccount")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "profile", "is_active"], name="notificati_user_id_8e8e6d_idx"),
                    models.Index(fields=["profile", "is_active"], name="notificati_profile__2a9c1b_idx"),
                ],
            },
        ),
    ]
