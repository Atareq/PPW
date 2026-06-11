# Generated for auction thumbnail_url field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auctions", "0004_auction_seller_package"),
    ]

    operations = [
        migrations.AddField(
            model_name="auction",
            name="thumbnail_url",
            field=models.CharField(blank=True, max_length=2048),
        ),
    ]
