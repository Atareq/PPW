from datetime import timedelta
import secrets

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from apps.loyalty.constants import BadgeType, RewardSourceType
from apps.loyalty.services import BadgeService, PointService
from apps.lucky_wheel.models import (
    LuckyWheelGrant,
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
    LuckyWheelSpin,
    LuckyWheelSpinStatus,
)
from apps.promotions.models import (
    CashbackOffer,
    DiscountOffer,
    OfferTriggerType,
)
from apps.promotions.serializers import UserPromotionGrantSerializer
from apps.promotions.services import UserPromotionGrantService
from apps.users.constants import ProfileType
from apps.users.models import Profile

_SYSTEM_RANDOM = secrets.SystemRandom()


def _translate_django_validation_error(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


class LuckyWheelPrizeSelector:
    """Cryptographically backed weighted prize selector with injectable RNG for tests (BE-LW-02)."""

    @staticmethod
    def select(candidates, *, rng=None):
        """Select one prize from ordered candidates proportionally to positive integer weights.

        `candidates` must be ordered deterministically (`id` ascending).
        `rng` defaults to `secrets.SystemRandom()` and may be either:
        - an object exposing `.randint(1, total_weight)` (e.g. `secrets.SystemRandom` or `random.Random`), or
        - a callable `rng(total_weight)` returning a 1-based integer in `[1, total_weight]`.
        """

        valid_candidates = [
            prize
            for prize in candidates
            if (
                prize.is_active
                and prize.deleted is None
                and prize.weight is not None
                and not isinstance(prize.weight, bool)
                and prize.weight > 0
                and (
                    prize.quantity_total is None
                    or prize.quantity_awarded < prize.quantity_total
                )
            )
        ]
        if not valid_candidates:
            raise ValidationError(
                {"detail": _("No lucky wheel prizes are available.")}
            )

        total_weight = sum(prize.weight for prize in valid_candidates)
        if total_weight <= 0:
            raise ValidationError(
                {"detail": _("No lucky wheel prizes are available.")}
            )

        pick = LuckyWheelPrizeSelector._draw_pick(total_weight, rng=rng)
        running = 0
        for prize in valid_candidates:
            running += prize.weight
            if pick <= running:
                return prize
        return valid_candidates[-1]

    @staticmethod
    def _draw_pick(total_weight: int, *, rng=None) -> int:
        chooser = rng if rng is not None else _SYSTEM_RANDOM
        if callable(chooser) and not hasattr(chooser, "randint"):
            pick = chooser(total_weight)
        else:
            pick = chooser.randint(1, total_weight)
        if isinstance(pick, bool) or not isinstance(pick, int):
            raise ValidationError(
                {"detail": _("Random selection pick is out of bounds.")}
            )
        if pick < 1 or pick > total_weight:
            raise ValidationError(
                {"detail": _("Random selection pick is out of bounds.")}
            )
        return pick


class LuckyWheelGrantService:
    """Create and read Lucky Wheel attempt grants without mutating the PP Coin wallet (BE-LW-01)."""

    @staticmethod
    def grant_onboarding_attempt(profile):
        """Legacy helper retained only for historical compatibility.

        Fresh account/profile onboarding no longer calls this method (BE-LW-01 / BE-DRW-CANCEL-01).
        """

        return LuckyWheelGrantService.grant_attempt(
            profile=profile,
            source_type=LuckyWheelGrantSourceType.ONBOARDING,
            source_id=str(profile.pk),
            attempts=1,
            idempotency_key=f"lucky_wheel:onboarding:{profile.pk}",
        )

    @staticmethod
    def grant_for_auction_completed(winner_profile, auction_item):
        """Grant the winning buyer profile one attempt after a completed auction deal."""

        if winner_profile is None or getattr(winner_profile, "pk", None) is None:
            raise ValidationError(
                {"profile": _("A valid winner profile is required.")}
            )
        if getattr(winner_profile, "type", None) != ProfileType.CUSTOMER:
            raise ValidationError(
                {
                    "profile": _(
                        "Only buyer customer profiles can receive auction completion wheel attempts."
                    )
                }
            )
        if auction_item is None or getattr(auction_item, "pk", None) is None:
            raise ValidationError(
                {"auction_item": _("A valid auction item is required.")}
            )

        return LuckyWheelGrantService.grant_attempt(
            profile=winner_profile,
            source_type=LuckyWheelGrantSourceType.AUCTION_COMPLETED,
            source_id=str(auction_item.pk),
            attempts=1,
            idempotency_key=(
                f"lucky_wheel:auction_completed:"
                f"{auction_item.pk}:{winner_profile.pk}"
            ),
        )

    @staticmethod
    @transaction.atomic
    def grant_attempt(
        *,
        profile,
        source_type,
        source_id="",
        attempts=1,
        expires_at=None,
        metadata=None,
        idempotency_key=None,
    ):
        """Create an attempt grant once for a deterministic source/profile idempotency key."""

        from apps.authentication.models import UserAccount

        if profile is None or getattr(profile, "pk", None) is None:
            raise ValidationError({"profile": _("Profile is required.")})
        if isinstance(attempts, bool):
            raise ValidationError(
                {"attempts": _("Attempts must be greater than 0.")}
            )
        try:
            attempts = int(attempts)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"attempts": _("Attempts must be greater than 0.")}
            ) from exc
        if attempts <= 0:
            raise ValidationError(
                {"attempts": _("Attempts must be greater than 0.")}
            )
        if source_type not in LuckyWheelGrantSourceType.values:
            raise ValidationError(
                {"source_type": _("Invalid lucky wheel grant source type.")}
            )

        profile = Profile.objects.select_related("user").get(pk=profile.pk)
        if profile.type == ProfileType.MANAGER:
            raise ValidationError(
                {
                    "profile": _(
                        "Manager profiles cannot receive lucky wheel attempts."
                    )
                }
            )

        UserAccount.objects.select_for_update().get(pk=profile.user_id)

        source_id_str = str(source_id or "")
        idempotency_key = str(
            idempotency_key
            or f"lucky_wheel:{source_type}:{source_id_str}:profile:{profile.pk}"
        ).strip()
        if not idempotency_key:
            raise ValidationError(
                {"idempotency_key": _("Idempotency key is required.")}
            )

        existing = LuckyWheelGrant.all_objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return LuckyWheelGrantService._validate_existing_grant_match(
                existing,
                user_id=profile.user_id,
                profile_id=profile.pk,
                source_type=source_type,
                source_id=source_id_str,
            )

        try:
            with transaction.atomic():
                return LuckyWheelGrant.objects.create(
                    user=profile.user,
                    profile=profile,
                    source_type=source_type,
                    source_id=source_id_str,
                    total_attempts=attempts,
                    remaining_attempts=attempts,
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    metadata=metadata or {},
                )
        except DjangoValidationError as exc:
            existing = LuckyWheelGrant.all_objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing is not None:
                return LuckyWheelGrantService._validate_existing_grant_match(
                    existing,
                    user_id=profile.user_id,
                    profile_id=profile.pk,
                    source_type=source_type,
                    source_id=source_id_str,
                )
            raise _translate_django_validation_error(exc) from exc
        except IntegrityError:
            existing = LuckyWheelGrant.all_objects.get(
                idempotency_key=idempotency_key
            )
            return LuckyWheelGrantService._validate_existing_grant_match(
                existing,
                user_id=profile.user_id,
                profile_id=profile.pk,
                source_type=source_type,
                source_id=source_id_str,
            )

    @staticmethod
    def _validate_existing_grant_match(
        existing: LuckyWheelGrant,
        *,
        user_id: int,
        profile_id: int | None,
        source_type: str,
        source_id: str,
    ) -> LuckyWheelGrant:
        if (
            existing.user_id != user_id
            or existing.source_type != source_type
            or existing.source_id != source_id
            or (
                existing.profile_id is not None
                and profile_id is not None
                and existing.profile_id != profile_id
            )
        ):
            raise ValidationError(
                {
                    "idempotency_key": _(
                        "Idempotency key has already been used for a different grant."
                    )
                }
            )
        return existing

    @staticmethod
    def get_remaining_attempts(user, profile=None, *, now=None):
        return sum(
            grant.remaining_attempts
            for grant in LuckyWheelGrantService.active_grants(
                user, profile, now=now
            )
        )

    @staticmethod
    def active_grants(user, profile=None, *, now=None):
        now = now or timezone.now()
        queryset = (
            LuckyWheelGrant.objects.filter(
                user=user,
                is_active=True,
                remaining_attempts__gt=0,
                deleted__isnull=True,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .order_by(F("expires_at").asc(nulls_last=True), "created", "id")
        )
        if profile is not None:
            queryset = queryset.filter(
                Q(profile__isnull=True) | Q(profile_id=profile.pk)
            )
        return queryset


class LuckyWheelService:
    """Canonical availability and active-segment query service for Lucky Wheel (BE-LW-01 / BE-LW-02)."""

    @staticmethod
    def available_prizes():
        """Return active, non-deleted, positive-weight, non-exhausted prize segments ordered by id."""

        return (
            LuckyWheelPrize.objects.select_related(
                "discount_offer",
                "cashback_offer",
            )
            .filter(
                is_active=True,
                deleted__isnull=True,
                weight__gt=0,
            )
            .filter(
                Q(quantity_total__isnull=True)
                | Q(quantity_awarded__lt=F("quantity_total"))
            )
            .order_by("id")
        )

    @classmethod
    def get_availability(cls, *, user, profile=None, now=None) -> dict:
        """Compute canonical Lucky Wheel availability without mutating state.

        `available` (and `eligible`) is True iff:
        1. At least one active, non-exhausted `LuckyWheelPrize` segment exists, AND
        2. The user has `remaining_attempts > 0` across active, non-expired grants
           scoped to `(user, profile)`.
        """

        if profile is None and hasattr(user, "active_profile"):
            profile = user.active_profile
        if profile is not None and profile.user_id != user.pk:
            raise ValidationError(
                {"profile": _("Profile does not belong to the authenticated user.")}
            )

        now = now or timezone.now()
        grants = list(
            LuckyWheelGrantService.active_grants(user, profile, now=now)
        )
        remaining = sum(grant.remaining_attempts for grant in grants)
        segments = list(cls.available_prizes())
        has_active_wheel = len(segments) > 0
        available = bool(has_active_wheel and remaining > 0)
        return {
            "available": available,
            "eligible": available,
            "has_active_wheel": has_active_wheel,
            "remaining_attempts": remaining,
            "cooldown_until": None,
            "segments": segments,
            "active_grants": grants,
        }


class LuckyWheelSpinService:
    """Consume one grant attempt and apply the backend-selected prize atomically (BE-LW-01 / BE-LW-02)."""

    @staticmethod
    @transaction.atomic
    def spin(user, profile, idempotency_key, *, rng=None):
        """Return an existing spin for same-user retries, otherwise lock and spend one attempt."""

        from apps.authentication.models import UserAccount

        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise ValidationError(
                {"idempotency_key": _("This field is required.")}
            )
        if profile is not None and profile.user_id != user.pk:
            raise ValidationError(
                {"profile": _("Profile does not belong to the authenticated user.")}
            )

        # Serialize concurrent spin attempts for this user account.
        UserAccount.objects.select_for_update().get(pk=user.pk)

        existing = (
            LuckyWheelSpin.objects.select_related("grant", "prize", "profile")
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None:
            if existing.user_id != user.pk:
                raise ValidationError(
                    {
                        "idempotency_key": _(
                            "Idempotency key has already been used."
                        )
                    }
                )
            return existing, None

        now = timezone.now()
        grant = (
            LuckyWheelGrantService.active_grants(user, profile, now=now)
            .select_for_update()
            .first()
        )
        if (
            grant is None
            or not grant.is_active
            or grant.deleted is not None
            or grant.remaining_attempts <= 0
            or (grant.expires_at is not None and grant.expires_at <= now)
        ):
            raise ValidationError(
                {"detail": _("No lucky wheel attempts are available.")}
            )

        prize = LuckyWheelSpinService._choose_prize(rng=rng)
        status = (
            LuckyWheelSpinStatus.NO_PRIZE
            if prize.prize_type == LuckyWheelPrizeType.NO_PRIZE
            else LuckyWheelSpinStatus.AWARDED
        )

        grant.remaining_attempts -= 1
        if grant.remaining_attempts == 0:
            grant.is_active = False
        grant.save(update_fields=["remaining_attempts", "is_active", "modified"])

        spin = LuckyWheelSpin.objects.create(
            user=user,
            profile=profile,
            grant=grant,
            prize=prize,
            status=status,
            prize_snapshot=LuckyWheelSpinService._snapshot_prize(prize),
            idempotency_key=idempotency_key,
            awarded_at=now,
        )

        extra = LuckyWheelSpinService._apply_prize(spin, prize)
        if prize.prize_type != LuckyWheelPrizeType.NO_PRIZE:
            LuckyWheelSpinService._record_prize_award(spin, prize)
        return spin, extra

    @staticmethod
    def spin_prize_award_idempotency_key(spin_id: int) -> str:
        return f"prize:source:lucky_wheel:spin:{spin_id}"

    @staticmethod
    def _record_prize_award(spin: LuckyWheelSpin, prize: LuckyWheelPrize):
        from apps.prizes.models import PrizeAwardSourceType
        from apps.prizes.services import PrizeAwardService

        return PrizeAwardService.record_digital_fulfilled(
            user=spin.user,
            profile=spin.profile,
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id=str(spin.pk),
            title=prize.label,
            description=prize.description or "",
            prize_snapshot=dict(spin.prize_snapshot or {}),
            idempotency_key=(
                LuckyWheelSpinService.spin_prize_award_idempotency_key(spin.pk)
            ),
            awarded_at=spin.awarded_at,
            fulfilled_at=spin.awarded_at,
            metadata={
                "spin_id": spin.pk,
                "prize_id": prize.pk,
                "prize_type": prize.prize_type,
            },
        )

    @staticmethod
    def _choose_prize(*, rng=None):
        """Pick by configured weights using SystemRandom, lock row, and boundedly re-resolve stale inventory."""

        excluded_ids: set[int] = set()
        while True:
            available = list(
                LuckyWheelService.available_prizes().exclude(pk__in=excluded_ids)
            )
            if not available:
                if excluded_ids:
                    raise ValidationError(
                        {"detail": _("Selected prize is no longer available.")}
                    )
                raise ValidationError(
                    {"detail": _("No lucky wheel prizes are configured.")}
                )

            selected = LuckyWheelPrizeSelector.select(available, rng=rng)
            prize = (
                LuckyWheelPrize.all_objects.select_for_update(of=("self",))
                .select_related("discount_offer", "cashback_offer")
                .get(pk=selected.pk)
            )
            if (
                not prize.is_active
                or prize.deleted is not None
                or prize.weight is None
                or prize.weight <= 0
                or (
                    prize.quantity_total is not None
                    and prize.quantity_awarded >= prize.quantity_total
                )
            ):
                excluded_ids.add(prize.pk)
                continue

            LuckyWheelSpinService._validate_prize_target(prize)
            prize.quantity_awarded += 1
            try:
                prize.save(update_fields=["quantity_awarded", "modified"])
            except DjangoValidationError as exc:
                raise _translate_django_validation_error(exc) from exc
            return prize

    @staticmethod
    def _validate_prize_target(prize: LuckyWheelPrize):
        """Ensure the selected prize's reward payload and linked offer are valid at spin time."""

        if prize.prize_type == LuckyWheelPrizeType.NO_PRIZE:
            return

        if prize.prize_type == LuckyWheelPrizeType.PP_COINS:
            if (
                prize.points_amount is None
                or isinstance(prize.points_amount, bool)
                or prize.points_amount <= 0
            ):
                raise ValidationError(
                    {"points_amount": _("PP coin prizes require points amount.")}
                )
            return

        if prize.prize_type == LuckyWheelPrizeType.DISCOUNT_OFFER:
            if not prize.discount_offer_id:
                raise ValidationError(
                    {"discount_offer": _("Discount offer prize requires an offer.")}
                )
            offer = DiscountOffer.all_objects.filter(
                pk=prize.discount_offer_id
            ).first()
            if offer is None or offer.deleted is not None or not offer.is_active:
                raise ValidationError(
                    {"discount_offer": _("Discount offer is no longer active.")}
                )
            if offer.trigger_type != OfferTriggerType.LUCKY_WHEEL:
                raise ValidationError(
                    {"discount_offer": _("Offer trigger type must be lucky_wheel.")}
                )
            return

        if prize.prize_type == LuckyWheelPrizeType.CASHBACK_OFFER:
            if not prize.cashback_offer_id:
                raise ValidationError(
                    {"cashback_offer": _("Cashback offer prize requires an offer.")}
                )
            offer = CashbackOffer.all_objects.filter(
                pk=prize.cashback_offer_id
            ).first()
            if offer is None or offer.deleted is not None or not offer.is_active:
                raise ValidationError(
                    {"cashback_offer": _("Cashback offer is no longer active.")}
                )
            if offer.trigger_type != OfferTriggerType.LUCKY_WHEEL:
                raise ValidationError(
                    {"cashback_offer": _("Offer trigger type must be lucky_wheel.")}
                )
            return

        if prize.prize_type == LuckyWheelPrizeType.BADGE:
            if not prize.badge_type or prize.badge_type not in BadgeType.values:
                raise ValidationError(
                    {"badge_type": _("Badge type is not in BadgeType.")}
                )
            return

        raise ValidationError({"prize_type": _("Unsupported prize type.")})

    @staticmethod
    def _resolve_grant_expires_at(prize: LuckyWheelPrize, awarded_at):
        metadata = prize.metadata if isinstance(prize.metadata, dict) else {}
        validity_days = metadata.get("validity_days")
        if validity_days is None:
            return None
        if (
            isinstance(validity_days, bool)
            or not isinstance(validity_days, int)
            or validity_days <= 0
        ):
            raise ValidationError(
                {"metadata": _("Prize validity days must be a positive integer.")}
            )
        base_time = awarded_at or timezone.now()
        return base_time + timedelta(days=validity_days)

    @staticmethod
    def _apply_prize(spin, prize):
        """Validate the prize payload at spin time and route to the target ledger/grant service."""

        LuckyWheelSpinService._validate_prize_target(prize)

        if prize.prize_type == LuckyWheelPrizeType.NO_PRIZE:
            return None

        if prize.prize_type == LuckyWheelPrizeType.PP_COINS:
            return PointService.earn_points(
                user=spin.user,
                profile=spin.profile,
                points=prize.points_amount,
                reason=f"Lucky wheel prize: {prize.label}",
                source_type=RewardSourceType.LUCKY_WHEEL,
                source_id=str(spin.pk),
                idempotency_key=f"lucky_wheel:spin:{spin.pk}:points",
                metadata={"spin_id": spin.pk, "prize_id": prize.pk},
            )

        if prize.prize_type == LuckyWheelPrizeType.DISCOUNT_OFFER:
            offer = DiscountOffer.all_objects.get(pk=prize.discount_offer_id)
            expires_at = LuckyWheelSpinService._resolve_grant_expires_at(
                prize,
                spin.awarded_at,
            )
            return UserPromotionGrantService.grant_discount(
                user=spin.user,
                profile=spin.profile,
                discount_offer=offer,
                source_type=RewardSourceType.LUCKY_WHEEL,
                source_id=str(spin.pk),
                expires_at=expires_at,
                idempotency_key=f"lucky_wheel:spin:{spin.pk}:discount",
                metadata={"spin_id": spin.pk, "prize_id": prize.pk},
            )

        if prize.prize_type == LuckyWheelPrizeType.CASHBACK_OFFER:
            offer = CashbackOffer.all_objects.get(pk=prize.cashback_offer_id)
            expires_at = LuckyWheelSpinService._resolve_grant_expires_at(
                prize,
                spin.awarded_at,
            )
            return UserPromotionGrantService.grant_cashback(
                user=spin.user,
                profile=spin.profile,
                cashback_offer=offer,
                source_type=RewardSourceType.LUCKY_WHEEL,
                source_id=str(spin.pk),
                expires_at=expires_at,
                idempotency_key=f"lucky_wheel:spin:{spin.pk}:cashback",
                metadata={"spin_id": spin.pk, "prize_id": prize.pk},
            )

        if prize.prize_type == LuckyWheelPrizeType.BADGE:
            if spin.profile is None or getattr(spin.profile, "pk", None) is None:
                raise ValidationError(
                    {
                        "profile": _(
                            "A valid profile is required to award a badge prize."
                        )
                    }
                )
            return BadgeService.award_badge(
                profile=spin.profile,
                badge_type=prize.badge_type,
                source_type=RewardSourceType.LUCKY_WHEEL,
                source_id=str(spin.pk),
                idempotency_key=f"lucky_wheel:spin:{spin.pk}:badge",
                metadata={"spin_id": spin.pk, "prize_id": prize.pk},
            )

        raise ValidationError({"prize_type": _("Unsupported prize type.")})

    @staticmethod
    def _snapshot_prize(prize):
        discount_offer = (
            prize.discount_offer if prize.discount_offer_id else None
        )
        cashback_offer = (
            prize.cashback_offer if prize.cashback_offer_id else None
        )
        return {
            "id": prize.pk,
            "label": prize.label,
            "description": prize.description,
            "prize_type": prize.prize_type,
            "points_amount": prize.points_amount,
            "discount_offer_id": prize.discount_offer_id,
            "discount_offer": (
                {
                    "id": discount_offer.pk,
                    "title": discount_offer.title,
                    "value_type": discount_offer.value_type,
                    "value": str(discount_offer.value),
                }
                if discount_offer is not None
                else None
            ),
            "cashback_offer_id": prize.cashback_offer_id,
            "cashback_offer": (
                {
                    "id": cashback_offer.pk,
                    "title": cashback_offer.title,
                    "value_type": cashback_offer.value_type,
                    "value": str(cashback_offer.value),
                }
                if cashback_offer is not None
                else None
            ),
            "badge_type": prize.badge_type,
        }


def serialize_spin_extra(extra):
    if extra is None:
        return {}
    if hasattr(extra, "grant_type"):
        return {"user_promotion_grant": UserPromotionGrantSerializer(extra).data}
    return {}
