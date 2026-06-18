from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="photo",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
    ]
