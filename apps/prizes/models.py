import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel

FORBIDDEN_PRIZE_PII_KEYS = frozenset(
    {
        "address",
        "shipping_address",
        "claim_address",
        "delivery_address",
        "national_id",
        "identity_number",
        "id_number",
        "identity_photo",
        "claimant_name",
        "full_name",
        "phone_number",
    }
)


def _contains_forbidden_pii_keys(payload) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in FORBIDDEN_PRIZE_PII_KEYS:
                return True
            if _contains_forbidden_pii_keys(value):
                return True
    elif isinstance(payload, list):
        for item in payload:
            if _contains_forbidden_pii_keys(item):
                return True
    return False


class PrizeAwardSourceType(models.TextChoices):
    SEASON = "SEASON", _("Season")
    REFERRAL_CHALLENGE = "REFERRAL_CHALLENGE", _("Referral Challenge")
    LUCKY_WHEEL = "LUCKY_WHEEL", _("Lucky Wheel")


class PrizeAwardFulfillmentType(models.TextChoices):
    DIGITAL_AUTOMATIC = "DIGITAL_AUTOMATIC", _("Digital Automatic")
    CLAIM_REQUIRED = "CLAIM_REQUIRED", _("Claim Required")


class PrizeAwardStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    FULFILLED = "FULFILLED", _("Fulfilled")


class PrizeClaimStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", _("Submitted")
    REVIEWED = "REVIEWED", _("Reviewed")
    DELIVERED = "DELIVERED", _("Delivered")


