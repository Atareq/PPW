from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.lucky_wheel.models import (
    LuckyWheelGrant,
    LuckyWheelGrantSourceType,
    LuckyWheelPrize,
    LuckyWheelPrizeType,
    LuckyWheelSpin,
    LuckyWheelSpinStatus,
)
from apps.loyalty.constants import RewardSourceType
from apps.loyalty.models import PointTransaction
from apps.orders.services import OrderCheckoutService
from apps.promotions.models import (
    CashbackTransaction,
    DiscountOffer,
    OfferTriggerType,
    UserPromotionGrant,
)
from apps.referrals.models import LinkSharingCode
from apps.referrals.services import REFERRAL_INVITER_POINTS
from apps.users.constants import ProfileType
from tests.factories import (
    CashbackOfferFactory,
    CustomerProfileFactory,
    DiscountOfferFactory,
    SupplyFactory,
    UserAccountFactory,
    customer_registration_payload,
)


pytestmark = pytest.mark.django_db


def authenticated_client(user=None):
    client = APIClient()
    if user is not None:
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def test_non_global_discount_offer_is_not_auto_applied():
    customer = CustomerProfileFactory(is_active=True)
    asset = SupplyFactory(price=Decimal("25.00"), count=3, is_market_listed=True)
    DiscountOfferFactory(
        value=Decimal("5.00"),
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        is_active=True,
    )

    order = OrderCheckoutService.create_order_from_items(
        customer,
        [{"asset_id": asset.pk, "quantity": 1}],
    )
    item = order.items.first()

    assert item.discount_offer is None
    assert item.discount_amount == Decimal("0.00")
    assert item.total_price == Decimal("25.00")


def test_lucky_wheel_spin_is_backend_selected_and_idempotent():
    customer = CustomerProfileFactory(is_active=True)
    grant = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        source_id=str(customer.pk),
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key=f"test:onboarding:{customer.pk}",
    )
    LuckyWheelPrize.objects.create(
        label="Ten PP",
        prize_type=LuckyWheelPrizeType.PP_COINS,
        points_amount=10,
        weight=1,
    )
    client = authenticated_client(customer.user)

    response = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-key-1"},
        format="json",
    )
    retry = client.post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-key-1"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert retry.status_code == status.HTTP_200_OK
    assert response.data["spin_id"] == retry.data["spin_id"]
    assert LuckyWheelSpin.objects.count() == 1
    grant.refresh_from_db()
    assert grant.remaining_attempts == 0
    assert PointTransaction.objects.filter(
        user=customer.user,
        source_type=RewardSourceType.LUCKY_WHEEL,
        points=10,
    ).count() == 1


