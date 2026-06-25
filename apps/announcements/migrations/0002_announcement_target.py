from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("announcements", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="target",
            field=models.CharField(
                choices=[
                    ("Seller", "Seller"),
                    ("Customer", "Customer"),
                ],
                db_index=True,
                default="Customer",
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="announcement",
            index=models.Index(
                fields=["target", "is_active", "-created"],
                name="announcemen_target_975ae2_idx",
            ),
        ),
    ]
