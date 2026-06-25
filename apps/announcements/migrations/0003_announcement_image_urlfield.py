from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("announcements", "0002_announcement_target"),
    ]

    operations = [
        migrations.AlterField(
            model_name="announcement",
            name="image",
            field=models.URLField(max_length=2048),
        ),
    ]
