from django.db import migrations, models
from django.db.models import F
import django.db.models.deletion


def backfill_registered_by_from_owner(apps, schema_editor):
    # Historical rows were registered by their owner. Subjects and ancestors
    # cannot be told apart, so owners are preserved as-is (no inference).
    PedigreeBird = apps.get_model("pedigrees", "PedigreeBird")
    PedigreeBird._base_manager.update(registered_by_id=F("owner_id"))


def restore_owner_from_registered_by(apps, schema_editor):
    # Reverse only: owner becomes non-null again, so unowned rows fall back
    # to the profile that registered them.
    PedigreeBird = apps.get_model("pedigrees", "PedigreeBird")
    PedigreeBird._base_manager.filter(owner__isnull=True).update(
        owner_id=F("registered_by_id")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_sellerroom_sellerroomfollow_and_more"),
        ("pedigrees", "0006_alter_pedigreebird_life_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="pedigreebird",
            name="registered_by",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="registered_pedigree_birds",
                to="users.profile",
            ),
        ),
        migrations.RunPython(
            backfill_registered_by_from_owner,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="pedigreebird",
            name="registered_by",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="registered_pedigree_birds",
                to="users.profile",
            ),
        ),
        migrations.AlterField(
            model_name="pedigreebird",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_pedigreebirds",
                to="users.profile",
            ),
        ),
        migrations.RunPython(
            migrations.RunPython.noop,
            restore_owner_from_registered_by,
        ),
        migrations.RemoveConstraint(
            model_name="pedigreebird",
            name="uniq_live_pedigree_bird_ring_per_owner",
        ),
        migrations.RemoveIndex(
            model_name="pedigreebird",
            name="ped_bird_owner_ring_idx",
        ),
        migrations.AddIndex(
            model_name="pedigreebird",
            index=models.Index(
                fields=["registered_by", "normalized_ring_number"],
                name="ped_bird_reg_ring_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="pedigreebird",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted__isnull", True)),
                fields=("registered_by", "normalized_ring_number"),
                name="uniq_live_pedigree_bird_ring_per_registrant",
            ),
        ),
        migrations.AlterField(
            model_name="pedigreebird",
            name="ownership_status",
            field=models.CharField(
                choices=[
                    ("owned", "Owned"),
                    ("sold", "Sold"),
                    ("gifted", "Gifted"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=20,
            ),
        ),
    ]
