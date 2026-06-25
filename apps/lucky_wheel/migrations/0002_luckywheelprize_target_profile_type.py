from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lucky_wheel", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="luckywheelprize",
            name="target_profile_type",
            field=models.CharField(
                choices=[("customer", "Customer"), ("seller", "Seller"), ("both", "Both")],
                default="both",
                db_index=True,
                max_length=10,
            ),
        ),
    ]
