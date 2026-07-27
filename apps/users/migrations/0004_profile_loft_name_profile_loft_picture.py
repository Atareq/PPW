from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_profile_address_profile_national_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="loft_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="profile",
            name="loft_picture",
            field=models.ImageField(blank=True, null=True, upload_to="lofts/"),
        ),
    ]
