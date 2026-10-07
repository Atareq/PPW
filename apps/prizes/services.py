import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.prizes.models import (
    PrizeAward,
    PrizeAwardFulfillmentType,
    PrizeAwardSourceType,
    PrizeAwardStatus,
    PrizeClaim,
    PrizeClaimStatus,
    _contains_forbidden_pii_keys,
)
from apps.users.models import Profile


def _translate_django_validation_error(
    exc: DjangoValidationError,
) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


class PrizeAwardService:
    """Narrow service for recording and querying canonical cross-domain prize awards (BE-PRZ-01).

    Invariants:
    - `record_digital_fulfilled()` records a `DIGITAL_AUTOMATIC` / `FULFILLED` award
      after the source domain's typed digital reward (`PointService`, `UserPromotionGrantService`,
      or `BadgeService`) has already succeeded inside the same transaction. It never
      executes a reward mutation itself.
    - `record_claim_required()` records a `CLAIM_REQUIRED` / `PENDING` award (`fulfilled_at=None`)
      without collecting or storing claim PII.
    - Both operations serialize on `UserAccount.objects.select_for_update()`, use an inner
      savepoint for unique-key recovery, return the existing award idempotently on same-payload
      replay, and reject cross-user or cross-source idempotency collisions with `400 ValidationError`.
    """

    @staticmethod
    @transaction.atomic
    def record_digital_fulfilled(
        *,
        user,
        profile=None,
        source_type: str,
        source_id,
        title: str,
        description: str = "",
        prize_snapshot: dict,
        idempotency_key: str,
        awarded_at=None,
        fulfilled_at=None,
        metadata: dict | None = None,
    ) -> PrizeAward:
        now = timezone.now()
        effective_awarded_at = awarded_at or now
        effective_fulfilled_at = fulfilled_at or effective_awarded_at
        return PrizeAwardService._record_award(
            user=user,
            profile=profile,
            source_type=source_type,
            source_id=source_id,
            fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
            status=PrizeAwardStatus.FULFILLED,
            title=title,
            description=description,
            prize_snapshot=prize_snapshot,
            idempotency_key=idempotency_key,
            awarded_at=effective_awarded_at,
            fulfilled_at=effective_fulfilled_at,
            metadata=metadata,
        )

    @staticmethod
    @transaction.atomic
    def record_claim_required(
        *,
        user,
        profile=None,
        source_type: str,
        source_id,
        title: str,
        description: str = "",
        prize_snapshot: dict,
        idempotency_key: str,
        awarded_at=None,
        metadata: dict | None = None,
    ) -> PrizeAward:
        now = timezone.now()
        effective_awarded_at = awarded_at or now
        return PrizeAwardService._record_award(
            user=user,
            profile=profile,
            source_type=source_type,
            source_id=source_id,
            fulfillment_type=PrizeAwardFulfillmentType.CLAIM_REQUIRED,
            status=PrizeAwardStatus.PENDING,
            title=title,
            description=description,
            prize_snapshot=prize_snapshot,
            idempotency_key=idempotency_key,
            awarded_at=effective_awarded_at,
            fulfilled_at=None,
            metadata=metadata,
        )

    @staticmethod
    def list_for_user(user):
        return (
            PrizeAward.objects.filter(
                user=user,
                deleted__isnull=True,
            )
            .select_related("user", "profile", "claim")
            .order_by("-awarded_at", "-id")
        )

    @staticmethod
    def _record_award(
        *,
        user,
        profile=None,
        source_type: str,
        source_id,
        fulfillment_type: str,
        status: str,
        title: str,
        description: str = "",
        prize_snapshot: dict,
        idempotency_key: str,
        awarded_at,
        fulfilled_at,
        metadata: dict | None = None,
    ) -> PrizeAward:
        from apps.authentication.models import UserAccount

        if user is None or getattr(user, "pk", None) is None:
            raise ValidationError({"user": _("Recipient user is required.")})

        resolved_profile = None
        if profile is not None:
            if getattr(profile, "pk", None) is None:
                raise ValidationError({"profile": _("Invalid profile.")})
            resolved_profile = Profile.all_objects.filter(pk=profile.pk).first()
            if resolved_profile is None:
                raise ValidationError({"profile": _("Invalid profile.")})
            if resolved_profile.user_id != user.pk:
                raise ValidationError(
                    {
                        "profile": _(
                            "Profile does not belong to the specified user."
                        )
                    }
                )

        if source_type not in PrizeAwardSourceType.values:
            raise ValidationError(
                {"source_type": _("Invalid prize award source type.")}
            )

        source_id_str = str(source_id if source_id is not None else "").strip()
        if not source_id_str:
            raise ValidationError(
                {"source_id": _("Prize award source ID is required.")}
            )

        if fulfillment_type not in PrizeAwardFulfillmentType.values:
            raise ValidationError(
                {"fulfillment_type": _("Invalid prize award fulfillment type.")}
            )

        if status not in PrizeAwardStatus.values:
            raise ValidationError({"status": _("Invalid prize award status.")})

        title_str = str(title or "").strip()
        if not title_str:
            raise ValidationError(
                {"title": _("Prize award title is required.")}
            )

        idempotency_key_str = str(idempotency_key or "").strip()
        if not idempotency_key_str:
            raise ValidationError(
                {"idempotency_key": _("Idempotency key is required.")}
            )

        if prize_snapshot is None or not isinstance(prize_snapshot, dict):
            raise ValidationError(
                {"prize_snapshot": _("Prize snapshot must be a JSON object.")}
            )
        if _contains_forbidden_pii_keys(prize_snapshot):
            raise ValidationError(
                {
                    "prize_snapshot": _(
                        "Prize snapshot cannot contain claim PII fields."
                    )
                }
            )

        resolved_metadata = {} if metadata is None else metadata
        if not isinstance(resolved_metadata, dict):
            raise ValidationError(
                {"metadata": _("Prize award metadata must be a JSON object.")}
            )
        if _contains_forbidden_pii_keys(resolved_metadata):
            raise ValidationError(
                {
                    "metadata": _(
                        "Prize award metadata cannot contain claim PII fields."
                    )
                }
            )

        UserAccount.objects.select_for_update().get(pk=user.pk)

        existing = PrizeAward.all_objects.filter(
            idempotency_key=idempotency_key_str
        ).first()
        if existing is not None:
            return PrizeAwardService._validate_existing_award_match(
                existing,
                user_id=user.pk,
                profile_id=resolved_profile.pk if resolved_profile else None,
                source_type=source_type,
                source_id=source_id_str,
                fulfillment_type=fulfillment_type,
            )

        try:
            with transaction.atomic():
                award = PrizeAward.objects.create(
                    user=user,
                    profile=resolved_profile,
                    source_type=source_type,
                    source_id=source_id_str,
                    fulfillment_type=fulfillment_type,
                    status=status,
                    title=title_str,
                    description=str(description or ""),
                    prize_snapshot=copy.deepcopy(prize_snapshot),
                    idempotency_key=idempotency_key_str,
                    awarded_at=awarded_at,
                    fulfilled_at=fulfilled_at,
                    metadata=copy.deepcopy(resolved_metadata),
                )
        except DjangoValidationError as exc:
            existing = PrizeAward.all_objects.filter(
                idempotency_key=idempotency_key_str
            ).first()
            if existing is not None:
                return PrizeAwardService._validate_existing_award_match(
                    existing,
                    user_id=user.pk,
                    profile_id=(
                        resolved_profile.pk if resolved_profile else None
                    ),
                    source_type=source_type,
                    source_id=source_id_str,
                    fulfillment_type=fulfillment_type,
                )
            raise _translate_django_validation_error(exc) from exc
        except IntegrityError:
            existing = PrizeAward.all_objects.get(
                idempotency_key=idempotency_key_str
            )
            return PrizeAwardService._validate_existing_award_match(
                existing,
                user_id=user.pk,
                profile_id=resolved_profile.pk if resolved_profile else None,
                source_type=source_type,
                source_id=source_id_str,
                fulfillment_type=fulfillment_type,
            )

        if fulfillment_type == PrizeAwardFulfillmentType.CLAIM_REQUIRED:
            PrizeAwardService._create_claim_required_notification(award)
        return award

    @staticmethod
    def _create_claim_required_notification(award: PrizeAward):
        from apps.loyalty.services import _resolve_reward_recipient_profile
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        recipient_profile = _resolve_reward_recipient_profile(
            award.user,
            award.profile,
        )
        if recipient_profile is None:
            return None

        return NotificationService.create_notification(
            recipient_profile=recipient_profile,
            notification_type=NotificationType.PRIZE_AWARDED,
            title=_("Congratulations! You are a winner"),
            body=f"Congratulations! You won a prize: {award.title}.",
            object_type="prize_award",
            object_id=award.pk,
            metadata={
                "prize_award_id": award.pk,
                "source_type": award.source_type,
                "source_id": award.source_id,
                "fulfillment_type": award.fulfillment_type,
                "title": award.title,
            },
            idempotency_key=f"prize_awarded:{award.pk}",
        )

    @staticmethod
    def _validate_existing_award_match(
        existing: PrizeAward,
        *,
        user_id: int,
        profile_id: int | None,
        source_type: str,
        source_id: str,
        fulfillment_type: str,
    ) -> PrizeAward:
        if (
            existing.user_id != user_id
            or existing.source_type != source_type
            or existing.source_id != source_id
            or existing.fulfillment_type != fulfillment_type
            or (
                existing.profile_id is not None
                and profile_id is not None
                and existing.profile_id != profile_id
            )
        ):
            raise ValidationError(
                {
                    "idempotency_key": _(
                        "Idempotency key has already been used for a different prize award."
                    )
                }
            )
        return existing


