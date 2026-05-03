from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0005_cart_order_total_price_alter_order_status_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("order_created", "Order Created"),
                    ("seller_pending_item", "Seller Pending Item"),
                    ("seller_approved", "Seller Approved"),
                    ("seller_auto_cancelled", "Seller Auto Cancelled"),
                ],
                max_length=40,
            ),
        ),
    ]
