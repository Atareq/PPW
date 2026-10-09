from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from apps.authentication.services import (
    build_token_for_user,
    switch_user_profile,
)
from apps.deals.models import (
    CompletedDealSourceType,
    CompletedDealType,
)
from apps.draws.models import (
    WeeklyDraw,
    WeeklyDrawEntry,
    WeeklyDrawEntryStatus,
    WeeklyDrawStatus,
)
from apps.draws.services import WeeklyDrawEntryService, WeeklyDrawService
from apps.loyalty.constants import BadgeType, RewardSourceType
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.loyalty.services import PointService
from apps.lucky_wheel.models import (
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
    LuckyWheelSpin,
    LuckyWheelSpinStatus,
)
from apps.lucky_wheel.services import (
    LuckyWheelGrantService,
    LuckyWheelSpinService,
)
from apps.notifications.models import Notification
from apps.payments.models import PaymentRequest
from apps.prizes.models import (
    PrizeAward,
    PrizeAwardFulfillmentType,
    PrizeAwardSourceType,
    PrizeAwardStatus,
)
from apps.prizes.services import PrizeAwardService
from apps.promotions.models import (
    CashbackOffer,
    DiscountOffer,
    OfferTargetType,
    OfferTriggerType,
    OfferValueType,
    UserPromotionGrant,
)
from apps.referrals.models import (
    ReferralChallenge,
    ReferralChallengePrizeCode,
    ReferralChallengeWinner,
)
from apps.referrals.services import ReferralChallengeService, ReferralService
from apps.seasons.models import (
    Season,
    SeasonPrize,
    SeasonSellerInsight,
    SeasonStatus,
)
from apps.seasons.services import (
    SeasonPayoutService,
    SeasonPrizeService,
    SeasonService,
)
from apps.users.constants import ProfileType
from tests.factories import (
    CompletedDealFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def auth_client(user, lang="en") -> APIClient:
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        HTTP_ACCEPT_LANGUAGE=lang,
    )
    return client


def make_manager(permissions=None):
    user = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager = user.active_profile
    if permissions is not None:
        manager.permissions = list(permissions)
        manager.save(update_fields=["permissions", "modified"])
    else:
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    return user, manager


def make_customer_with_attempt(attempts=1):
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer = user.active_profile
    grant = LuckyWheelGrantService.grant_attempt(
        profile=customer,
        source_type=LuckyWheelGrantSourceType.MANAGER_GRANT,
        source_id="prz01-seed",
        attempts=attempts,
        idempotency_key=f"lw:prz01:grant:{customer.pk}",
    )
    return user, customer, grant


def make_ended_season_with_deals():
    now = timezone.now()
    season = Season.objects.create(
        title="PRZ01 Season",
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(hours=1),
        status=SeasonStatus.DRAFT,
    )
    sellers = [SellerProfileFactory() for _ in range(4)]
    in_window = season.starts_at + timedelta(days=2)
    for idx, seller in enumerate(sellers):
        for offset in range(4 - idx):
            buyer = CustomerProfileFactory()
            CompletedDealFactory(
                deal_type=CompletedDealType.MARKET,
                source_type=CompletedDealSourceType.ORDER_ITEM,
                buyer_user=buyer.user,
                buyer_profile=buyer,
                seller_user=seller.user,
                seller_profile=seller,
                amount=Decimal("100.00"),
                currency="EGP",
                completed_at=in_window + timedelta(minutes=offset),
            )
    return season, sellers


def make_ended_referral_challenge_with_scorers():
    now = timezone.now()
    _, manager_profile = make_manager()
    challenge = ReferralChallengeService.create_challenge(
        name="PRZ01 Referral Challenge",
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(hours=1),
        created_by=manager_profile.user,
    )
    inviters = [
        UserAccountFactory(with_profile=ProfileType.CUSTOMER) for _ in range(3)
    ]
    in_window = challenge.starts_at + timedelta(days=1)
    for idx, inviter in enumerate(inviters):
        score = 3 - idx
        for n in range(score):
            referred = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
            code = ReferralService.create_code(inviter.active_profile)
            ref = ReferralService.redeem_code(
                referred.active_profile, code.code
            )
            type(ref).objects.filter(pk=ref.pk).update(
                redeemed_at=in_window + timedelta(minutes=n)
            )
            ReferralService.approve(ref.pk, manager_profile.user)
    return challenge, inviters, manager_profile.user


# ===========================================================================
# DOMAIN (1 - 10)
# ===========================================================================


def test_01_prize_award_owned_by_user_account_across_profile_switches():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer = user.active_profile
    seller = SellerProfileFactory(user=user, is_active=False, activated=True)

    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=customer,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="101",
        title="100 PP Coins",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:01",
    )

    assert award.user_id == user.pk
    assert award.profile_id == customer.pk
    assert list(PrizeAwardService.list_for_user(user)) == [award]

    switched, new_type = switch_user_profile(user, ProfileType.SELLER)
    assert switched is True
    assert new_type == ProfileType.SELLER
    user.refresh_from_db()
    assert user.active_profile.pk == seller.pk

    # Switching mode does not hide, duplicate, or alter ownership of the award.
    assert PrizeAward.objects.filter(user=user).count() == 1
    reloaded = PrizeAwardService.list_for_user(user).get()
    assert reloaded.pk == award.pk
    assert reloaded.user_id == user.pk
    assert reloaded.profile_id == customer.pk


def test_02_optional_profile_is_context_only_and_nullable():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    award = PrizeAwardService.record_claim_required(
        user=user,
        profile=None,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="501",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:02",
    )

    assert award.user_id == user.pk
    assert award.profile is None
    assert list(PrizeAwardService.list_for_user(user)) == [award]


def test_03_user_profile_mismatch_is_rejected():
    user_a = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_b = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    with pytest.raises(DRFValidationError) as exc_info:
        PrizeAwardService.record_digital_fulfilled(
            user=user_a,
            profile=user_b.active_profile,
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id="103",
            title="50 PP",
            prize_snapshot={"points_amount": 50},
            idempotency_key="prize:test:03",
        )
    assert "profile" in exc_info.value.detail
    assert PrizeAward.objects.count() == 0

    with pytest.raises(DjangoValidationError) as model_exc:
        PrizeAward(
            user=user_a,
            profile=user_b.active_profile,
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id="103",
            fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
            status=PrizeAwardStatus.FULFILLED,
            title="50 PP",
            prize_snapshot={"points_amount": 50},
            idempotency_key="prize:test:03:model",
            awarded_at=timezone.now(),
            fulfilled_at=timezone.now(),
        ).save()
    assert "profile" in model_exc.value.message_dict


