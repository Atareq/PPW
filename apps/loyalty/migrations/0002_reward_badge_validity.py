from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("loyalty", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="badgeaward",
            name="loyalty_unique_active_profile_badge",
        ),
        migrations.AddField(
            model_name="badgeaward",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="badgeaward",
            name="idempotency_key",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="badgeaward",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="badgeaward",
            name="badge_type",
            field=models.CharField(
                choices=[
                    ("first_order", "First Order"),
                    ("points_starter", "Points Starter"),
                    ("seller_starter", "Seller Starter"),
                    ("WELCOME_BUYER", "Welcome Buyer"),
                    ("WELCOME_SELLER", "Welcome Seller"),
                    ("FIRST_BIDDER", "First Bidder"),
                    ("LAST_MINUTE_FIGHTER", "Last Minute Fighter"),
                    (
                        "AUCTION_PURCHASE_CONFIRMED",
                        "Auction Purchase Confirmed",
                    ),
                    (
                        "MARKET_PURCHASE_CONFIRMED",
                        "Market Purchase Confirmed",
                    ),
                    ("REVIEWER_BUYER", "Reviewer Buyer"),
                    ("LOYAL_FOLLOWER", "Loyal Follower"),
                    (
                        "FIRST_AUCTION_PUBLISHED",
                        "First Auction Published",
                    ),
                    (
                        "FIRST_MARKET_ITEM_LISTED",
                        "First Market Item Listed",
                    ),
                    (
                        "FIRST_AUCTION_SALE_CONFIRMED",
                        "First Auction Sale Confirmed",
                    ),
                    (
                        "FIRST_MARKET_SALE_CONFIRMED",
                        "First Market Sale Confirmed",
                    ),
                    ("TRUSTED_BUYER", "Trusted Buyer"),
                    ("TRUSTED_SELLER", "Trusted Seller"),
                ],
                max_length=50,
            ),
        ),
        migrations.AddIndex(
            model_name="badgeaward",
            index=models.Index(
                fields=["expires_at"],
                name="loyalty_bad_expires_ed2581_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="badgeaward",
            index=models.Index(
                fields=["source_type"],
                name="loyalty_bad_source__5cd7c0_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="badgeaward",
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(("idempotency_key__isnull", False))
                    & ~models.Q(("idempotency_key", ""))
                ),
                fields=("idempotency_key",),
                name="loyalty_unique_badge_idempotency_key",
            ),
        ),
    ]
