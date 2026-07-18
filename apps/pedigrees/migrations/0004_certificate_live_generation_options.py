from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models

from apps.pedigrees.constants import PEDIGREE_MAX_GENERATION_DEPTH


def populate_generation_depth(apps, schema_editor):
    PedigreeCertificate = apps.get_model("pedigrees", "PedigreeCertificate")
    for certificate in PedigreeCertificate.objects.select_related("template").all():
        template_generation = getattr(certificate.template, "generation", None)
        certificate.generation_depth = template_generation or 1
        certificate.save(update_fields=["generation_depth"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "pedigrees",
            "0003_pedigreebird_pedigreetemplate_pedigreecertificate_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="pedigreecertificate",
            name="customization",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="pedigreecertificate",
            name="generation_depth",
            field=models.PositiveSmallIntegerField(
                null=True,
                validators=[
                    MinValueValidator(1),
                    MaxValueValidator(PEDIGREE_MAX_GENERATION_DEPTH),
                ],
            ),
        ),
        migrations.AddField(
            model_name="pedigreecertificate",
            name="owner_data",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(
            populate_generation_depth,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="pedigreecertificate",
            name="generation_depth",
            field=models.PositiveSmallIntegerField(
                validators=[
                    MinValueValidator(1),
                    MaxValueValidator(PEDIGREE_MAX_GENERATION_DEPTH),
                ],
            ),
        ),
        migrations.RemoveField(
            model_name="pedigreecertificate",
            name="editable_data",
        ),
        migrations.RemoveField(
            model_name="pedigreecertificate",
            name="tree_snapshot",
        ),
    ]