def test_04_source_type_is_typed_and_rejects_unknown_or_weekly_draw():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert set(PrizeAwardSourceType.values) == {
        "SEASON",
        "REFERRAL_CHALLENGE",
        "LUCKY_WHEEL",
    }
    assert "WEEKLY_DRAW" not in PrizeAwardSourceType.values

    for invalid_source in ("WEEKLY_DRAW", "UNKNOWN_SOURCE", ""):
        with pytest.raises(DRFValidationError) as exc_info:
            PrizeAwardService.record_digital_fulfilled(
                user=user,
                source_type=invalid_source,
                source_id="104",
                title="Test",
                prize_snapshot={"k": "v"},
                idempotency_key=f"prize:test:04:{invalid_source}",
            )
        assert "source_type" in exc_info.value.detail


def test_05_fulfillment_type_is_typed():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert set(PrizeAwardFulfillmentType.values) == {
        "DIGITAL_AUTOMATIC",
        "CLAIM_REQUIRED",
    }

    with pytest.raises(DjangoValidationError) as exc_info:
        PrizeAward(
            user=user,
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id="105",
            fulfillment_type="INVALID_FULFILLMENT",
            status=PrizeAwardStatus.FULFILLED,
            title="Test",
            prize_snapshot={"k": "v"},
            idempotency_key="prize:test:05",
            awarded_at=timezone.now(),
            fulfilled_at=timezone.now(),
        ).save()
    assert "fulfillment_type" in exc_info.value.message_dict


def test_06_status_is_typed_and_consistent_with_fulfillment_type():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    assert set(PrizeAwardStatus.values) == {"PENDING", "FULFILLED"}

    # DIGITAL_AUTOMATIC cannot be PENDING
    with pytest.raises(DjangoValidationError) as exc_info:
        PrizeAward(
            user=user,
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id="106",
            fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
            status=PrizeAwardStatus.PENDING,
            title="Test",
            prize_snapshot={"k": "v"},
            idempotency_key="prize:test:06:a",
            awarded_at=timezone.now(),
            fulfilled_at=None,
        ).save()
    assert "status" in exc_info.value.message_dict

    # PENDING cannot have fulfilled_at set
    with pytest.raises(DjangoValidationError) as exc_info_2:
        PrizeAward(
            user=user,
            source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
            source_id="106",
            fulfillment_type=PrizeAwardFulfillmentType.CLAIM_REQUIRED,
            status=PrizeAwardStatus.PENDING,
            title="Test",
            prize_snapshot={"k": "v"},
            idempotency_key="prize:test:06:b",
            awarded_at=timezone.now(),
            fulfilled_at=timezone.now(),
        ).save()
    assert "fulfilled_at" in exc_info_2.value.message_dict


def test_07_idempotency_key_unique_at_model_and_db_level():
    user_1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    PrizeAwardService.record_digital_fulfilled(
        user=user_1,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="107",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:07:uniq",
    )

    with pytest.raises((DjangoValidationError, IntegrityError)):
        with transaction.atomic():
            PrizeAward.objects.create(
                user=user_2,
                source_type=PrizeAwardSourceType.LUCKY_WHEEL,
                source_id="108",
                fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
                status=PrizeAwardStatus.FULFILLED,
                title="200 PP",
                prize_snapshot={"points_amount": 200},
                idempotency_key="prize:test:07:uniq",
                awarded_at=timezone.now(),
                fulfilled_at=timezone.now(),
            )


def test_08_immutable_source_identity_after_creation():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="108",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:08",
    )

    award.source_type = PrizeAwardSourceType.SEASON
    with pytest.raises(DjangoValidationError) as exc_1:
        award.save()
    assert "source_type" in exc_1.value.message_dict

    award.refresh_from_db()
    award.source_id = "999"
    with pytest.raises(DjangoValidationError) as exc_2:
        award.save()
    assert "source_id" in exc_2.value.message_dict


def test_09_immutable_recipient_and_profile_after_creation():
    user_1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    seller_1 = SellerProfileFactory(user=user_1, is_active=False, activated=True)

    award = PrizeAwardService.record_digital_fulfilled(
        user=user_1,
        profile=user_1.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="109",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:09",
    )

    award.user = user_2
    award.profile = user_2.active_profile
    with pytest.raises(DjangoValidationError) as exc_1:
        award.save()
    assert "user" in exc_1.value.message_dict

    award.refresh_from_db()
    award.profile = seller_1
    with pytest.raises(DjangoValidationError) as exc_2:
        award.save()
    assert "profile" in exc_2.value.message_dict


def test_10_immutable_prize_snapshot_and_delete_protection_after_creation():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    snapshot = {"label": "Gold Spin", "points_amount": 250}
    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="110",
        title="Gold Spin",
        prize_snapshot=snapshot,
        idempotency_key="prize:test:10",
    )

    # Mutating caller dict does not mutate stored snapshot
    snapshot["points_amount"] = 9999
    award.refresh_from_db()
    assert award.prize_snapshot == {"label": "Gold Spin", "points_amount": 250}

    award.prize_snapshot = {"label": "Tampered", "points_amount": 9999}
    with pytest.raises(DjangoValidationError) as exc_info:
        award.save()
    assert "prize_snapshot" in exc_info.value.message_dict

    award.refresh_from_db()
    with pytest.raises(ProtectedError):
        award.delete()


# ===========================================================================
# DIGITAL RECORD (11 - 18)
# ===========================================================================


def test_11_record_digital_fulfilled_creates_fulfilled_award():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="211",
        title="500 PP",
        description="Lucky wheel points win",
        prize_snapshot={"prize_type": "pp_coins", "points_amount": 500},
        idempotency_key="prize:test:11",
    )

    assert (
        award.fulfillment_type
        == PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC
    )
    assert award.status == PrizeAwardStatus.FULFILLED


def test_12_record_digital_fulfilled_sets_fulfilled_at():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="212",
        title="500 PP",
        prize_snapshot={"points_amount": 500},
        idempotency_key="prize:test:12",
    )

    assert award.awarded_at is not None
    assert award.fulfilled_at is not None
    assert not timezone.is_naive(award.fulfilled_at)


