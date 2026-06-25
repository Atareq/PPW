from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_customerprofile_anonymous_bid_perk_and_more"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="profile",
            unique_together=set(),
        ),
    ]
