import random

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.loyalty.constants import RewardSourceType
from apps.loyalty.services import BadgeService, PointService
from apps.promotions.serializers import UserPromotionGrantSerializer
from apps.promotions.services import UserPromotionGrantService
from apps.users.models import Profile
from apps.lucky_wheel.models import (
    LuckyWheelGrant,
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
    LuckyWheelSpin,
    LuckyWheelSpinStatus,
)


class LuckyWheelGrantService:
    @staticmethod
    def grant_onboarding_attempt(profile):
        return LuckyWheelGrantService.grant_attempt(
            profile=profile,
            source_type=LuckyWheelGrantSourceType.ONBOARDING,
            source_id=str(profile.pk),
            attempts=1,
            idempotency_key=f"lucky_wheel:onboarding:{profile.pk}",
        )

    @staticmethod
    def grant_for_auction_completed(winner_profile, auction_item):
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
        profile = Profile.objects.select_related("user").get(pk=profile.pk)
        attempts = int(attempts)
        if attempts <= 0:
            raise ValidationError({"attempts": "Attempts must be greater than 0."})
        idempotency_key = idempotency_key or (
            f"lucky_wheel:{source_type}:{source_id}:profile:{profile.pk}"
        )
        existing = LuckyWheelGrant.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing
        try:
            return LuckyWheelGrant.objects.create(
                user=profile.user,
                profile=profile,
                source_type=source_type,
                source_id=str(source_id or ""),
                total_attempts=attempts,
                remaining_attempts=attempts,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
                metadata=metadata or {},
            )
        except IntegrityError:
            return LuckyWheelGrant.objects.get(idempotency_key=idempotency_key)

    @staticmethod
    def get_remaining_attempts(user, profile=None):
        return sum(
            grant.remaining_attempts
            for grant in LuckyWheelGrantService.active_grants(user, profile)
        )

    @staticmethod
    def active_grants(user, profile=None):
        now = timezone.now()
        queryset = (
            LuckyWheelGrant.objects.filter(
                user=user,
                is_active=True,
                remaining_attempts__gt=0,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .order_by("expires_at", "created", "id")
        )
        if profile is not None:
            queryset = queryset.filter(
                Q(profile__isnull=True) | Q(profile_id=profile.pk)
            )
        return queryset


class LuckyWheelSpinService:
    @staticmethod
    @transaction.atomic
    def spin(user, profile, idempotency_key):
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise ValidationError({"idempotency_key": "This field is required."})

        existing = (
            LuckyWheelSpin.objects.select_related("grant", "prize", "profile")
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None:
            return existing, None

        grant = (
            LuckyWheelGrantService.active_grants(user, profile)
            .select_for_update()
            .first()
        )
        if grant is None:
            raise ValidationError({"detail": "No lucky wheel attempts are available."})

        prize = LuckyWheelSpinService._choose_prize()
        status = (
            LuckyWheelSpinStatus.NO_PRIZE
            if prize.prize_type == LuckyWheelPrizeType.NO_PRIZE
            else LuckyWheelSpinStatus.AWARDED
        )
        spin = LuckyWheelSpin.objects.create(
            user=user,
            profile=profile,
            grant=grant,
            prize=prize,
            status=status,
            prize_snapshot=LuckyWheelSpinService._snapshot_prize(prize),
            idempotency_key=idempotency_key,
            awarded_at=timezone.now(),
        )

        grant.remaining_attempts -= 1
        if grant.remaining_attempts == 0:
            grant.is_active = False
        grant.save(update_fields=["remaining_attempts", "is_active", "modified"])

        extra = LuckyWheelSpinService._apply_prize(spin, prize)
        return spin, extra

    @staticmethod
    def _choose_prize():
        available = list(
            LuckyWheelPrize.objects.filter(is_active=True)
            .filter(
                Q(quantity_total__isnull=True)
                | Q(quantity_awarded__lt=F("quantity_total"))
            )
            .order_by("id")
        )
        if not available:
            raise ValidationError({"detail": "No lucky wheel prizes are configured."})

        total_weight = sum(prize.weight for prize in available)
        pick = random.randint(1, total_weight)
        running = 0
        selected = available[-1]
        for prize in available:
            running += prize.weight
            if pick <= running:
                selected = prize
                break

        prize = LuckyWheelPrize.objects.select_for_update().get(pk=selected.pk)
        if (
            prize.quantity_total is not None
            and prize.quantity_awarded >= prize.quantity_total
        ):
            raise ValidationError({"detail": "Selected prize is no longer available."})
        prize.quantity_awarded += 1
        prize.save(update_fields=["quantity_awarded", "modified"])
        return prize

    @staticmethod
    def _apply_prize(spin, prize):
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
            return UserPromotionGrantService.grant_discount(
                user=spin.user,
                profile=spin.profile,
                discount_offer=prize.discount_offer,
                source_type=RewardSourceType.LUCKY_WHEEL,
                source_id=str(spin.pk),
                idempotency_key=f"lucky_wheel:spin:{spin.pk}:discount",
                metadata={"spin_id": spin.pk, "prize_id": prize.pk},
            )
        if prize.prize_type == LuckyWheelPrizeType.CASHBACK_OFFER:
            return UserPromotionGrantService.grant_cashback(
                user=spin.user,
                profile=spin.profile,
                cashback_offer=prize.cashback_offer,
                source_type=RewardSourceType.LUCKY_WHEEL,
                source_id=str(spin.pk),
                idempotency_key=f"lucky_wheel:spin:{spin.pk}:cashback",
                metadata={"spin_id": spin.pk, "prize_id": prize.pk},
            )
        if prize.prize_type == LuckyWheelPrizeType.BADGE:
            return BadgeService.award_badge(
                profile=spin.profile,
                badge_type=prize.badge_type,
                source_type=RewardSourceType.LUCKY_WHEEL,
                source_id=str(spin.pk),
                idempotency_key=f"lucky_wheel:spin:{spin.pk}:badge",
                metadata={"spin_id": spin.pk, "prize_id": prize.pk},
            )
        raise ValidationError({"prize_type": "Unsupported prize type."})

    @staticmethod
    def _snapshot_prize(prize):
        return {
            "id": prize.pk,
            "label": prize.label,
            "description": prize.description,
            "prize_type": prize.prize_type,
            "points_amount": prize.points_amount,
            "discount_offer_id": prize.discount_offer_id,
            "cashback_offer_id": prize.cashback_offer_id,
            "badge_type": prize.badge_type,
        }


def serialize_spin_extra(extra):
    if extra is None:
        return {}
    if hasattr(extra, "grant_type"):
        return {"user_promotion_grant": UserPromotionGrantSerializer(extra).data}
    return {}