def test_13_same_idempotency_key_returns_same_award():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    first = PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="213",
        title="500 PP",
        prize_snapshot={"points_amount": 500},
        idempotency_key="prize:test:13",
    )
    second = PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="213",
        title="500 PP",
        prize_snapshot={"points_amount": 500},
        idempotency_key="prize:test:13",
    )

    assert first.pk == second.pk
    assert PrizeAward.objects.count() == 1


def test_14_cross_user_or_cross_source_idempotency_collision_rejected():
    user_1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    PrizeAwardService.record_digital_fulfilled(
        user=user_1,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="214",
        title="500 PP",
        prize_snapshot={"points_amount": 500},
        idempotency_key="prize:test:14",
    )

    # Cross-user collision
    with pytest.raises(DRFValidationError) as exc_user:
        PrizeAwardService.record_digital_fulfilled(
            user=user_2,
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id="214",
            title="500 PP",
            prize_snapshot={"points_amount": 500},
            idempotency_key="prize:test:14",
        )
    assert "idempotency_key" in exc_user.value.detail

    # Cross-source collision for same user
    with pytest.raises(DRFValidationError) as exc_source:
        PrizeAwardService.record_digital_fulfilled(
            user=user_1,
            source_type=PrizeAwardSourceType.SEASON,
            source_id="214",
            title="500 PP",
            prize_snapshot={"points_amount": 500},
            idempotency_key="prize:test:14",
        )
    assert "idempotency_key" in exc_source.value.detail
    assert PrizeAward.objects.count() == 1


def test_15_digital_recording_creates_no_reward_itself():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="215",
        title="1000 PP",
        prize_snapshot={"prize_type": "pp_coins", "points_amount": 1000},
        idempotency_key="prize:test:15",
    )

    assert PointService.get_balance(user) == 0


def test_16_digital_recording_creates_zero_point_transactions_by_itself():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    before = PointTransaction.objects.count()

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.SEASON,
        source_id="216",
        title="Season Rank 1",
        prize_snapshot={"pp_points": 100000},
        idempotency_key="prize:test:16",
    )

    assert PointTransaction.objects.count() == before == 0


def test_17_digital_recording_creates_zero_user_promotion_grants_by_itself():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    before = UserPromotionGrant.objects.count()

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="217",
        title="20% Discount",
        prize_snapshot={"prize_type": "discount_offer", "value": "20.00"},
        idempotency_key="prize:test:17",
    )

    assert UserPromotionGrant.objects.count() == before == 0


def test_18_digital_recording_creates_zero_badge_awards_by_itself():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    before = BadgeAward.objects.count()

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="218",
        title="Lucky Badge",
        prize_snapshot={"prize_type": "badge", "badge_type": "WELCOME_BUYER"},
        idempotency_key="prize:test:18",
    )

    assert BadgeAward.objects.count() == before == 0


# ===========================================================================
# CLAIM REQUIRED (19 - 25)
# ===========================================================================


def test_19_record_claim_required_creates_pending_award():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="319",
        title="iPhone",
        description="Monthly referral challenge Rank #1",
        prize_snapshot={"prize_code": "IPHONE", "rank": 1},
        idempotency_key="prize:test:19",
    )

    assert award.fulfillment_type == PrizeAwardFulfillmentType.CLAIM_REQUIRED
    assert award.status == PrizeAwardStatus.PENDING


def test_20_record_claim_required_leaves_fulfilled_at_null():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="320",
        title="AirPods",
        prize_snapshot={"prize_code": "AIRPODS", "rank": 2},
        idempotency_key="prize:test:20",
    )

    assert award.fulfilled_at is None
    assert award.awarded_at is not None


def test_21_no_pii_stored_and_pii_keys_rejected_in_snapshot_and_metadata():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="321",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE", "rank": 1},
        idempotency_key="prize:test:21",
    )

    for forbidden_key in (
        "address",
        "shipping_address",
        "national_id",
        "identity_number",
        "claimant_name",
    ):
        assert forbidden_key not in award.prize_snapshot
        assert forbidden_key not in award.metadata
        with pytest.raises(DRFValidationError) as exc_snap:
            PrizeAwardService.record_claim_required(
                user=user,
                source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
                source_id="321-bad",
                title="iPhone",
                prize_snapshot={"prize_code": "IPHONE", forbidden_key: "secret"},
                idempotency_key=f"prize:test:21:snap:{forbidden_key}",
            )
        assert "prize_snapshot" in exc_snap.value.detail

        with pytest.raises(DRFValidationError) as exc_meta:
            PrizeAwardService.record_claim_required(
                user=user,
                source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
                source_id="321-bad-meta",
                title="iPhone",
                prize_snapshot={"prize_code": "IPHONE"},
                metadata={forbidden_key: "secret"},
                idempotency_key=f"prize:test:21:meta:{forbidden_key}",
            )
        assert "metadata" in exc_meta.value.detail


def test_22_claim_required_creates_no_point_transaction():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="322",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:22",
    )
    assert PointTransaction.objects.count() == 0
    assert PointService.get_balance(user) == 0


def test_23_claim_required_creates_no_promotion_grant():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="323",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:23",
    )
    assert UserPromotionGrant.objects.count() == 0


def test_24_claim_required_creates_no_badge():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="324",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:24",
    )
    assert BadgeAward.objects.count() == 0


def test_25_claim_required_creates_no_payment_request():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="325",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:25",
    )
    assert PaymentRequest.objects.count() == 0


# ===========================================================================
# LUCKY WHEEL INTEGRATION (26 - 35)
# ===========================================================================


def test_26_successful_pp_wheel_spin_creates_exactly_one_fulfilled_prize_award():
    user, customer, _ = make_customer_with_attempt()
    prize = LuckyWheelPrize.objects.create(
        label="250 PP Spin",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=250,
        weight=10,
        is_active=True,
    )

    spin, _ = LuckyWheelSpinService.spin(
        user, customer, "lw-spin-26"
    )

    awards = list(
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id=str(spin.pk),
        )
    )
    assert len(awards) == 1
    award = awards[0]
    assert award.user_id == user.pk
    assert award.profile_id == customer.pk
    assert (
        award.fulfillment_type
        == PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC
    )
    assert award.status == PrizeAwardStatus.FULFILLED
    assert award.fulfilled_at == spin.awarded_at
    assert award.title == prize.label
    assert award.prize_snapshot["points_amount"] == 250
    assert (
        award.idempotency_key
        == f"prize:source:lucky_wheel:spin:{spin.pk}"
    )


