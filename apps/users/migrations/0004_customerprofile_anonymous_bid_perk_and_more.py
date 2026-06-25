from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds anonymous_bid_perk, golden_bid_until, is_golden_bid to CustomerProfile.
    Uses SeparateDatabaseAndState so existing databases where these columns were
    added by a now-lost migration are skipped via IF NOT EXISTS guards, while
    fresh databases get the columns created normally.
    """

    dependencies = [
        ("users", "0003_profile_address_profile_national_id_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE users_customerprofile
                          ADD COLUMN IF NOT EXISTS anonymous_bid_perk boolean NOT NULL DEFAULT false,
                          ADD COLUMN IF NOT EXISTS golden_bid_until timestamp with time zone,
                          ADD COLUMN IF NOT EXISTS is_golden_bid boolean NOT NULL DEFAULT false;
                    """,
                    reverse_sql="""
                        ALTER TABLE users_customerprofile
                          DROP COLUMN IF EXISTS anonymous_bid_perk,
                          DROP COLUMN IF EXISTS golden_bid_until,
                          DROP COLUMN IF EXISTS is_golden_bid;
                    """,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="customerprofile",
                    name="anonymous_bid_perk",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="customerprofile",
                    name="golden_bid_until",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="customerprofile",
                    name="is_golden_bid",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
