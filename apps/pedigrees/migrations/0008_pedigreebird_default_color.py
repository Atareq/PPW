from django.db import migrations, models


class Migration(migrations.Migration):
    # No backfill: NULL resolves to the backend fallback at generation time.

    dependencies = [
        ("pedigrees", "0007_pedigreebird_registered_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="pedigreebird",
            name="default_color",
            field=models.CharField(blank=True, max_length=7, null=True),
        ),
    ]