def test_27_pp_still_awarded_exactly_once_on_wheel_spin():
    user, customer, _ = make_customer_with_attempt()
    LuckyWheelPrize.objects.create(
        label="300 PP Spin",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=300,
        weight=10,
        is_active=True,
    )

    LuckyWheelSpinService.spin(user, customer, "lw-spin-27")

    assert PointTransaction.objects.filter(user=user).count() == 1
    assert PointService.get_balance(user) == 300
    assert PrizeAward.objects.filter(user=user).count() == 1


def test_28_retry_same_spin_creates_one_prize_award():
    user, customer, _ = make_customer_with_attempt(attempts=2)
    LuckyWheelPrize.objects.create(
        label="150 PP Spin",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=150,
        weight=10,
        is_active=True,
    )

    spin_1, _ = LuckyWheelSpinService.spin(user, customer, "lw-spin-28")
    spin_2, _ = LuckyWheelSpinService.spin(user, customer, "lw-spin-28")
    # Also directly invoke _record_prize_award to verify ledger idempotency
    LuckyWheelSpinService._record_prize_award(spin_1, spin_1.prize)

    assert spin_1.pk == spin_2.pk
    assert PrizeAward.objects.filter(user=user).count() == 1
    assert PointTransaction.objects.filter(user=user).count() == 1
    assert PointService.get_balance(user) == 150


def test_29_discount_spin_creates_one_fulfilled_prize_award_without_duplicate_grant():
    user, customer, _ = make_customer_with_attempt()
    offer = DiscountOffer.objects.create(
        title="Wheel 15% Discount",
        target_type=OfferTargetType.ALL,
        value_type=OfferValueType.PERCENTAGE,
        value=Decimal("15.00"),
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="15% Discount Segment",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=10,
        is_active=True,
    )

    spin, _ = LuckyWheelSpinService.spin(user, customer, "lw-spin-29")

    assert UserPromotionGrant.objects.filter(user=user).count() == 1
    award = PrizeAward.objects.get(
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id=str(spin.pk),
    )
    assert (
        award.fulfillment_type
        == PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC
    )
    assert award.status == PrizeAwardStatus.FULFILLED
    assert award.prize_snapshot["discount_offer_id"] == offer.pk


def test_30_cashback_spin_creates_one_fulfilled_prize_award_without_duplicate_grant():
    user, customer, _ = make_customer_with_attempt()
    offer = CashbackOffer.objects.create(
        title="Wheel 10% Cashback",
        target_type=OfferTargetType.ALL,
        value_type=OfferValueType.PERCENTAGE,
        value=Decimal("10.00"),
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="10% Cashback Segment",
        prize_type=LuckyWheelPrizeType.CASHBACK_OFFER,
        cashback_offer=offer,
        weight=10,
        is_active=True,
    )

    spin, _ = LuckyWheelSpinService.spin(user, customer, "lw-spin-30")

    assert UserPromotionGrant.objects.filter(user=user).count() == 1
    award = PrizeAward.objects.get(
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id=str(spin.pk),
    )
    assert (
        award.fulfillment_type
        == PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC
    )
    assert award.status == PrizeAwardStatus.FULFILLED
    assert award.prize_snapshot["cashback_offer_id"] == offer.pk


def test_31_badge_spin_creates_one_fulfilled_prize_award_without_duplicate_badge():
    user, customer, _ = make_customer_with_attempt()
    LuckyWheelPrize.objects.create(
        label="Wheel Badge Segment",
        prize_type=LuckyWheelPrizeType.BADGE,
        badge_type=BadgeType.LOYAL_FOLLOWER,
        weight=10,
        is_active=True,
    )

    spin, _ = LuckyWheelSpinService.spin(user, customer, "lw-spin-31")

    assert (
        BadgeAward.objects.filter(
            profile=customer,
            badge_type=BadgeType.LOYAL_FOLLOWER,
        ).count()
        == 1
    )
    award = PrizeAward.objects.get(
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id=str(spin.pk),
    )
    assert (
        award.fulfillment_type
        == PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC
    )
    assert award.status == PrizeAwardStatus.FULFILLED
    assert award.prize_snapshot["badge_type"] == BadgeType.LOYAL_FOLLOWER


def test_32_no_prize_spin_creates_zero_prize_awards():
    user, customer, _ = make_customer_with_attempt()
    LuckyWheelPrize.objects.create(
        label="Better Luck Next Time",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=10,
        is_active=True,
    )

    spin, _ = LuckyWheelSpinService.spin(user, customer, "lw-spin-32")

    assert spin.status == LuckyWheelSpinStatus.NO_PRIZE
    assert PrizeAward.objects.count() == 0


def test_33_failed_spin_creates_zero_prize_awards(monkeypatch):
    user, customer, grant = make_customer_with_attempt()
    prize = LuckyWheelPrize.objects.create(
        label="200 PP Spin",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=200,
        weight=10,
        quantity_total=5,
        is_active=True,
    )

    def boom(*args, **kwargs):
        raise RuntimeError("PointService failure")

    monkeypatch.setattr(PointService, "earn_points", boom)

    with pytest.raises(RuntimeError, match="PointService failure"):
        LuckyWheelSpinService.spin(user, customer, "lw-spin-33")

    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert PrizeAward.objects.count() == 0


def test_34_prize_award_failure_rolls_back_integrated_digital_reward(monkeypatch):
    user, customer, grant = make_customer_with_attempt()
    prize = LuckyWheelPrize.objects.create(
        label="400 PP Spin",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=400,
        weight=10,
        quantity_total=3,
        is_active=True,
    )

    def fail_award(*args, **kwargs):
        raise RuntimeError("PrizeAward ledger write failure")

    monkeypatch.setattr(
        PrizeAwardService, "record_digital_fulfilled", fail_award
    )

    with pytest.raises(RuntimeError, match="PrizeAward ledger write failure"):
        LuckyWheelSpinService.spin(user, customer, "lw-spin-34")

    grant.refresh_from_db()
    prize.refresh_from_db()
    assert grant.remaining_attempts == 1
    assert grant.is_active is True
    assert prize.quantity_awarded == 0
    assert LuckyWheelSpin.objects.count() == 0
    assert PointTransaction.objects.filter(user=user).count() == 0
    assert PointService.get_balance(user) == 0
    assert PrizeAward.objects.count() == 0


