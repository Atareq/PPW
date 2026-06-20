import logging

logger = logging.getLogger(__name__)


class AuditLogService:
    @staticmethod
    def log(request, action: str, resource_type: str, resource_id=None, details=None) -> None:
        """Write one AuditLog row for an admin action. Never raises — failures are logged only."""
        from apps.general.models import AuditLog

        try:
            actor = request.user if request.user.is_authenticated else None
            staff_role = ""
            if actor:
                try:
                    from apps.users.models import ManagerProfile
                    mp = ManagerProfile.objects.filter(
                        pk=actor.active_profile.pk,
                        deleted__isnull=True,
                    ).first()
                    if mp:
                        staff_role = mp.staff_role
                except Exception:
                    pass

            ip = _get_client_ip(request)

            AuditLog.objects.create(
                actor=actor,
                actor_staff_role=staff_role,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id is not None else "",
                details=details or {},
                ip_address=ip,
            )
        except Exception:
            logger.exception("AuditLogService.log failed silently")


def _get_client_ip(request) -> str | None:
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
