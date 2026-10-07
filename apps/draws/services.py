from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.draws.models import (
    WeeklyDraw,
    WeeklyDrawEntry,
    WeeklyDrawEntrySource,
    WeeklyDrawEntryStatus,
    WeeklyDrawStatus,
)
from apps.users.constants import ProfileType
from apps.users.models import Profile
from apps.users.services import AccountOnboardingService


def _translate_django_validation_error(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


class WeeklyDrawService:
    """Manage weekly onboarding draw windows, assignment, and execution freeze (BE-DRW-01 / BE-DRW-02).

    Invariants:
    - Canonical active window is half-open `[starts_at, ends_at)`:
      `starts_at <= now < ends_at`.
    - Eligible draw selection at `now`:
      1. `OPEN` draw where `starts_at <= now < ends_at` (`starts_at ASC, id ASC`).
      2. Earliest upcoming `OPEN` or `DRAFT` draw where `starts_at > now`
         (`starts_at ASC, id ASC`).
      3. Ended (`ends_at <= now`), `CLOSED`, and `FINALIZED` draws are never eligible.
    - Schedule fields (`starts_at`, `ends_at`) are locked once `now >= starts_at`,
      once the draw has assigned entries, or once `status in (CLOSED, FINALIZED)`.
    - A draw can be closed/frozen for execution only when `now >= ends_at`.
    - Winner selection is gated behind product confirmation (BE-DRW-02 PARTIAL/BLOCKED):
      `finalize_draw` locks the draw, verifies `now >= ends_at`, freezes the
      `ASSIGNED` participant set once, and keeps `status = CLOSED` rather than
      falsely marking `FINALIZED` without a source-confirmed winner rule.
    - Zero winner selection, prize distribution, PP mutations, Lucky Wheel grants,
      or notifications occur in BE-DRW-01 / BE-DRW-02.
    """

    EDITABLE_FIELDS = {"title", "starts_at", "ends_at", "status", "metadata"}
    LOCKED_SCHEDULE_FIELDS = {"starts_at", "ends_at"}
    CREATABLE_STATUSES = {WeeklyDrawStatus.DRAFT, WeeklyDrawStatus.OPEN}
    WINNER_SELECTION_STATUS_BLOCKED = "BLOCKED_PENDING_PRODUCT_DECISION"

    @staticmethod
    def _resolve_creator_profile(actor):
        if actor is None:
            return None
        if isinstance(actor, Profile):
            return actor
        if hasattr(actor, "active_profile"):
            try:
                return actor.active_profile
            except PermissionDenied:
                return None
        return None

    @staticmethod
    def _validate_aware_datetime(value, *, field_name: str):
        if value is None:
            raise ValidationError({field_name: [_("This field is required.")]})
        if not hasattr(value, "tzinfo") or timezone.is_naive(value):
            raise ValidationError(
                {field_name: [_("Datetime value must be timezone-aware.")]}
            )
        return value

    @staticmethod
    def _validate_metadata(metadata):
        if metadata is None:
            return {}
        if not isinstance(metadata, dict):
            raise ValidationError(
                {"metadata": [_("Weekly draw metadata must be a JSON object.")]}
            )
        return metadata

    @staticmethod
    def _validate_title(title):
        if title is None:
            return ""
        if not isinstance(title, str):
            raise ValidationError({"title": [_("Invalid weekly draw title.")]})
        return title.strip()

    @staticmethod
    def _get_locked_draw(draw) -> WeeklyDraw:
        draw_id = getattr(draw, "pk", draw)
        try:
            return WeeklyDraw.objects.select_for_update().get(
                pk=draw_id,
                deleted__isnull=True,
            )
        except (WeeklyDraw.DoesNotExist, ValueError, TypeError) as exc:
            raise NotFound(_("Weekly draw was not found.")) from exc

    @classmethod
    def get_eligible_entries(cls, draw: WeeklyDraw | int):
        """Return the canonical eligible entrant queryset (`ASSIGNED`, non-deleted) for `draw`."""

        draw_id = getattr(draw, "pk", draw)
        return (
            WeeklyDrawEntry.objects.filter(
                draw_id=draw_id,
                status=WeeklyDrawEntryStatus.ASSIGNED,
                deleted__isnull=True,
            )
            .select_related("user", "draw")
            .order_by("created", "id")
        )

    @classmethod
    def _build_execution_freeze_payload(
        cls,
        draw: WeeklyDraw,
        *,
        frozen_at,
        actor_profile=None,
    ) -> dict:
        eligible_entries = list(cls.get_eligible_entries(draw))
        return {
            "eligible_entry_ids": [entry.pk for entry in eligible_entries],
            "eligible_user_ids": [entry.user_id for entry in eligible_entries],
            "eligible_entries_count": len(eligible_entries),
            "frozen_at": frozen_at.isoformat(),
            "frozen_by_profile_id": (
                actor_profile.pk if actor_profile is not None else None
            ),
            "winner_selection_status": cls.WINNER_SELECTION_STATUS_BLOCKED,
            "winners_selected_count": 0,
        }

    @classmethod
    def _freeze_draw_participants(
        cls,
        locked_draw: WeeklyDraw,
        *,
        now,
        actor_profile=None,
    ) -> dict:
        current_metadata = (
            dict(locked_draw.metadata)
            if isinstance(locked_draw.metadata, dict)
            else {}
        )
        existing_freeze = current_metadata.get("execution_freeze")
        if isinstance(existing_freeze, dict):
            return existing_freeze

        freeze_payload = cls._build_execution_freeze_payload(
            locked_draw,
            frozen_at=now,
            actor_profile=actor_profile,
        )
        current_metadata["execution_freeze"] = freeze_payload
        locked_draw.metadata = current_metadata
        return freeze_payload

    @classmethod
    def _validate_no_overlapping_open_draw(
        cls,
        *,
        starts_at,
        ends_at,
        exclude_pk=None,
    ):
        qs = WeeklyDraw.objects.filter(
            deleted__isnull=True,
            status=WeeklyDrawStatus.OPEN,
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
        )
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if qs.exists():
            raise ValidationError(
                {
                    "status": [
                        _(
                            "Another open weekly draw overlaps with this schedule window."
                        )
                    ]
                }
            )

    @classmethod
    def is_draw_eligible_for_assignment(
        cls,
        draw: WeeklyDraw | None,
        *,
        now=None,
    ) -> bool:
        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        if draw is None or draw.deleted is not None:
            return False
        if draw.status in (WeeklyDrawStatus.CLOSED, WeeklyDrawStatus.FINALIZED):
            return False
        if draw.ends_at <= now:
            return False
        if (
            draw.status == WeeklyDrawStatus.OPEN
            and draw.starts_at <= now < draw.ends_at
        ):
            return True
        if (
            draw.status in (WeeklyDrawStatus.OPEN, WeeklyDrawStatus.DRAFT)
            and draw.starts_at > now
        ):
            return True
        return False

    @classmethod
    def choose_eligible_draw(
        cls,
        *,
        now=None,
        lock: bool = False,
    ) -> WeeklyDraw | None:
        """Select the canonical eligible weekly draw at `now`.

        1. Prefer an `OPEN` draw where `starts_at <= now < ends_at`
           ordered by `starts_at ASC, id ASC`.
        2. Otherwise choose the earliest upcoming (`starts_at > now`) draw with
           `status in (OPEN, DRAFT)` ordered by `starts_at ASC, id ASC`.
        3. Never select ended (`ends_at <= now`), `CLOSED`, or `FINALIZED` draws.
        """

        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        base_qs = WeeklyDraw.objects.filter(deleted__isnull=True)
        if lock:
            base_qs = base_qs.select_for_update()

        current_open = (
            base_qs.filter(
                status=WeeklyDrawStatus.OPEN,
                starts_at__lte=now,
                ends_at__gt=now,
            )
            .order_by("starts_at", "id")
            .first()
        )
        if current_open is not None:
            return current_open

        upcoming = (
            base_qs.filter(
                status__in=(WeeklyDrawStatus.OPEN, WeeklyDrawStatus.DRAFT),
                starts_at__gt=now,
                ends_at__gt=now,
            )
            .order_by("starts_at", "id")
            .first()
        )
        return upcoming

    @classmethod
    def assign_pending_entries(
        cls,
        draw: WeeklyDraw | int | None = None,
        *,
        now=None,
    ) -> list[WeeklyDrawEntry]:
        """Atomically assign `PENDING` weekly draw entries to an eligible draw once."""

        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        with transaction.atomic():
            if draw is not None:
                target_draw = cls._get_locked_draw(draw)
                if not cls.is_draw_eligible_for_assignment(target_draw, now=now):
                    return []
            else:
                target_draw = cls.choose_eligible_draw(now=now, lock=True)
                if target_draw is None:
                    return []

            pending_entries = list(
                WeeklyDrawEntry.objects.select_for_update()
                .filter(
                    deleted__isnull=True,
                    status=WeeklyDrawEntryStatus.PENDING,
                    draw__isnull=True,
                )
                .order_by("created", "id")
            )
            if not pending_entries:
                return []

            for entry in pending_entries:
                entry.draw = target_draw
                entry.status = WeeklyDrawEntryStatus.ASSIGNED
                entry.assigned_at = now
                entry.save(
                    update_fields=["draw", "status", "assigned_at", "modified"]
                )
            return pending_entries

    @classmethod
    def create_draw(
        cls,
        draw_data=None,
        *,
        created_by=None,
        now=None,
        **kwargs,
    ) -> WeeklyDraw:
        data = dict(draw_data or {})
        data.update(kwargs)
        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")

        creator = (
            created_by
            if created_by is not None
            else data.pop("created_by", None)
        )
        creator_profile = cls._resolve_creator_profile(creator)

        title = cls._validate_title(data.get("title", ""))
        starts_at = cls._validate_aware_datetime(
            data.get("starts_at"),
            field_name="starts_at",
        )
        ends_at = cls._validate_aware_datetime(
            data.get("ends_at"),
            field_name="ends_at",
        )
        if starts_at >= ends_at:
            raise ValidationError(
                {
                    "ends_at": [
                        _("Weekly draw start time must be earlier than end time.")
                    ]
                }
            )

        status_value = data.get("status", WeeklyDrawStatus.DRAFT)
        if status_value not in WeeklyDrawStatus.values:
            raise ValidationError(
                {"status": [_("Invalid weekly draw status.")]}
            )
        if status_value not in cls.CREATABLE_STATUSES:
            raise ValidationError(
                {
                    "status": [
                        _(
                            "Weekly draws can only be created in DRAFT or OPEN status."
                        )
                    ]
                }
            )
        if status_value == WeeklyDrawStatus.OPEN and ends_at <= now:
            raise ValidationError(
                {
                    "ends_at": [
                        _(
                            "Cannot create an open weekly draw that has already ended."
                        )
                    ]
                }
            )

        metadata = cls._validate_metadata(data.get("metadata", {}))

        with transaction.atomic():
            if status_value == WeeklyDrawStatus.OPEN:
                cls._validate_no_overlapping_open_draw(
                    starts_at=starts_at,
                    ends_at=ends_at,
                )
            try:
                draw = WeeklyDraw.objects.create(
                    title=title,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    status=status_value,
                    created_by=creator_profile,
                    metadata=metadata,
                )
            except DjangoValidationError as exc:
                raise _translate_django_validation_error(exc) from exc

            cls.assign_pending_entries(now=now)
            return draw

    create = create_draw

    @classmethod
    def update_draw(
        cls,
        draw: WeeklyDraw | int,
        data=None,
        *,
        now=None,
        **kwargs,
    ) -> WeeklyDraw:
        changes = dict(data or {})
        changes.update(kwargs)
        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")

        with transaction.atomic():
            locked = cls._get_locked_draw(draw)
            if locked.status in (
                WeeklyDrawStatus.CLOSED,
                WeeklyDrawStatus.FINALIZED,
            ):
                raise ValidationError(
                    {
                        "status": [
                            _(
                                "Closed or finalized weekly draws cannot be modified."
                            )
                        ]
                    }
                )

            schedule_fields_submitted = cls.LOCKED_SCHEDULE_FIELDS & set(
                changes.keys()
            )
            if schedule_fields_submitted:
                has_entries = locked.entries.filter(deleted__isnull=True).exists()
                if now >= locked.starts_at or has_entries:
                    raise ValidationError(
                        {
                            field: [
                                _(
                                    "Weekly draw schedule cannot be modified after the draw has started or has assigned entries."
                                )
                            ]
                            for field in sorted(schedule_fields_submitted)
                        }
                    )

            new_starts_at = (
                cls._validate_aware_datetime(
                    changes["starts_at"], field_name="starts_at"
                )
                if "starts_at" in changes
                else locked.starts_at
            )
            new_ends_at = (
                cls._validate_aware_datetime(
                    changes["ends_at"], field_name="ends_at"
                )
                if "ends_at" in changes
                else locked.ends_at
            )
            if new_starts_at >= new_ends_at:
                raise ValidationError(
                    {
                        "ends_at": [
                            _(
                                "Weekly draw start time must be earlier than end time."
                            )
                        ]
                    }
                )

            new_status = changes.get("status", locked.status)
            if new_status not in WeeklyDrawStatus.values:
                raise ValidationError(
                    {"status": [_("Invalid weekly draw status.")]}
                )
            if new_status != locked.status:
                if (
                    locked.status == WeeklyDrawStatus.DRAFT
                    and new_status == WeeklyDrawStatus.OPEN
                ):
                    if new_ends_at <= now:
                        raise ValidationError(
                            {
                                "ends_at": [
                                    _(
                                        "Cannot open a weekly draw that has already ended."
                                    )
                                ]
                            }
                        )
                elif (
                    locked.status == WeeklyDrawStatus.OPEN
                    and new_status == WeeklyDrawStatus.CLOSED
                ):
                    if now < new_ends_at:
                        raise ValidationError(
                            {
                                "ends_at": [
                                    _(
                                        "Weekly draw cannot be closed before its end time."
                                    )
                                ]
                            }
                        )
                else:
                    raise ValidationError(
                        {
                            "status": [
                                _(
                                    "Invalid weekly draw status transition."
                                )
                            ]
                        }
                    )

            if new_status == WeeklyDrawStatus.OPEN:
                cls._validate_no_overlapping_open_draw(
                    starts_at=new_starts_at,
                    ends_at=new_ends_at,
                    exclude_pk=locked.pk,
                )

            update_fields = []
            if "title" in changes:
                locked.title = cls._validate_title(changes["title"])
                update_fields.append("title")
            if "starts_at" in changes:
                locked.starts_at = new_starts_at
                update_fields.append("starts_at")
            if "ends_at" in changes:
                locked.ends_at = new_ends_at
                update_fields.append("ends_at")
            if "metadata" in changes:
                locked.metadata = cls._validate_metadata(changes["metadata"])
                update_fields.append("metadata")
            if "status" in changes and new_status != locked.status:
                if new_status == WeeklyDrawStatus.CLOSED:
                    cls._freeze_draw_participants(locked, now=now)
                    if "metadata" not in update_fields:
                        update_fields.append("metadata")
                locked.status = new_status
                update_fields.append("status")

            if update_fields:
                update_fields.append("modified")
                try:
                    locked.save(update_fields=update_fields)
                except DjangoValidationError as exc:
                    raise _translate_django_validation_error(exc) from exc

            cls.assign_pending_entries(now=now)
            return locked

    update = update_draw

    @classmethod
    def open_draw(cls, draw: WeeklyDraw | int, *, now=None) -> WeeklyDraw:
        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        with transaction.atomic():
            locked = cls._get_locked_draw(draw)
            if locked.status != WeeklyDrawStatus.DRAFT:
                raise ValidationError(
                    {
                        "status": [
                            _("Only draft weekly draws can be opened.")
                        ]
                    }
                )
            if locked.ends_at <= now:
                raise ValidationError(
                    {
                        "ends_at": [
                            _("Cannot open a weekly draw that has already ended.")
                        ]
                    }
                )
            cls._validate_no_overlapping_open_draw(
                starts_at=locked.starts_at,
                ends_at=locked.ends_at,
                exclude_pk=locked.pk,
            )
            locked.status = WeeklyDrawStatus.OPEN
            try:
                locked.save(update_fields=["status", "modified"])
            except DjangoValidationError as exc:
                raise _translate_django_validation_error(exc) from exc
            cls.assign_pending_entries(now=now)
            return locked

    @classmethod
    def close_draw(
        cls,
        draw: WeeklyDraw | int,
        *,
        closed_by=None,
        now=None,
    ) -> WeeklyDraw:
        """Close an ended `OPEN` weekly draw (`now >= ends_at`) and freeze its `ASSIGNED` participants."""

        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        actor_profile = cls._resolve_creator_profile(closed_by)
        with transaction.atomic():
            locked = cls._get_locked_draw(draw)
            if locked.status != WeeklyDrawStatus.OPEN:
                raise ValidationError(
                    {
                        "status": [
                            _("Only open weekly draws can be closed.")
                        ]
                    }
                )
            if now < locked.ends_at:
                raise ValidationError(
                    {
                        "ends_at": [
                            _("Weekly draw cannot be closed before its end time.")
                        ]
                    }
                )
            cls._freeze_draw_participants(
                locked,
                now=now,
                actor_profile=actor_profile,
            )
            locked.status = WeeklyDrawStatus.CLOSED
            try:
                locked.save(update_fields=["status", "metadata", "modified"])
            except DjangoValidationError as exc:
                raise _translate_django_validation_error(exc) from exc
            return locked

    @classmethod
    def finalize_draw(
        cls,
        draw: WeeklyDraw | int,
        *,
        finalized_by=None,
        now=None,
    ) -> WeeklyDraw:
        """Execute the weekly draw finalization / freeze foundation (BE-DRW-02).

        Execution flow:
        1. Lock `WeeklyDraw` with `select_for_update()` inside `transaction.atomic()`.
        2. If `status == FINALIZED`, return the draw idempotently without mutation.
        3. Reject `DRAFT` draws and draws where `now < ends_at`.
        4. Freeze the eligible `ASSIGNED` participant set (`metadata["execution_freeze"]`)
           and transition `OPEN -> CLOSED` if not already closed.
        5. Winner-selection requirement gate (BE-DRW-02):
           Because winner count, selection algorithm, and prize definition are not
           specified in the canonical product source, keep `status = CLOSED` and
           return the frozen draw idempotently without fabricating a winner.
        """

        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        actor_profile = cls._resolve_creator_profile(finalized_by)
        with transaction.atomic():
            locked = cls._get_locked_draw(draw)
            if locked.status == WeeklyDrawStatus.FINALIZED:
                return locked

            if locked.status == WeeklyDrawStatus.DRAFT:
                raise ValidationError(
                    {
                        "status": [
                            _("Draft weekly draws cannot be finalized.")
                        ]
                    }
                )

            if now < locked.ends_at:
                raise ValidationError(
                    {
                        "ends_at": [
                            _(
                                "Weekly draw cannot be finalized before its end time."
                            )
                        ]
                    }
                )

            already_frozen = isinstance(locked.metadata, dict) and isinstance(
                locked.metadata.get("execution_freeze"), dict
            )
            if locked.status == WeeklyDrawStatus.CLOSED and already_frozen:
                return locked

            cls._freeze_draw_participants(
                locked,
                now=now,
                actor_profile=actor_profile,
            )
            update_fields = ["metadata", "modified"]
            if locked.status != WeeklyDrawStatus.CLOSED:
                locked.status = WeeklyDrawStatus.CLOSED
                update_fields.append("status")

            try:
                locked.save(update_fields=update_fields)
            except DjangoValidationError as exc:
                raise _translate_django_validation_error(exc) from exc
            return locked


class WeeklyDrawFinalizationService:
    """Dedicated facade for weekly draw execution freeze and finalization (BE-DRW-02)."""

    @staticmethod
    def get_eligible_entries(draw: WeeklyDraw | int):
        return WeeklyDrawService.get_eligible_entries(draw)

    @staticmethod
    def close_draw(draw: WeeklyDraw | int, *, closed_by=None, now=None) -> WeeklyDraw:
        return WeeklyDrawService.close_draw(
            draw,
            closed_by=closed_by,
            now=now,
        )

    @staticmethod
    def finalize_draw(
        draw: WeeklyDraw | int,
        *,
        finalized_by=None,
        now=None,
    ) -> WeeklyDraw:
        return WeeklyDrawService.finalize_draw(
            draw,
            finalized_by=finalized_by,
            now=now,
        )


class WeeklyDrawEntryService:
    """Account-level onboarding weekly draw entry service (BE-DRW-01).

    Invariants:
    - Owned by `UserAccount` (one `ONBOARDING` entry per `UserAccount`).
    - Idempotency key is `draw:onboarding:user:{user_id}`.
    - Automatic profile-creation hook (`ensure_for_first_profile_onboarding`)
      grants an entry only on the account's first-ever `Customer` or `Seller`
      profile in `Profile.all_objects` (including soft-deleted history).
    - Historical accounts creating a second mode later or recreating after
      soft-delete are never retroactively backfilled.
    - Manager accounts never receive onboarding weekly draw entries.
    - Creates 0 `PointTransaction` rows, 0 prizes, 0 notifications, and 0
      `PaymentRequest` rows.
    """

    @staticmethod
    def idempotency_key(user) -> str:
        user_id = getattr(user, "pk", user)
        return f"draw:onboarding:user:{user_id}"

    @staticmethod
    def _resolve_user(user):
        from apps.authentication.models import UserAccount

        if user is None or isinstance(user, bool):
            raise ValidationError({"user": [_("User account is required.")]})
        if isinstance(user, UserAccount):
            if user.pk is None:
                raise ValidationError({"user": [_("User account must be saved.")]})
            return user
        try:
            return UserAccount.objects.get(pk=int(user))
        except (UserAccount.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(
                {"user": [_("User account was not found.")]}
            ) from exc

    @staticmethod
    def _validate_profile_for_user(user, profile):
        if profile is None:
            return None
        if not isinstance(profile, Profile):
            try:
                profile = Profile.all_objects.get(
                    pk=int(getattr(profile, "pk", profile))
                )
            except (Profile.DoesNotExist, ValueError, TypeError) as exc:
                raise ValidationError(
                    {"profile": [_("Profile was not found.")]}
                ) from exc
        if profile.user_id != user.pk:
            raise ValidationError(
                {
                    "profile": [
                        _("Profile does not belong to the specified user account.")
                    ]
                }
            )
        if profile.type == ProfileType.MANAGER:
            raise ValidationError(
                {
                    "profile": [
                        _(
                            "Manager profiles cannot receive weekly onboarding draw entries."
                        )
                    ]
                }
            )
        return profile

    @classmethod
    def has_prior_onboarding_profile_history(
        cls,
        user,
        *,
        current_profile=None,
    ) -> bool:
        """Return True unless `user` has at most one historical Customer/Seller profile (live, non-deleted)."""

        resolved_user = cls._resolve_user(user)
        resolved_profile = cls._validate_profile_for_user(
            resolved_user, current_profile
        )
        return AccountOnboardingService.has_prior_customer_seller_profile_history(
            resolved_user,
            current_profile=resolved_profile,
        )

    @classmethod
    def ensure_for_onboarding(
        cls,
        user,
        profile=None,
        *,
        now=None,
    ) -> WeeklyDrawEntry:
        """Idempotently ensure the one-time onboarding weekly draw entry for `user`."""

        from apps.authentication.models import UserAccount

        resolved_user = cls._resolve_user(user)
        resolved_profile = cls._validate_profile_for_user(resolved_user, profile)
        now = WeeklyDrawService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        if Profile.all_objects.filter(
            user_id=resolved_user.pk,
            type=ProfileType.MANAGER,
            deleted__isnull=True,
        ).exists():
            raise ValidationError(
                {
                    "user": [
                        _(
                            "Manager accounts cannot receive weekly onboarding draw entries."
                        )
                    ]
                }
            )

        key = cls.idempotency_key(resolved_user)
        with transaction.atomic():
            locked_user = UserAccount.objects.select_for_update().get(
                pk=resolved_user.pk
            )
            existing = (
                WeeklyDrawEntry.objects.select_for_update()
                .select_related("draw", "user")
                .filter(
                    user=locked_user,
                    source=WeeklyDrawEntrySource.ONBOARDING,
                )
                .first()
            )
            if existing is not None:
                return existing

            eligible_draw = WeeklyDrawService.choose_eligible_draw(
                now=now,
                lock=True,
            )
            if eligible_draw is not None:
                entry_status = WeeklyDrawEntryStatus.ASSIGNED
                assigned_at = now
            else:
                entry_status = WeeklyDrawEntryStatus.PENDING
                assigned_at = None

            metadata = {
                "onboarding_profile_id": (
                    resolved_profile.pk if resolved_profile is not None else None
                ),
                "onboarding_profile_type": (
                    resolved_profile.type if resolved_profile is not None else None
                ),
            }
            try:
                return WeeklyDrawEntry.objects.create(
                    draw=eligible_draw,
                    user=locked_user,
                    source=WeeklyDrawEntrySource.ONBOARDING,
                    status=entry_status,
                    idempotency_key=key,
                    assigned_at=assigned_at,
                    metadata=metadata,
                )
            except IntegrityError:
                return WeeklyDrawEntry.objects.select_related(
                    "draw", "user"
                ).get(idempotency_key=key)

    @classmethod
    def ensure_for_first_profile_onboarding(
        cls,
        user,
        profile=None,
        *,
        now=None,
    ) -> WeeklyDrawEntry | None:
        """Automatic profile-creation hook: grant only on the account's first-ever Customer/Seller profile."""

        from apps.authentication.models import UserAccount

        resolved_user = cls._resolve_user(user)
        resolved_profile = cls._validate_profile_for_user(resolved_user, profile)
        now = WeeklyDrawService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        if Profile.all_objects.filter(
            user_id=resolved_user.pk,
            type=ProfileType.MANAGER,
            deleted__isnull=True,
        ).exists():
            raise ValidationError(
                {
                    "user": [
                        _(
                            "Manager accounts cannot receive weekly onboarding draw entries."
                        )
                    ]
                }
            )

        with transaction.atomic():
            locked_user = UserAccount.objects.select_for_update().get(
                pk=resolved_user.pk
            )
            if cls.has_prior_onboarding_profile_history(
                locked_user,
                current_profile=resolved_profile,
            ):
                return None
            return cls.ensure_for_onboarding(
                user=locked_user,
                profile=resolved_profile,
                now=now,
            )

    @classmethod
    def get_entry(cls, user) -> WeeklyDrawEntry | None:
        """Return the account's onboarding weekly draw entry without creating or backfilling."""

        resolved_user = cls._resolve_user(user)
        return (
            WeeklyDrawEntry.objects.select_related("draw", "user")
            .filter(
                user=resolved_user,
                source=WeeklyDrawEntrySource.ONBOARDING,
                deleted__isnull=True,
            )
            .first()
        )

    @classmethod
    def get_entry_status(cls, user) -> dict:
        """Return the safe non-mutating onboarding weekly draw status payload for `user`."""

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
        draw = entry.draw
        return {
            "has_entry": True,
            "status": entry.status,
            "draw_id": entry.draw_id,
            "draw_starts_at": draw.starts_at if draw is not None else None,
            "draw_ends_at": draw.ends_at if draw is not None else None,
            "assigned_at": entry.assigned_at,
        }