def test_35_lucky_wheel_probability_and_inventory_behavior_unchanged():
    user, customer, _ = make_customer_with_attempt(attempts=3)
    p1 = LuckyWheelPrize.objects.create(
        label="Finite 100 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=100,
        weight=10,
        quantity_total=1,
        is_active=True,
    )
    p2 = LuckyWheelPrize.objects.create(
        label="Unlimited 50 PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=50,
        weight=10,
        quantity_total=None,
        is_active=True,
    )

    spin_1, _ = LuckyWheelSpinService.spin(
        user, customer, "lw-spin-35-1", rng=lambda total: 1
    )
    spin_2, _ = LuckyWheelSpinService.spin(
        user, customer, "lw-spin-35-2", rng=lambda total: 1
    )

    p1.refresh_from_db()
    p2.refresh_from_db()
    assert spin_1.prize_id == p1.pk
    assert spin_2.prize_id == p2.pk
    assert p1.quantity_awarded == 1
    assert p2.quantity_awarded == 1
    assert PrizeAward.objects.filter(user=user).count() == 2
    assert PointService.get_balance(user) == 150


# ===========================================================================
# SEASON INTEGRATION (36 - 39)
# ===========================================================================


def test_36_season_payout_creates_one_fulfilled_prize_award_per_top_winner():
    season, sellers = make_ended_season_with_deals()
    _, manager_profile = make_manager()

    SeasonService.finalize(season, manager_profile=manager_profile)

    awards = list(
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.SEASON
        ).order_by("id")
    )
    assert len(awards) == 3
    insights = list(SeasonService.insights(season))
    for idx, award in enumerate(awards):
        winner_seller = sellers[idx]
        winner_insight = insights[idx]
        assert award.user_id == winner_seller.user_id
        assert award.profile_id == winner_seller.pk
        assert award.source_id == str(winner_insight.pk)
        assert (
            award.fulfillment_type
            == PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC
        )
        assert award.status == PrizeAwardStatus.FULFILLED
        assert award.fulfilled_at is not None
        assert award.prize_snapshot["rank"] == idx + 1
        assert award.prize_snapshot["pp_points"] in (100000, 50000, 25000)

    # Rank 4 gets no PrizeAward
    assert not PrizeAward.objects.filter(user=sellers[3].user).exists()


def test_37_pp_season_payout_remains_exactly_once():
    season, sellers = make_ended_season_with_deals()
    SeasonService.finalize(season)

    assert PointService.get_balance(sellers[0].user) == 100000
    assert PointService.get_balance(sellers[1].user) == 50000
    assert PointService.get_balance(sellers[2].user) == 25000
    assert PointService.get_balance(sellers[3].user) == 0
    assert (
        PointTransaction.objects.filter(
            source_type=RewardSourceType.SEASON_WINNER,
            source_id=str(season.pk),
        ).count()
        == 3
    )


def test_38_season_finalization_retry_does_not_duplicate_prize_award():
    season, sellers = make_ended_season_with_deals()

    SeasonService.finalize(season)
    SeasonService.finalize(season)
    SeasonPayoutService.payout_season_winners(season)

    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.SEASON
        ).count()
        == 3
    )
    assert (
        PointTransaction.objects.filter(
            source_type=RewardSourceType.SEASON_WINNER,
            source_id=str(season.pk),
        ).count()
        == 3
    )
    assert PointService.get_balance(sellers[0].user) == 100000


def test_39_failed_season_payout_leaves_no_prize_award(monkeypatch):
    season, sellers = make_ended_season_with_deals()
    real_earn = PointService.earn_points
    calls = {"n": 0}

    def fail_on_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("Season payout mid-loop failure")
        return real_earn(*args, **kwargs)

    monkeypatch.setattr(PointService, "earn_points", fail_on_second)

    with pytest.raises(RuntimeError, match="Season payout mid-loop failure"):
        SeasonService.finalize(season)

    season.refresh_from_db()
    assert season.status != SeasonStatus.FINALIZED
    assert (
        PointTransaction.objects.filter(
            source_type=RewardSourceType.SEASON_WINNER
        ).count()
        == 0
    )
    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.SEASON
        ).count()
        == 0
    )


# ===========================================================================
# REFERRAL CHALLENGE INTEGRATION (40 - 42)
# ===========================================================================


def test_40_canonical_non_digital_referral_winners_create_pending_claim_required_awards():
    challenge, inviters, reviewer = make_ended_referral_challenge_with_scorers()

    ReferralChallengeService.finalize(challenge.pk, reviewer)

    winners = list(
        ReferralChallengeWinner.objects.filter(challenge=challenge).order_by(
            "rank"
        )
    )
    assert len(winners) == 3

    # Rank 1 (IPHONE) and Rank 2 (AIRPODS) are canonical non-digital in-kind prizes -> CLAIM_REQUIRED / PENDING
    awards = list(
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE
        ).order_by("id")
    )
    assert len(awards) == 2
    assert [a.user_id for a in awards] == [inviters[0].pk, inviters[1].pk]
    assert [a.source_id for a in awards] == [
        str(winners[0].pk),
        str(winners[1].pk),
    ]
    for award, expected_code in zip(
        awards,
        (
            ReferralChallengePrizeCode.IPHONE,
            ReferralChallengePrizeCode.AIRPODS,
        ),
    ):
        assert (
            award.fulfillment_type
            == PrizeAwardFulfillmentType.CLAIM_REQUIRED
        )
        assert award.status == PrizeAwardStatus.PENDING
        assert award.fulfilled_at is None
        assert award.prize_snapshot["prize_code"] == expected_code

    # Rank 3 (ANNUAL_LOFT_SUBSCRIPTION) is not falsely marked FULFILLED or physical CLAIM_REQUIRED
    assert not PrizeAward.objects.filter(
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id=str(winners[2].pk),
    ).exists()


def test_41_challenge_finalization_retry_does_not_duplicate_award():
    challenge, _, reviewer = make_ended_referral_challenge_with_scorers()

    ReferralChallengeService.finalize(challenge.pk, reviewer)
    ReferralChallengeService.finalize(challenge.pk, reviewer)
    ReferralChallengeService._record_winner_prize_awards(challenge)

    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE
        ).count()
        == 2
    )