class PrizeAward(BaseModel):
    """Canonical cross-domain prize award and fulfillment ledger (BE-PRZ-01).

    Invariants:
    - Owned by `UserAccount`; optional `profile` is contextual attribution only.
    - References the canonical source outcome (`source_type`, `source_id`) without
      replacing `LuckyWheelSpin`, `SeasonSellerInsight`, or `ReferralChallengeWinner`.
    - `DIGITAL_AUTOMATIC` awards are recorded only after the typed source service
      (`PointService`, `UserPromotionGrantService`, `BadgeService`) succeeds, with
      `status=FULFILLED` and `fulfilled_at` set.
    - `CLAIM_REQUIRED` awards are recorded with `status=PENDING` and
      `fulfilled_at=None`, without collecting PII (`BE-PRZ-02` owns claim submission
      and review/delivery).
    - `prize_snapshot` is descriptive/audit data only, never executable JSON.
    - Recipient, source identity, fulfillment type, snapshot, awarded timestamp,
      and idempotency key are immutable after creation.
    """

    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        related_name="prize_awards",
    )
    profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prize_awards",
    )
    source_type = models.CharField(
        max_length=32,
        choices=PrizeAwardSourceType.choices,
        db_index=True,
    )
    source_id = models.CharField(
        max_length=64,
        db_index=True,
    )
    fulfillment_type = models.CharField(
        max_length=32,
        choices=PrizeAwardFulfillmentType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=PrizeAwardStatus.choices,
        db_index=True,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    prize_snapshot = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=160, unique=True)
    awarded_at = models.DateTimeField(default=timezone.now)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-awarded_at", "-id")
        indexes = [
            models.Index(
                fields=["user", "-awarded_at", "-id"],
                name="prizes_award_user_awarded_idx",
            ),
            models.Index(
                fields=["source_type", "source_id"],
                name="prizes_award_source_idx",
            ),
            models.Index(
                fields=["status", "fulfillment_type"],
                name="prizes_award_status_ful_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(
                        fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
                        status=PrizeAwardStatus.FULFILLED,
                        fulfilled_at__isnull=False,
                    )
                    | Q(
                        fulfillment_type=PrizeAwardFulfillmentType.CLAIM_REQUIRED,
                        status=PrizeAwardStatus.PENDING,
                        fulfilled_at__isnull=True,
                    )
                    | Q(
                        fulfillment_type=PrizeAwardFulfillmentType.CLAIM_REQUIRED,
                        status=PrizeAwardStatus.FULFILLED,
                        fulfilled_at__isnull=False,
                    )
                ),
                name="prizes_award_status_fulfillment_consistency_ck",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"PrizeAward #{self.pk} ({self.source_type}:{self.source_id} -> "
            f"user={self.user_id}, {self.fulfillment_type}/{self.status})"
        )

    def clean(self):
        errors = {}

        if self.user_id is None:
            errors["user"] = _("Recipient user is required.")
        elif self.profile_id is not None:
            profile_user_id = getattr(self.profile, "user_id", None)
            if profile_user_id is not None and profile_user_id != self.user_id:
                errors["profile"] = _(
                    "Profile does not belong to the specified user."
                )

        if self.source_type not in PrizeAwardSourceType.values:
            errors["source_type"] = _("Invalid prize award source type.")

        source_id_str = str(self.source_id or "").strip()
        if not source_id_str:
            errors["source_id"] = _("Prize award source ID is required.")
        else:
            self.source_id = source_id_str

        if self.fulfillment_type not in PrizeAwardFulfillmentType.values:
            errors["fulfillment_type"] = _(
                "Invalid prize award fulfillment type."
            )

        if self.status not in PrizeAwardStatus.values:
            errors["status"] = _("Invalid prize award status.")

        title_str = str(self.title or "").strip()
        if not title_str:
            errors["title"] = _("Prize award title is required.")
        else:
            self.title = title_str

        idempotency_key_str = str(self.idempotency_key or "").strip()
        if not idempotency_key_str:
            errors["idempotency_key"] = _("Idempotency key is required.")
        else:
            self.idempotency_key = idempotency_key_str

        if self.prize_snapshot is None or not isinstance(
            self.prize_snapshot, dict
        ):
            errors["prize_snapshot"] = _(
                "Prize snapshot must be a JSON object."
            )
        elif _contains_forbidden_pii_keys(self.prize_snapshot):
            errors["prize_snapshot"] = _(
                "Prize snapshot cannot contain claim PII fields."
            )

        if self.metadata is None or not isinstance(self.metadata, dict):
            errors["metadata"] = _(
                "Prize award metadata must be a JSON object."
            )
        elif _contains_forbidden_pii_keys(self.metadata):
            errors["metadata"] = _(
                "Prize award metadata cannot contain claim PII fields."
            )

        if self.awarded_at is None:
            errors["awarded_at"] = _("Awarded timestamp is required.")
        elif timezone.is_naive(self.awarded_at):
            errors["awarded_at"] = _("Datetime value must be timezone-aware.")

        if self.fulfilled_at is not None and timezone.is_naive(
            self.fulfilled_at
        ):
            errors["fulfilled_at"] = _(
                "Datetime value must be timezone-aware."
            )

        if errors:
            raise DjangoValidationError(errors)

        if (
            self.fulfillment_type
            == PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC
        ):
            if self.status != PrizeAwardStatus.FULFILLED:
                raise DjangoValidationError(
                    {
                        "status": _(
                            "Digital automatic prize awards must have fulfilled status."
                        )
                    }
                )
            if self.fulfilled_at is None:
                raise DjangoValidationError(
                    {
                        "fulfilled_at": _(
                            "Fulfilled prize awards must have a fulfilled_at timestamp."
                        )
                    }
                )
        elif self.fulfillment_type == PrizeAwardFulfillmentType.CLAIM_REQUIRED:
            if self.pk is None and self.status != PrizeAwardStatus.PENDING:
                raise DjangoValidationError(
                    {
                        "status": _(
                            "Claim-required prize awards must start in pending status."
                        )
                    }
                )
            if self.pk is not None and self.status == PrizeAwardStatus.FULFILLED:
                if not PrizeClaim.objects.filter(
                    award_id=self.pk,
                    status=PrizeClaimStatus.DELIVERED,
                ).exists():
                    raise DjangoValidationError(
                        {
                            "status": _(
                                "Claim-required prize awards can only be fulfilled when their claim is delivered."
                            )
                        }
                    )

        if self.status == PrizeAwardStatus.FULFILLED:
            if self.fulfilled_at is None:
                raise DjangoValidationError(
                    {
                        "fulfilled_at": _(
                            "Fulfilled prize awards must have a fulfilled_at timestamp."
                        )
                    }
                )
        elif self.status == PrizeAwardStatus.PENDING:
            if self.fulfilled_at is not None:
                raise DjangoValidationError(
                    {
                        "fulfilled_at": _(
                            "Pending prize awards cannot have a fulfilled_at timestamp."
                        )
                    }
                )

        if self.pk is not None:
            original = PrizeAward.all_objects.filter(pk=self.pk).first()
            if original is not None:
                immutable_errors = {}
                if self.user_id != original.user_id:
                    immutable_errors["user"] = _(
                        "Prize award recipient cannot be modified after creation."
                    )
                if self.profile_id != original.profile_id:
                    immutable_errors["profile"] = _(
                        "Prize award profile attribution cannot be modified after creation."
                    )
                if self.source_type != original.source_type:
                    immutable_errors["source_type"] = _(
                        "Prize award source identity cannot be modified after creation."
                    )
                if self.source_id != original.source_id:
                    immutable_errors["source_id"] = _(
                        "Prize award source identity cannot be modified after creation."
                    )
                if self.fulfillment_type != original.fulfillment_type:
                    immutable_errors["fulfillment_type"] = _(
                        "Prize award fulfillment type cannot be modified after creation."
                    )
                if self.prize_snapshot != original.prize_snapshot:
                    immutable_errors["prize_snapshot"] = _(
                        "Prize award snapshot cannot be modified after creation."
                    )
                if self.awarded_at != original.awarded_at:
                    immutable_errors["awarded_at"] = _(
                        "Prize award timestamp cannot be modified after creation."
                    )
                if self.idempotency_key != original.idempotency_key:
                    immutable_errors["idempotency_key"] = _(
                        "Prize award idempotency key cannot be modified after creation."
                    )
                if original.status == PrizeAwardStatus.FULFILLED and (
                    self.status != PrizeAwardStatus.FULFILLED
                    or self.fulfilled_at != original.fulfilled_at
                ):
                    immutable_errors["status"] = _(
                        "Fulfilled prize awards cannot be reverted."
                    )
                if immutable_errors:
                    raise DjangoValidationError(immutable_errors)

    def save(self, *args, **kwargs):
        if isinstance(self.prize_snapshot, dict):
            self.prize_snapshot = copy.deepcopy(self.prize_snapshot)
        if isinstance(self.metadata, dict):
            self.metadata = copy.deepcopy(self.metadata)
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            _("Prize award ledger records cannot be deleted."),
            [self],
        )