def test_lucky_wheel_discount_prize_creates_user_promotion_grant():
    customer = CustomerProfileFactory(is_active=True)
    offer = DiscountOfferFactory(
        trigger_type=OfferTriggerType.LUCKY_WHEEL,
        value=Decimal("5.00"),
    )
    LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        total_attempts=1,
        remaining_attempts=1,
        idempotency_key="grant-discount",
    )
    LuckyWheelPrize.objects.create(
        label="Discount",
        prize_type=LuckyWheelPrizeType.DISCOUNT_OFFER,
        discount_offer=offer,
        weight=1,
    )

    response = authenticated_client(customer.user).post(
        "/api/lucky-wheel/spin/",
        {"idempotency_key": "spin-discount"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert UserPromotionGrant.objects.filter(
        user=customer.user,
        discount_offer=offer,
        source_type=RewardSourceType.LUCKY_WHEEL,
    ).count() == 1


def test_admin_lucky_wheel_spin_list_includes_user_identity():
    customer = CustomerProfileFactory(is_active=True)
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    grant = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        total_attempts=1,
        remaining_attempts=0,
        idempotency_key="admin-spin-grant",
    )
    prize = LuckyWheelPrize.objects.create(
        label="No prize",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
    )
    spin = LuckyWheelSpin.objects.create(
        user=customer.user,
        profile=customer,
        grant=grant,
        prize=prize,
        status=LuckyWheelSpinStatus.NO_PRIZE,
        prize_snapshot={"label": "No prize"},
        idempotency_key="admin-spin",
    )

    response = authenticated_client(manager).get("/api/lucky-wheel/admin/spins/")

    assert response.status_code == status.HTTP_200_OK
    result = response.data["results"][0]
    assert result["id"] == spin.pk
    assert result["user_id"] == customer.user_id
    assert result["profile_id"] == customer.pk
    assert result["username"] == customer.user.username


def test_customer_lucky_wheel_spin_list_uses_customer_serializer():
    customer = CustomerProfileFactory(is_active=True)
    grant = LuckyWheelGrant.objects.create(
        user=customer.user,
        profile=customer,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
        total_attempts=1,
        remaining_attempts=0,
        idempotency_key="customer-spin-grant",
    )
    prize = LuckyWheelPrize.objects.create(
        label="No prize",
        prize_type=LuckyWheelPrizeType.NO_PRIZE,
        weight=1,
    )
    LuckyWheelSpin.objects.create(
        user=customer.user,
        profile=customer,
        grant=grant,
        prize=prize,
        status=LuckyWheelSpinStatus.NO_PRIZE,
        prize_snapshot={"label": "No prize"},
        idempotency_key="customer-spin",
    )

    response = authenticated_client(customer.user).get("/api/lucky-wheel/spins/my/")

    assert response.status_code == status.HTTP_200_OK
    result = response.data["results"][0]
    assert "user_id" not in result
    assert "profile_id" not in result
    assert "username" not in result


def test_registration_grants_one_onboarding_lucky_wheel_attempt():
    payload = customer_registration_payload(phone_number="+201001234999")
    payload["username"] = "onboarding-customer"

    response = authenticated_client().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    grant = LuckyWheelGrant.objects.get(
        source_type=LuckyWheelGrantSourceType.ONBOARDING
    )
    assert grant.remaining_attempts == 1
    assert grant.total_attempts == 1


def test_referral_redeem_awards_inviter_points_once():
    inviter = CustomerProfileFactory(is_active=True)
    redeemer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    redeemer = redeemer_user.active_profile
    code = LinkSharingCode.objects.create(
        owner_profile=inviter,
        code="ABC123",
        expires_at=timezone.now() + timezone.timedelta(hours=1),
    )
    client = authenticated_client(redeemer_user)

    response = client.post("/api/referrals/redeem/", {"code": code.code})
    retry = client.post("/api/referrals/redeem/", {"code": code.code})

    assert response.status_code == status.HTTP_200_OK
    assert retry.status_code == status.HTTP_400_BAD_REQUEST
    code.refresh_from_db()
    assert code.is_used is True
    assert code.used_by_profile_id == redeemer.pk
    assert PointTransaction.objects.filter(
        user=inviter.user,
        source_type=RewardSourceType.REFERRAL_ACCEPTED,
        points=REFERRAL_INVITER_POINTS,
    ).count() == 1


def test_customer_buys_points_with_cashback_idempotently():
    customer = CustomerProfileFactory(
        is_active=True,
        cashback_balance=Decimal("10.00"),
    )
    CashbackOfferFactory(is_active=True)
    client = authenticated_client(customer.user)

    response = client.post(
        "/api/loyalty/points/buy-with-cashback/",
        {"cashback_amount": "2.00", "idempotency_key": "buy-1"},
        format="json",
    )
    retry = client.post(
        "/api/loyalty/points/buy-with-cashback/",
        {"cashback_amount": "2.00", "idempotency_key": "buy-1"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert retry.status_code == status.HTTP_200_OK
    customer.refresh_from_db()
    assert customer.cashback_balance == Decimal("8.00")
    assert CashbackTransaction.objects.filter(customer=customer).count() == 1
    assert PointTransaction.objects.filter(
        user=customer.user,
        source_type=RewardSourceType.CASHBACK_POINTS_PURCHASE,
        points=40,
    ).count() == 1
