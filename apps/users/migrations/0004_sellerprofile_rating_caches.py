from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_alter_profile_activated"),
    ]

    operations = [
        migrations.AddField(
            model_name="sellerprofile",
            name="avg_rating",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=3,
            ),
        ),
        migrations.AddField(
            model_name="sellerprofile",
            name="ratings_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
