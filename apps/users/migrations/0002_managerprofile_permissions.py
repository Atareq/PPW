from django.db import migrations, models


LEGACY_PERMISSION_MAP = {
    "assets": "assets.full_control",
    "orders": "orders.full_control",
    "users": "users.full_control",
}


def normalize_permissions(permissions):
    if permissions is None:
        return []

    normalized = []
    for permission in permissions:
        mapped_permission = LEGACY_PERMISSION_MAP.get(permission, permission)
        if mapped_permission not in normalized:
            normalized.append(mapped_permission)
    return normalized


def copy_user_permissions_to_manager_profiles(apps, schema_editor):
    UserAccount = apps.get_model("authentication", "UserAccount")
    ManagerProfile = apps.get_model("users", "ManagerProfile")

    for user in UserAccount.objects.all().iterator():
        manager_profile = ManagerProfile.objects.filter(user_id=user.pk).first()
        if manager_profile is None:
            continue
        manager_profile.permissions = normalize_permissions(user.permissions)
        manager_profile.save(update_fields=["permissions"])


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0001_initial"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="managerprofile",
            name="permissions",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(
            copy_user_permissions_to_manager_profiles,
            migrations.RunPython.noop,
        ),
    ]