class PrizeClaim(BaseModel):
    """Claim submission and fulfillment lifecycle for CLAIM_REQUIRED awards (BE-PRZ-02).

    Invariants:
    - Exactly one `PrizeClaim` per `CLAIM_REQUIRED` `PrizeAward` (`OneToOneField`).
    - Submitted claimant fields (`full_name`, `address`, `identity_number`) are
      stored only on `PrizeClaim`, never on `PrizeAward.prize_snapshot` or `metadata`,
      and are immutable after submission.
    - Lifecycle is strictly `SUBMITTED -> REVIEWED -> DELIVERED`.
    - `DELIVERED` is terminal and transitions the linked `PrizeAward` to `FULFILLED`
      with `fulfilled_at = delivered_at`.
    """

    award = models.OneToOneField(
        PrizeAward,
        on_delete=models.PROTECT,
        related_name="claim",
    )
    full_name = models.CharField(max_length=255)
    address = models.TextField()
    identity_number = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20,
        choices=PrizeClaimStatus.choices,
        default=PrizeClaimStatus.SUBMITTED,
        db_index=True,
    )
    submitted_at = models.DateTimeField(db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_prize_claims",
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="delivered_prize_claims",
    )

    class Meta:
        ordering = ["-submitted_at", "-id"]
        verbose_name = _("Prize claim")
        verbose_name_plural = _("Prize claims")
        indexes = [
            models.Index(
                fields=["status", "-submitted_at", "-id"],
                name="prize_claim_status_sub_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"PrizeClaim<{self.pk}> award={self.award_id} status={self.status}"
        )

    def clean(self):
        super().clean()
        errors = {}

        if self.award_id is None:
            errors["award"] = _("Prize award is required.")
        elif self.award is not None and (
            self.award.fulfillment_type
            != PrizeAwardFulfillmentType.CLAIM_REQUIRED
        ):
            errors["award"] = _(
                "Claims are only supported for claim-required prize awards."
            )

        full_name_str = str(self.full_name or "").strip()
        if not full_name_str:
            errors["full_name"] = _("Full name is required.")
        elif len(full_name_str) > 255:
            errors["full_name"] = _(
                "Full name cannot exceed 255 characters."
            )
        else:
            self.full_name = full_name_str

        address_str = str(self.address or "").strip()
        if not address_str:
            errors["address"] = _("Address is required.")
        else:
            self.address = address_str

        identity_str = str(self.identity_number or "").strip()
        if not identity_str:
            errors["identity_number"] = _("Identity number is required.")
        elif len(identity_str) > 100:
            errors["identity_number"] = _(
                "Identity number cannot exceed 100 characters."
            )
        else:
            self.identity_number = identity_str

        if self.status not in PrizeClaimStatus.values:
            errors["status"] = _("Invalid prize claim status.")

        if self.submitted_at is None:
            errors["submitted_at"] = _("Submitted timestamp is required.")
        elif timezone.is_naive(self.submitted_at):
            errors["submitted_at"] = _(
                "Datetime value must be timezone-aware."
            )

        if self.reviewed_at is not None and timezone.is_naive(
            self.reviewed_at
        ):
            errors["reviewed_at"] = _("Datetime value must be timezone-aware.")

        if self.delivered_at is not None and timezone.is_naive(
            self.delivered_at
        ):
            errors["delivered_at"] = _(
                "Datetime value must be timezone-aware."
            )

        if errors:
            raise DjangoValidationError(errors)

        if self.pk is not None:
            original = PrizeClaim.all_objects.filter(pk=self.pk).first()
            if original is not None:
                immutable_errors = {}
                if self.award_id != original.award_id:
                    immutable_errors["award"] = _(
                        "Claim prize award cannot be modified after submission."
                    )
                if self.full_name != original.full_name:
                    immutable_errors["full_name"] = _(
                        "Claimant full name cannot be modified after submission."
                    )
                if self.address != original.address:
                    immutable_errors["address"] = _(
                        "Claimant address cannot be modified after submission."
                    )
                if self.identity_number != original.identity_number:
                    immutable_errors["identity_number"] = _(
                        "Claimant identity number cannot be modified after submission."
                    )
                if self.submitted_at != original.submitted_at:
                    immutable_errors["submitted_at"] = _(
                        "Claim submitted timestamp cannot be modified after submission."
                    )
                if (
                    original.status == PrizeClaimStatus.SUBMITTED
                    and self.status == PrizeClaimStatus.DELIVERED
                ):
                    immutable_errors["status"] = _(
                        "Claims must be reviewed before they can be marked delivered."
                    )
                if original.status in (
                    PrizeClaimStatus.REVIEWED,
                    PrizeClaimStatus.DELIVERED,
                ):
                    if self.status == PrizeClaimStatus.SUBMITTED:
                        immutable_errors["status"] = _(
                            "Reviewed or delivered claims cannot revert to submitted."
                        )
                    if self.reviewed_at != original.reviewed_at:
                        immutable_errors["reviewed_at"] = _(
                            "Claim review timestamp cannot be modified after review."
                        )
                    if self.reviewed_by_id != original.reviewed_by_id:
                        immutable_errors["reviewed_by"] = _(
                            "Claim reviewer cannot be modified after review."
                        )
                if original.status == PrizeClaimStatus.DELIVERED:
                    if self.status != PrizeClaimStatus.DELIVERED:
                        immutable_errors["status"] = _(
                            "Delivered claims cannot be reverted."
                        )
                    if self.delivered_at != original.delivered_at:
                        immutable_errors["delivered_at"] = _(
                            "Claim delivery timestamp cannot be modified after delivery."
                        )
                    if self.delivered_by_id != original.delivered_by_id:
                        immutable_errors["delivered_by"] = _(
                            "Claim deliverer cannot be modified after delivery."
                        )
                if immutable_errors:
                    raise DjangoValidationError(immutable_errors)

        if self.status == PrizeClaimStatus.SUBMITTED:
            if (
                self.reviewed_at is not None
                or self.reviewed_by_id is not None
                or self.delivered_at is not None
                or self.delivered_by_id is not None
            ):
                raise DjangoValidationError(
                    {
                        "status": _(
                            "Submitted claims cannot have review or delivery metadata."
                        )
                    }
                )
        elif self.status == PrizeClaimStatus.REVIEWED:
            if self.reviewed_at is None or self.reviewed_by_id is None:
                raise DjangoValidationError(
                    {
                        "reviewed_at": _(
                            "Reviewed claims must record reviewed_at and reviewed_by."
                        )
                    }
                )
            if (
                self.delivered_at is not None
                or self.delivered_by_id is not None
            ):
                raise DjangoValidationError(
                    {
                        "delivered_at": _(
                            "Reviewed claims cannot have delivery metadata before delivery."
                        )
                    }
                )
        elif self.status == PrizeClaimStatus.DELIVERED:
            if self.reviewed_at is None or self.reviewed_by_id is None:
                raise DjangoValidationError(
                    {
                        "status": _(
                            "Claims must be reviewed before they can be marked delivered."
                        )
                    }
                )
            if self.delivered_at is None or self.delivered_by_id is None:
                raise DjangoValidationError(
                    {
                        "delivered_at": _(
                            "Delivered claims must record delivered_at and delivered_by."
                        )
                    }
                )


    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            _("Prize claim records cannot be deleted."),
            [self],
        )


