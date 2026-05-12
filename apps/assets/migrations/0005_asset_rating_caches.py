from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0004_alter_bird_ring_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="avg_rating",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=3,
            ),
        ),
        migrations.AddField(
            model_name="asset",
            name="ratings_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
