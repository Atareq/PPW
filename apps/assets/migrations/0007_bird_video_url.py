from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0006_remove_asset_thumbnail_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='bird',
            name='video_url',
            field=models.CharField(blank=True, default='', max_length=2048),
        ),
    ]