class PrizeClaimService:
    """Narrow service for claim submission, manager review, and delivery (BE-PRZ-02).

    Invariants:
    - `submit_claim()` locks the `PrizeAward` row, verifies recipient ownership,
      `fulfillment_type=CLAIM_REQUIRED`, `status=PENDING`, and absence of an existing
      claim, and creates a `SUBMITTED` `PrizeClaim` while keeping `PrizeAward.status=PENDING`.
    - `review_claim()` locks the claim and award rows, transitions `SUBMITTED -> REVIEWED`,
      and is idempotent if already `REVIEWED`.
    - `deliver_claim()` locks the claim and award rows, requires `REVIEWED` before
      transitioning `REVIEWED -> DELIVERED`, atomically sets `PrizeAward.status=FULFILLED`
      and `fulfilled_at=delivered_at`, and is idempotent if already `DELIVERED`.
    - Zero side effects on PP Coins, promotions, badges, payment requests, or notifications.
    """

    @staticmethod
    def list_for_admin():
        return (
            PrizeClaim.objects.filter(deleted__isnull=True)
            .select_related(
                "award",
                "award__user",
                "award__profile",
                "reviewed_by",
                "delivered_by",
            )
            .order_by("-submitted_at", "-id")
        )

    @staticmethod
    @transaction.atomic
    def submit_claim(
        user,
        award_id: int,
        *,
        full_name: str,
        address: str,
        identity_number: str,
    ) -> PrizeClaim:
        if (
            user is None
            or getattr(user, "pk", None) is None
            or not getattr(user, "is_authenticated", False)
        ):
            raise PermissionDenied(_("Authentication is required."))

        field_errors = {}
        normalized_full_name = str(full_name or "").strip()
        if not normalized_full_name:
            field_errors["full_name"] = _("Full name is required.")
        elif len(normalized_full_name) > 255:
            field_errors["full_name"] = _(
                "Full name cannot exceed 255 characters."
            )

        normalized_address = str(address or "").strip()
        if not normalized_address:
            field_errors["address"] = _("Address is required.")

        normalized_identity = str(identity_number or "").strip()
        if not normalized_identity:
            field_errors["identity_number"] = _("Identity number is required.")
        elif len(normalized_identity) > 100:
            field_errors["identity_number"] = _(
                "Identity number cannot exceed 100 characters."
            )

        if field_errors:
            raise ValidationError(field_errors)

        award = (
            PrizeAward.objects.select_for_update()
            .filter(pk=award_id, deleted__isnull=True)
            .first()
        )
        if award is None or award.user_id != user.pk:
            raise Http404(_("Prize award not found."))

        if award.fulfillment_type != PrizeAwardFulfillmentType.CLAIM_REQUIRED:
            raise ValidationError(
                {"detail": _("This prize award does not require a claim.")}
            )

        if PrizeClaim.all_objects.filter(award_id=award.pk).exists():
            raise ValidationError(
                {
                    "detail": _(
                        "A claim has already been submitted for this prize award."
                    )
                }
            )

        if award.status != PrizeAwardStatus.PENDING:
            raise ValidationError(
                {"detail": _("This prize award has already been fulfilled.")}
            )

        try:
            with transaction.atomic():
                return PrizeClaim.objects.create(
                    award=award,
                    full_name=normalized_full_name,
                    address=normalized_address,
                    identity_number=normalized_identity,
                    status=PrizeClaimStatus.SUBMITTED,
                    submitted_at=timezone.now(),
                )
        except DjangoValidationError as exc:
            if PrizeClaim.all_objects.filter(award_id=award.pk).exists():
                raise ValidationError(
                    {
                        "detail": _(
                            "A claim has already been submitted for this prize award."
                        )
                    }
                ) from exc
            raise _translate_django_validation_error(exc) from exc
        except IntegrityError as exc:
            raise ValidationError(
                {
                    "detail": _(
                        "A claim has already been submitted for this prize award."
                    )
                }
            ) from exc

    @staticmethod
    @transaction.atomic
    def review_claim(claim_id: int, reviewer_user) -> PrizeClaim:
        if (
            reviewer_user is None
            or getattr(reviewer_user, "pk", None) is None
            or not getattr(reviewer_user, "is_authenticated", False)
        ):
            raise PermissionDenied(_("Authentication is required."))

        claim = (
            PrizeClaim.objects.select_for_update()
            .select_related("award")
            .filter(pk=claim_id, deleted__isnull=True)
            .first()
        )
        if claim is None:
            raise Http404(_("Prize claim not found."))

        award = PrizeAward.objects.select_for_update().get(pk=claim.award_id)
        claim.award = award

        if claim.status == PrizeClaimStatus.REVIEWED:
            return claim

        if claim.status == PrizeClaimStatus.DELIVERED:
            raise ValidationError(
                {"detail": _("Delivered claims cannot be reviewed again.")}
            )

        if claim.status != PrizeClaimStatus.SUBMITTED:
            raise ValidationError(
                {"detail": _("Only submitted claims can be reviewed.")}
            )

        claim.status = PrizeClaimStatus.REVIEWED
        claim.reviewed_at = timezone.now()
        claim.reviewed_by = reviewer_user
        try:
            claim.save(
                update_fields=[
                    "status",
                    "reviewed_at",
                    "reviewed_by",
                    "modified",
                ]
            )
        except DjangoValidationError as exc:
            raise _translate_django_validation_error(exc) from exc

        claim.award = award
        return claim

    @staticmethod
    @transaction.atomic
    def deliver_claim(claim_id: int, deliverer_user) -> PrizeClaim:
        if (
            deliverer_user is None
            or getattr(deliverer_user, "pk", None) is None
            or not getattr(deliverer_user, "is_authenticated", False)
        ):
            raise PermissionDenied(_("Authentication is required."))

        claim = (
            PrizeClaim.objects.select_for_update()
            .select_related("award")
            .filter(pk=claim_id, deleted__isnull=True)
            .first()
        )
        if claim is None:
            raise Http404(_("Prize claim not found."))

        award = PrizeAward.objects.select_for_update().get(pk=claim.award_id)
        claim.award = award

        if claim.status == PrizeClaimStatus.DELIVERED:
            return claim

        if claim.status != PrizeClaimStatus.REVIEWED:
            raise ValidationError(
                {
                    "detail": _(
                        "Claims must be reviewed before they can be marked delivered."
                    )
                }
            )

        now = timezone.now()
        claim.status = PrizeClaimStatus.DELIVERED
        claim.delivered_at = now
        claim.delivered_by = deliverer_user
        try:
            claim.save(
                update_fields=[
                    "status",
                    "delivered_at",
                    "delivered_by",
                    "modified",
                ]
            )
            award.status = PrizeAwardStatus.FULFILLED
            award.fulfilled_at = now
            award.save(update_fields=["status", "fulfilled_at", "modified"])
        except DjangoValidationError as exc:
            raise _translate_django_validation_error(exc) from exc

        claim.award = award
        return claim


