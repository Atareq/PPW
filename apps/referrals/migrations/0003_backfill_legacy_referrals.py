"""Reward-silent backfill of pre-BE-REF-01 redeemed codes into referrals.

Before BE-REF-01 a redeemed code meant an accepted referral whose inviter was
paid at redemption, so each legacy used code becomes an ``approved`` referral.
No events are emitted and no point transactions are written. The reward
handler also treats the legacy ledger key as already paid. When one account
redeemed several codes (possible before BE-REF-01), only its earliest
redemption becomes the referral; codes whose redeemer profile no longer
exists are skipped.
"""

from django.db import migrations


def backfill_legacy_referrals(apps, schema_editor):
    LinkSharingCode = apps.get_model("referrals", "LinkSharingCode")
    Referral = apps.get_model("referrals", "Referral")

    referred_users = set(Referral.objects.values_list("referred_user_id", flat=True))
    codes = (
        LinkSharingCode.objects.filter(
            is_used=True,
            used_by_profile__isnull=False,
            deleted__isnull=True,
            referral__isnull=True,
        )
        .select_related("owner_profile", "used_by_profile")
        .order_by("used_at", "id")
    )
    for code in codes:
        referred_user_id = code.used_by_profile.user_id
        inviter_user_id = code.owner_profile.user_id
        if referred_user_id in referred_users or referred_user_id == inviter_user_id:
            continue
        redeemed_at = code.used_at or code.modified
        Referral.objects.create(
            code=code,
            inviter_user_id=inviter_user_id,
            inviter_profile_id=code.owner_profile_id,
            referred_user_id=referred_user_id,
            referred_profile_id=code.used_by_profile_id,
            status="approved",
            redeemed_at=redeemed_at,
            reviewed_at=redeemed_at,
        )
        referred_users.add(referred_user_id)


class Migration(migrations.Migration):

    dependencies = [
        ("referrals", "0002_referral"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_referrals, migrations.RunPython.noop),
    ]
