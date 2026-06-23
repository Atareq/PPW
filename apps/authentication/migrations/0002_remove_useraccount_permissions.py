from django.db import migrations


LEGACY_LEVEL_ACTIONS = {
    "list": {"list", "retrieve"},
    "edit": {
        "create",
        "update",
        "mark_in_review",
        "resolve",
        "reject",
        "activate",
        "deactivate",
        "cancel",
        "close",
        "review",
    },
    "full_control": None,
}

LEGACY_NAMESPACE_PREFIXES = {
    "orders": ("orders.", "order_items."),
    "users": ("users.", "managers."),
}


def _prefixes_for_namespace(namespace):
    return LEGACY_NAMESPACE_PREFIXES.get(namespace, (f"{namespace}.",))


def _expand_legacy_permission(permission, valid_codes):
    if permission in valid_codes:
        return [permission]

    if not isinstance(permission, str):
        return []

    if "." not in permission:
        prefixes = _prefixes_for_namespace(permission)
        return [
            code
            for code in valid_codes
            if any(code.startswith(prefix) for prefix in prefixes)
        ]

    namespace, legacy_level = permission.rsplit(".", 1)
    prefixes = _prefixes_for_namespace(namespace)
    namespace_codes = [
        code
        for code in valid_codes
        if any(code.startswith(prefix) for prefix in prefixes)
    ]
    allowed_actions = LEGACY_LEVEL_ACTIONS.get(legacy_level)

    if allowed_actions is None and legacy_level == "full_control":
        return namespace_codes

    if not allowed_actions:
        return []

    return [
        code for code in namespace_codes if code.rsplit(".", 1)[1] in allowed_actions
    ]


def copy_user_permissions_to_manager_profiles(apps, schema_editor):
    from apps.users.manager_permissions import (
        get_manager_permission_codes,
        normalize_manager_permissions,
    )

    UserAccount = apps.get_model("authentication", "UserAccount")
    ManagerProfile = apps.get_model("users", "ManagerProfile")
    valid_codes = get_manager_permission_codes()

    for user in UserAccount.objects.all().iterator():
        manager_profile = ManagerProfile.objects.filter(user_id=user.pk).first()
        if manager_profile is None:
            continue

        expanded_permissions = list(manager_profile.permissions or [])
        for permission in user.permissions or []:
            expanded_permissions.extend(
                _expand_legacy_permission(permission, valid_codes)
            )

        manager_profile.permissions = normalize_manager_permissions(
            [
                permission
                for permission in expanded_permissions
                if permission in valid_codes
            ]
        )
        manager_profile.save(update_fields=["permissions"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_managerprofile_permissions"),
        ("authentication", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            copy_user_permissions_to_manager_profiles,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="useraccount",
            name="permissions",
        ),
    ]
