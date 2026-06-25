from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('packages', '0002_remove_sellerpackage_packages_one_active_seller_package_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sellerpackage',
            name='payment_proof',
            field=models.FileField(blank=True, null=True, upload_to='package_payment_proofs/'),
        ),
    ]
