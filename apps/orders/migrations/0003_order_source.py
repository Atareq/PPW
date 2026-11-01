from django.db import migrations, models


class Migration(migrations.Migration):
    # Existing orders get "unknown": their checkout client was never recorded.

    dependencies = [
        ("orders", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="source",
            field=models.CharField(
                choices=[
                    ("mobile", "Mobile"),
                    ("web", "Web"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                editable=False,
                max_length=20,
            ),
        ),
    ]