def test_42_referral_challenge_award_contains_no_claim_pii():
    challenge, inviters, reviewer = make_ended_referral_challenge_with_scorers()
    inviters[0].active_profile.national_id = "29901011234567"
    inviters[0].active_profile.address = "123 Private Cairo St"
    inviters[0].active_profile.save(
        update_fields=["national_id", "address", "modified"]
    )

    ReferralChallengeService.finalize(challenge.pk, reviewer)

    award = PrizeAward.objects.get(
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        user=inviters[0],
    )
    serialized_text = f"{award.prize_snapshot} {award.metadata}"
    assert "29901011234567" not in serialized_text
    assert "123 Private Cairo St" not in serialized_text
    assert "national_id" not in award.prize_snapshot
    assert "address" not in award.prize_snapshot


# ===========================================================================
# API & PERMISSIONS (43 - 51)
# ===========================================================================


def test_43_user_lists_own_awards_across_customer_and_seller_modes():
    user_1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    SellerProfileFactory(user=user_1, is_active=False, activated=True)
    user_2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    award_1 = PrizeAwardService.record_digital_fulfilled(
        user=user_1,
        profile=user_1.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="443-1",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:43:1",
    )
    award_2 = PrizeAwardService.record_claim_required(
        user=user_1,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="443-2",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:43:2",
    )
    PrizeAwardService.record_digital_fulfilled(
        user=user_2,
        profile=user_2.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="443-3",
        title="Other User Prize",
        prize_snapshot={"points_amount": 50},
        idempotency_key="prize:test:43:3",
    )

    res_customer = auth_client(user_1).get("/api/prizes/my-awards/")
    assert res_customer.status_code == status.HTTP_200_OK
    ids_customer = [row["id"] for row in res_customer.data["results"]]
    assert set(ids_customer) == {award_1.pk, award_2.pk}

    # Switch to seller mode and verify identical award visibility
    assert switch_user_profile(user_1, ProfileType.SELLER)[0] is True
    res_seller = auth_client(user_1).get("/api/prizes/my-awards/")
    assert res_seller.status_code == status.HTTP_200_OK
    ids_seller = [row["id"] for row in res_seller.data["results"]]
    assert ids_seller == ids_customer


def test_44_user_cannot_read_another_users_award():
    user_1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other_award = PrizeAwardService.record_digital_fulfilled(
        user=user_2,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="444",
        title="Secret Prize",
        prize_snapshot={"points_amount": 500},
        idempotency_key="prize:test:44",
    )

    res = auth_client(user_1).get(f"/api/prizes/my-awards/{other_award.pk}/")
    assert res.status_code == status.HTTP_404_NOT_FOUND


def test_45_user_cannot_patch_award():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="445",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:45",
    )

    res = auth_client(user).patch(
        f"/api/prizes/my-awards/{award.pk}/",
        {"status": "FULFILLED", "title": "Hacked"},
        format="json",
    )
    assert res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    award.refresh_from_db()
    assert award.status == PrizeAwardStatus.PENDING
    assert award.title == "iPhone"


def test_46_user_cannot_delete_award():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="446",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:46",
    )

    res = auth_client(user).delete(f"/api/prizes/my-awards/{award.pk}/")
    assert res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert PrizeAward.objects.filter(pk=award.pk).exists()


def test_47_manager_with_permission_lists_and_retrieves_awards_with_filters():
    user_1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_2 = UserAccountFactory(with_profile=ProfileType.SELLER)
    a1 = PrizeAwardService.record_digital_fulfilled(
        user=user_1,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="447-1",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:47:1",
    )
    a2 = PrizeAwardService.record_claim_required(
        user=user_2,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="447-2",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:47:2",
    )

    mgr_user, _ = make_manager(["prizes.awards.list", "prizes.awards.retrieve"])
    client = auth_client(mgr_user)

    list_res = client.get("/api/prizes/admin/awards/")
    assert list_res.status_code == status.HTTP_200_OK
    assert len(list_res.data["results"]) == 2

    pending_res = client.get(
        "/api/prizes/admin/awards/?status=PENDING&fulfillment_type=CLAIM_REQUIRED&source_type=REFERRAL_CHALLENGE"
    )
    assert pending_res.status_code == status.HTTP_200_OK
    assert [r["id"] for r in pending_res.data["results"]] == [a2.pk]

    user_filter_res = client.get(
        f"/api/prizes/admin/awards/?user={user_1.pk}"
    )
    assert [r["id"] for r in user_filter_res.data["results"]] == [a1.pk]

    detail_res = client.get(f"/api/prizes/admin/awards/{a2.pk}/")
    assert detail_res.status_code == status.HTTP_200_OK
    assert detail_res.data["id"] == a2.pk
    assert detail_res.data["user_id"] == user_2.pk
    assert detail_res.data["idempotency_key"] == "prize:test:47:2"


def test_48_manager_without_permission_denied():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="448",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:48",
    )
    mgr_no_perm, _ = make_manager(["seasons.list"])
    client = auth_client(mgr_no_perm)

    assert client.get("/api/prizes/admin/awards/").status_code == 403
    assert (
        client.get(f"/api/prizes/admin/awards/{award.pk}/").status_code == 403
    )

    # List permission alone does not grant retrieve
    mgr_list_only, _ = make_manager(["prizes.awards.list"])
    assert (
        auth_client(mgr_list_only).get("/api/prizes/admin/awards/").status_code
        == 200
    )
    assert (
        auth_client(mgr_list_only)
        .get(f"/api/prizes/admin/awards/{award.pk}/")
        .status_code
        == 403
    )


def test_49_customer_cannot_use_manager_endpoint():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=customer,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="449",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:49",
    )
    client = auth_client(customer)
    assert client.get("/api/prizes/admin/awards/").status_code == 403
    assert (
        client.get(f"/api/prizes/admin/awards/{award.pk}/").status_code == 403
    )


def test_50_seller_cannot_use_manager_endpoint():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=seller,
        source_type=PrizeAwardSourceType.SEASON,
        source_id="450",
        title="Season Rank 1",
        prize_snapshot={"pp_points": 100000},
        idempotency_key="prize:test:50",
    )
    client = auth_client(seller)
    assert client.get("/api/prizes/admin/awards/").status_code == 403
    assert (
        client.get(f"/api/prizes/admin/awards/{award.pk}/").status_code == 403
    )


