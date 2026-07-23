from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "pedigrees",
            "0003_pedigreebird_pedigreetemplate_pedigreecertificate_and_more",
        ),
    ]

    operations = [
        migrations.DeleteModel(
            name="PedigreeCertificate",
        ),
        migrations.DeleteModel(
            name="PedigreeTemplate",
        ),
    ]
