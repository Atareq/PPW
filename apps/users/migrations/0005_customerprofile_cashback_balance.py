from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_sellerprofile_rating_caches"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerprofile",
            name="cashback_balance",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddConstraint(
            model_name="customerprofile",
            constraint=models.CheckConstraint(
                check=models.Q(("cashback_balance__gte", 0)),
                name="users_customer_cashback_balance_gte_0",
            ),
        ),
    ]