def test_51_public_response_hides_idempotency_key_and_internal_metadata():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    award = PrizeAwardService.record_digital_fulfilled(
        user=user,
        profile=user.active_profile,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="451",
        title="100 PP",
        description="Spin reward",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:51:secret-key",
        metadata={"internal_note": "audit_only"},
    )

    client = auth_client(user)
    list_res = client.get("/api/prizes/my-awards/")
    detail_res = client.get(f"/api/prizes/my-awards/{award.pk}/")

    assert list_res.status_code == 200
    assert detail_res.status_code == 200
    expected_fields = {
        "id",
        "source_type",
        "source_id",
        "fulfillment_type",
        "status",
        "title",
        "description",
        "prize_snapshot",
        "awarded_at",
        "fulfilled_at",
        "claim_status",
        "claim_submitted_at",
        "claim_reviewed_at",
        "claim_delivered_at",
        "created",
    }
    assert set(list_res.data["results"][0].keys()) == expected_fields
    assert set(detail_res.data.keys()) == expected_fields
    assert detail_res.data["claim_status"] is None
    assert "idempotency_key" not in detail_res.data
    assert "metadata" not in detail_res.data
    assert "identity_number" not in detail_res.data
    assert "address" not in detail_res.data
    assert "full_name" not in detail_res.data
    assert "internal_note" not in str(detail_res.data)



# ===========================================================================
# HISTORICAL NO-BACKFILL (52 - 54)
# ===========================================================================


def test_52_migration_does_not_backfill_historical_wheel_spins():
    from importlib import import_module

    migration_0001 = import_module("apps.prizes.migrations.0001_initial")
    op_names = [
        type(op).__name__ for op in migration_0001.Migration.operations
    ]
    assert "RunPython" not in op_names
    assert "RunSQL" not in op_names

    # A pre-existing LuckyWheelSpin row in the DB does not get backfilled automatically
    user, customer, grant = make_customer_with_attempt()
    prize = LuckyWheelPrize.objects.create(
        label="Legacy Spin",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=100,
        weight=10,
        is_active=True,
    )
    legacy_spin = LuckyWheelSpin.objects.create(
        user=user,
        profile=customer,
        grant=grant,
        prize=prize,
        status=LuckyWheelSpinStatus.AWARDED,
        prize_snapshot={"label": "Legacy Spin", "points_amount": 100},
        idempotency_key="legacy-spin-52",
        awarded_at=timezone.now() - timedelta(days=20),
    )

    # Replaying the legacy spin idempotency key returns the historical spin without creating a backfilled PrizeAward
    replayed_spin, _ = LuckyWheelSpinService.spin(
        user, customer, "legacy-spin-52"
    )
    assert replayed_spin.pk == legacy_spin.pk
    assert PrizeAward.objects.count() == 0


def test_53_migration_does_not_backfill_historical_season_winners():
    now = timezone.now()
    season = Season.objects.create(
        title="Legacy Paid Season",
        starts_at=now - timedelta(days=60),
        ends_at=now - timedelta(days=30),
        status=SeasonStatus.CALCULATED,
    )
    seller = SellerProfileFactory()
    buyer = CustomerProfileFactory()
    CompletedDealFactory(
        deal_type=CompletedDealType.MARKET,
        source_type=CompletedDealSourceType.ORDER_ITEM,
        buyer_user=buyer.user,
        buyer_profile=buyer,
        seller_user=seller.user,
        seller_profile=seller,
        amount=Decimal("100.00"),
        currency="EGP",
        completed_at=season.starts_at + timedelta(days=2),
    )
    # Historical payout already exists before BE-PRZ-01
    PointService.earn_points(
        user=seller.user,
        profile=seller,
        points=100000,
        reason="Historical season winner",
        source_type=RewardSourceType.SEASON_WINNER,
        source_id=str(season.pk),
        idempotency_key=f"season:{season.pk}:seller:{seller.pk}:rank:1",
    )

    SeasonService.finalize(season)

    # Historical winner is not paid again and not backfilled with a new PrizeAward
    assert PointService.get_balance(seller.user) == 100000
    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.SEASON
        ).count()
        == 0
    )


def test_54_migration_does_not_backfill_historical_referral_winners():
    now = timezone.now()
    _, manager_profile = make_manager()
    challenge = ReferralChallenge.objects.create(
        name="Legacy Finalized Challenge",
        starts_at=now - timedelta(days=60),
        ends_at=now - timedelta(days=30),
        finalized_at=now - timedelta(days=29),
        finalized_by=manager_profile.user,
    )
    winner_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    ReferralChallengeWinner.objects.create(
        challenge=challenge,
        rank=1,
        user=winner_user,
        score=5,
        score_achieved_at=now - timedelta(days=35),
        prize_code=ReferralChallengePrizeCode.IPHONE,
        prize_title="iPhone",
    )

    # Calling finalize on an already-finalized historical challenge is a no-op and does not backfill
    ReferralChallengeService.finalize(challenge.pk, manager_profile.user)
    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE
        ).count()
        == 0
    )


# ===========================================================================
# SIDE EFFECTS (55 - 57)
# ===========================================================================


def test_55_prize_award_creation_sends_zero_notifications():
    before = Notification.objects.count()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="555-1",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:55:1",
    )
    assert Notification.objects.count() == before == 0

    award = PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="555-2",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:55:2",
    )

    assert Notification.objects.count() == before + 1 == 1
    notif = Notification.objects.get()
    assert notif.object_type == "prize_award"
    assert notif.object_id == award.pk


def test_56_prize_award_creation_creates_zero_payment_requests():
    before = PaymentRequest.objects.count()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.SEASON,
        source_id="556-1",
        title="Season Rank 1",
        prize_snapshot={"pp_points": 100000},
        idempotency_key="prize:test:56:1",
    )
    PrizeAwardService.record_claim_required(
        user=user,
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id="556-2",
        title="iPhone",
        prize_snapshot={"prize_code": "IPHONE"},
        idempotency_key="prize:test:56:2",
    )

    assert PaymentRequest.objects.count() == before == 0


