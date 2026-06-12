import uuid

from django.db import migrations, models


def populate_bird_public_ids(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Bird = apps.get_model("assets", "Bird")

    for bird in Bird.objects.using(db_alias).filter(public_id__isnull=True):
        Bird.objects.using(db_alias).filter(pk=bird.pk).update(
            public_id=uuid.uuid4()
        )


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0006_remove_asset_thumbnail_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="bird",
            name="public_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(
            populate_bird_public_ids,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="bird",
            name="public_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
            ),
        ),
    ]
