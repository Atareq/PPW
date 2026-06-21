class AppPermissions:
    """Legacy compatibility shim; manager codes live in users.manager_permissions."""

    @classmethod
    def all(cls):
        from apps.users.manager_permissions import get_manager_permission_codes

        return get_manager_permission_codes()