def test_57_no_weekly_draw_mutation_or_prize_award_created():
    now = timezone.now()
    _, manager_profile = make_manager()
    draw = WeeklyDraw.objects.create(
        title="Weekly Draw 57",
        starts_at=now - timedelta(days=8),
        ends_at=now - timedelta(days=1),
        status=WeeklyDrawStatus.CLOSED,
        created_by=manager_profile,
    )
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    entry = WeeklyDrawEntry.objects.create(
        draw=draw,
        user=user,
        status=WeeklyDrawEntryStatus.ASSIGNED,
        assigned_at=now - timedelta(days=5),
        idempotency_key=f"draw:onboarding:user:{user.pk}",
    )

    assert PrizeAward.objects.count() == 0

    PrizeAwardService.record_digital_fulfilled(
        user=user,
        source_type=PrizeAwardSourceType.LUCKY_WHEEL,
        source_id="557",
        title="100 PP",
        prize_snapshot={"points_amount": 100},
        idempotency_key="prize:test:57",
    )
    entry.refresh_from_db()
    draw.refresh_from_db()
    assert entry.status == WeeklyDrawEntryStatus.ASSIGNED
    assert draw.status == WeeklyDrawStatus.CLOSED


# ===========================================================================
# CROSS-EPIC REGRESSION SMOKE IN SUITE (58 - 63)
# ===========================================================================


def test_58_lucky_wheel_regression_smoke():
    wheel_user, wheel_customer, _ = make_customer_with_attempt()
    LuckyWheelPrize.objects.create(
        label="500 PP Wheel",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=500,
        weight=10,
        is_active=True,
    )
    spin, _ = LuckyWheelSpinService.spin(
        wheel_user, wheel_customer, "lw-spin-58"
    )
    assert spin.status == LuckyWheelSpinStatus.AWARDED
    assert PointService.get_balance(wheel_user) == 500
    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id=str(spin.pk),
            status=PrizeAwardStatus.FULFILLED,
            fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
        ).count()
        == 1
    )


def test_59_seasons_regression_smoke():
    season, sellers = make_ended_season_with_deals()
    season = SeasonService.finalize(season)
    assert season.status == SeasonStatus.FINALIZED
    assert PointService.get_balance(sellers[0].user) == 100000
    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.SEASON,
            status=PrizeAwardStatus.FULFILLED,
            fulfillment_type=PrizeAwardFulfillmentType.DIGITAL_AUTOMATIC,
        ).count()
        == 3
    )


def test_60_referrals_regression_smoke():
    challenge, inviters, reviewer = make_ended_referral_challenge_with_scorers()
    ReferralChallengeService.finalize(challenge.pk, reviewer)
    assert (
        ReferralChallengeWinner.objects.filter(challenge=challenge).count()
        == 3
    )
    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
            status=PrizeAwardStatus.PENDING,
            fulfillment_type=PrizeAwardFulfillmentType.CLAIM_REQUIRED,
        ).count()
        == 2
    )


def test_61_draws_regression_smoke():
    now = timezone.now()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    entry = WeeklyDrawEntryService.ensure_for_onboarding(user, now=now)
    assert entry is None
    assert WeeklyDrawEntry.objects.filter(user=user).count() == 0
    assert PrizeAward.objects.count() == 0


def test_62_promotions_regression_smoke():
    user, customer, _ = make_customer_with_attempt()
    offer = DiscountOffer.objects.create(
        title="Smoke 25% Discount",
        target_type=OfferTargetType.ALL,
        value_type=OfferValueType.PERCENTAGE,
        value=Decimal("25.00"),
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        is_active=True,
    )
    LuckyWheelPrize.objects.create(
        label="25% Discount Wheel",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=10,
        is_active=True,
    )
    spin, _ = LuckyWheelSpinService.spin(user, customer, "lw-spin-62")
    assert UserPromotionGrant.objects.filter(user=user).count() == 1
    assert (
        PrizeAward.objects.filter(
            source_type=PrizeAwardSourceType.LUCKY_WHEEL,
            source_id=str(spin.pk),
        ).count()
        == 1
    )


def test_63_loyalty_and_rewards_sprint_regression_smoke():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    tx = PointService.earn_points(
        user=user,
        profile=user.active_profile,
        points=100,
        reason="Smoke loyalty earn",
        source_type=RewardSourceType.ONBOARDING,
        source_id=str(user.pk),
        idempotency_key=f"smoke:loyalty:{user.pk}",
    )
    assert tx.points == 100
    assert PointService.get_balance(user) == 100
    assert PrizeAward.objects.count() == 0


def test_64_annual_loft_subscription_winner_remains_safely_deferred_while_blocked():
    from apps.loft_subscriptions.models import (
        LoftSubscription,
        PedigreeTemplatePackage,
        SubscriptionDurationOption,
        SubscriptionInvoice,
        SubscriptionPaymentRequest,
    )
    from apps.notifications.models import NotificationType

    SubscriptionDurationOption.objects.create(
        code="month_12",
        name="One Year",
        duration_months=12,
        price=Decimal("1700.00"),
        sort_order=1,
    )
    PedigreeTemplatePackage.objects.create(
        code="shapes_1",
        name="One Shape",
        template_limit=1,
        price=Decimal("0.00"),
        sort_order=1,
    )
    PedigreeTemplatePackage.objects.create(
        code="shapes_unlimited",
        name="Unlimited Shapes",
        is_unlimited=True,
        price=Decimal("999.00"),
        sort_order=2,
    )

    challenge, inviters, reviewer = make_ended_referral_challenge_with_scorers()
    # Give Rank 3 winner both Customer and Seller profiles to confirm no profile-guessing occurs
    SellerProfileFactory(user=inviters[2], is_active=False, activated=True)

    ReferralChallengeService.finalize(challenge.pk, reviewer)
    ReferralChallengeService.finalize(challenge.pk, reviewer)

    rank_3_winner = ReferralChallengeWinner.objects.get(
        challenge=challenge,
        rank=3,
    )
    assert rank_3_winner.user_id == inviters[2].pk
    assert (
        rank_3_winner.prize_code
        == ReferralChallengePrizeCode.ANNUAL_LOFT_SUBSCRIPTION
    )
    assert LoftSubscription.objects.count() == 0
    assert SubscriptionInvoice.objects.count() == 0
    assert SubscriptionPaymentRequest.objects.count() == 0
    assert not PrizeAward.objects.filter(
        source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
        source_id=str(rank_3_winner.pk),
    ).exists()
    assert not PrizeAward.objects.filter(user=inviters[2]).exists()
    assert not Notification.objects.filter(
        recipient_profile__user=inviters[2],
        notification_type=NotificationType.PRIZE_AWARDED,
    ).exists()

