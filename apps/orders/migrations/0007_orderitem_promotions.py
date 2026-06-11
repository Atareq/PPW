from decimal import Decimal

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


def populate_order_item_promotion_totals(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    OrderItem = apps.get_model("orders", "OrderItem")

    for item in OrderItem.objects.all():
        gross_total = item.quantity * item.unit_price_snapshot
        OrderItem.objects.filter(pk=item.pk).update(
            gross_total=gross_total,
            total_price=gross_total,
        )

    for order in Order.objects.all():
        total_price = sum(
            (
                item.total_price
                for item in OrderItem.objects.filter(order_id=order.pk)
            ),
            Decimal("0.00"),
        )
        Order.objects.filter(pk=order.pk).update(total_price=total_price)


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0006_alter_orderevent_event_type"),
        ("promotions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="gross_total",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="discount_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="cashback_redeemed_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="cashback_earned_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="discount_offer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="order_items",
                to="promotions.discountoffer",
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="cashback_offer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="order_items",
                to="promotions.cashbackoffer",
            ),
        ),
        migrations.RunPython(
            populate_order_item_promotion_totals,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                check=models.Q(("gross_total__gte", 0)),
                name="orders_orderitem_gross_total_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                check=models.Q(("discount_amount__gte", 0)),
                name="orders_orderitem_discount_amount_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                check=models.Q(("cashback_redeemed_amount__gte", 0)),
                name="orders_orderitem_cashback_redeemed_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                check=models.Q(("cashback_earned_amount__gte", 0)),
                name="orders_orderitem_cashback_earned_gte_0",
            ),
        ),
    ]
