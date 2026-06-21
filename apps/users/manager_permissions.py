from django.core.exceptions import ValidationError

MANAGER_PERMISSION_MODULES = (
    "assets",
    "auctions",
    "orders",
    "users",
    "managers",
    "payments",
    "complaints",
    "packages",
    "promotions",
    "races",
    "pedigrees",
    "feed",
    "chat",
    "ratings",
    "insights",
    "notifications",
)

MANAGER_PERMISSION_LEVELS = (
    "list",
    "edit",
    "full_control",
)

_LEVEL_PRIORITY = {
    level: priority for priority, level in enumerate(MANAGER_PERMISSION_LEVELS)
}


def build_manager_permission_code(module: str, level: str) -> str:
    return f"{module}.{level}"


def split_manager_permission_code(permission_code: str) -> tuple[str, str]:
    try:
        module, level = permission_code.split(".", 1)
    except ValueError as exc:
        raise ValidationError(
            {"permissions": [f"Invalid permission code: {permission_code}"]}
        ) from exc
    return module, level


def get_manager_permission_codes() -> set[str]:
    return {
        build_manager_permission_code(module, level)
        for module in MANAGER_PERMISSION_MODULES
        for level in MANAGER_PERMISSION_LEVELS
    }


def get_manager_permission_choices() -> list[tuple[str, str]]:
    choices = []
    for module in MANAGER_PERMISSION_MODULES:
        for level in MANAGER_PERMISSION_LEVELS:
            code = build_manager_permission_code(module, level)
            label = f"{module.replace('_', ' ').title()} - {level.replace('_', ' ')}"
            choices.append((code, label))
    return choices


def validate_manager_permissions(permissions) -> list[str]:
    if permissions is None:
        return []

    if not isinstance(permissions, list):
        raise ValidationError({"permissions": ["Permissions must be a list."]})

    valid_codes = get_manager_permission_codes()
    invalid_codes = []

    for permission in permissions:
        if not isinstance(permission, str):
            raise ValidationError(
                {"permissions": ["Each permission must be a string."]}
            )
        if permission not in valid_codes:
            invalid_codes.append(permission)

    if invalid_codes:
        raise ValidationError(
            {
                "permissions": [
                    f"Invalid permission code: {permission}"
                    for permission in invalid_codes
                ]
            }
        )

    return permissions


def normalize_manager_permissions(permissions) -> list[str]:
    validated_permissions = validate_manager_permissions(permissions)
    highest_level_by_module: dict[str, str] = {}

    for permission in validated_permissions:
        module, level = split_manager_permission_code(permission)
        current_level = highest_level_by_module.get(module)
        if current_level is None or _LEVEL_PRIORITY[level] > _LEVEL_PRIORITY[current_level]:
            highest_level_by_module[module] = level

    normalized_permissions = [
        build_manager_permission_code(module, highest_level_by_module[module])
        for module in MANAGER_PERMISSION_MODULES
        if module in highest_level_by_module
    ]
    return normalized_permissions


def permission_level_satisfies(
    granted_permission: str, required_permission: str
) -> bool:
    granted_module, granted_level = split_manager_permission_code(
        granted_permission
    )
    required_module, required_level = split_manager_permission_code(
        required_permission
    )
    if granted_module != required_module:
        return False
    return _LEVEL_PRIORITY[granted_level] >= _LEVEL_PRIORITY[required_level]
