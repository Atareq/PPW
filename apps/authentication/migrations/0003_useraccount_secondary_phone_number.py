from django.db import migrations
import phonenumber_field.modelfields


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0002_remove_useraccount_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="useraccount",
            name="secondary_phone_number",
            field=phonenumber_field.modelfields.PhoneNumberField(
                blank=True,
                db_index=True,
                max_length=128,
                null=True,
                region=None,
            ),
        ),
    ]
