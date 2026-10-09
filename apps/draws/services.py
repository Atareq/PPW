"""Weekly onboarding draw services retired per product decision (BE-DRW-CANCEL-01).

The weekly onboarding draw is cancelled completely. Onboarding flows no longer
call into `apps.draws`, and any residual call into `WeeklyDrawEntryService` is
a side-effect-free no-op that creates 0 `WeeklyDrawEntry` rows, 0 `PrizeAward`
rows, 0 `PointTransaction` rows, and 0 `Notification` rows.
"""

from apps.draws.models import WeeklyDraw, WeeklyDrawEntry, WeeklyDrawEntrySource


class WeeklyDrawService:
    """Retired weekly onboarding draw service (BE-DRW-CANCEL-01)."""

    WINNER_SELECTION_STATUS_CANCELLED = "CANCELLED_BY_PRODUCT_DECISION"


class WeeklyDrawFinalizationService:
    """Retired weekly onboarding draw finalization facade (BE-DRW-CANCEL-01)."""


class WeeklyDrawEntryService:
    """Retired weekly onboarding draw entry service (BE-DRW-CANCEL-01).

    Ensure methods return `None` without writing `WeeklyDrawEntry` rows.
    """

    @staticmethod
    def idempotency_key(user) -> str:
        user_id = getattr(user, "pk", user)
        return f"draw:onboarding:user:{user_id}"

    @classmethod
    def ensure_for_onboarding(cls, user, profile=None, *, now=None) -> None:
        return None

    @classmethod
    def ensure_for_first_profile_onboarding(
        cls, user, profile=None, *, now=None
    ) -> None:
        return None

    @classmethod
    def get_entry(cls, user) -> WeeklyDrawEntry | None:
        user_id = getattr(user, "pk", user)
        if user_id is None:
            return None
        return (
            WeeklyDrawEntry.objects.select_related("draw", "user")
            .filter(
                user_id=user_id,
                source=WeeklyDrawEntrySource.ONBOARDING,
                deleted__isnull=True,
            )
            .first()
        )

    @classmethod
    def get_entry_status(cls, user) -> dict:
        entry = cls.get_entry(user)
        if entry is None:
            return {
                "has_entry": False,
                "status": None,
                "draw_id": None,
                "draw_starts_at": None,
                "draw_ends_at": None,
                "assigned_at": None,
            }
        draw: WeeklyDraw | None = entry.draw
        return {
            "has_entry": True,
            "status": entry.status,
            "draw_id": entry.draw_id,
            "draw_starts_at": draw.starts_at if draw is not None else None,
            "draw_ends_at": draw.ends_at if draw is not None else None,
            "assigned_at": entry.assigned_at,
        }
