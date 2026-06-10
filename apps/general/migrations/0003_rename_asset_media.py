from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("general", "0002_image_assetimage"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="Image",
            new_name="Media",
        ),
        migrations.RenameField(
            model_name="media",
            old_name="image_url",
            new_name="media_url",
        ),
        migrations.AddField(
            model_name="media",
            name="media_type",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Video")],
                default="image",
                max_length=10,
            ),
        ),
        migrations.RenameModel(
            old_name="AssetImage",
            new_name="AssetMedia",
        ),
        migrations.RenameField(
            model_name="assetmedia",
            old_name="image_ptr",
            new_name="media_ptr",
        ),
        migrations.AlterField(
            model_name="assetmedia",
            name="asset",
            field=models.ForeignKey(
                db_index=False,
                on_delete=models.CASCADE,
                related_name="media",
                to="assets.asset",
            ),
        ),
        migrations.RenameIndex(
            model_name="assetmedia",
            old_name="general_ass_asset_i_1d02bf_idx",
            new_name="general_ass_asset_i_13b496_idx",
        ),
        migrations.RenameIndex(
            model_name="assetmedia",
            old_name="general_ass_asset_i_318e1f_idx",
            new_name="general_ass_asset_i_7ed4b6_idx",
        ),
    ]
