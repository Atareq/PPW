from django.db import migrations, models


LEGACY_PERMISSION_MAP = {
    "assets": [
        "assets.list",
        "assets.retrieve",
        "assets.create",
        "assets.update",
        "assets.delete",
        "assets.moderate",
    ],
    "assets.list": ["assets.list"],
    "assets.edit": [
        "assets.create",
        "assets.update",
        "assets.moderate",
    ],
    "assets.full_control": [
        "assets.list",
        "assets.retrieve",
        "assets.create",
        "assets.update",
        "assets.delete",
        "assets.moderate",
    ],
    "orders": [
        "orders.list",
        "orders.retrieve",
        "order_items.list",
        "order_items.retrieve",
    ],
    "orders.list": ["orders.list"],
    "orders.edit": [
        "orders.list",
        "orders.retrieve",
        "order_items.list",
        "order_items.retrieve",
    ],
    "orders.full_control": [
        "orders.list",
        "orders.retrieve",
        "order_items.list",
        "order_items.retrieve",
    ],
    "users": [
        "users.sellers.list",
        "users.sellers.retrieve",
        "users.sellers.update",
        "users.customers.list",
        "users.customers.retrieve",
        "users.customers.update",
        "managers.list",
        "managers.retrieve",
    ],
    "users.list": [
        "users.sellers.list",
        "users.customers.list",
    ],
    "users.edit": [
        "users.sellers.update",
        "users.customers.update",
    ],
    "users.full_control": [
        "users.sellers.list",
        "users.sellers.retrieve",
        "users.sellers.update",
        "users.customers.list",
        "users.customers.retrieve",
        "users.customers.update",
        "managers.list",
        "managers.retrieve",
    ],
}


def normalize_permissions(permissions):
    if permissions is None:
        return []

    normalized = []
    for permission in permissions:
        mapped_permissions = LEGACY_PERMISSION_MAP.get(permission, [permission])
        for mapped_permission in mapped_permissions:
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
