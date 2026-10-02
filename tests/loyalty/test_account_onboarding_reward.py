"""BE-REW-02: onboarding PP is awarded once per UserAccount, not per profile."""

import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.loyalty.constants import (
    BadgeType,
    RewardEventType,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import PointService
from apps.lucky_wheel.models import LuckyWheelGrant, LuckyWheelGrantSourceType
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db

REGISTER_CUSTOMER_URL = "/api/auth/register/customer/"
SELLER_PROFILE_URL = "/api/users/mobile/sellers/"


def authenticated_client(user=None):
    client = APIClient()
    if user is not None:
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def onboarding_transactions(user):
    return PointTransaction.objects.filter(
        user=user,
        source_type=RewardSourceType.ONBOARDING,
    )


def register_customer(username="onboarded", phone_number="+201001230001"):
    payload = customer_registration_payload(phone_number=phone_number)
    payload["username"] = username
    response = authenticated_client().post(
        REGISTER_CUSTOMER_URL,
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    return UserAccount.objects.get(username=username)


def create_seller_mode(user):
    response = authenticated_client(user).post(
        SELLER_PROFILE_URL,
        {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
            "recoded_voice": "voice-key",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    return SellerProfile.objects.get(user=user)


def test_customer_registration_awards_account_onboarding_once():
    user = register_customer()
    customer_profile = CustomerProfile.objects.get(user=user)

    assert PointService.get_balance(user) == 100
    transaction = onboarding_transactions(user).get()
    assert transaction.user_id == user.pk
    assert transaction.profile_id == customer_profile.pk
    assert transaction.points == 100
    assert transaction.idempotency_key == f"reward:onboarding:user:{user.pk}"
    assert PointTransaction.objects.filter(user=user).count() == 1


def test_seller_mode_created_later_does_not_award_onboarding_again():
    user = register_customer()
    customer_profile = CustomerProfile.objects.get(user=user)

    create_seller_mode(user)

    assert PointService.get_balance(user) == 100
    assert PointWallet.objects.filter(user=user).count() == 1
    transaction = onboarding_transactions(user).get()
    assert transaction.profile_id == customer_profile.pk
    assert PointTransaction.objects.filter(user=user).count() == 1


def test_seller_first_onboarding_awards_once_across_both_modes():
    user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_profile = user.active_profile

    RewardEngine.handle_event(
        RewardEventType.SELLER_PROFILE_CREATED,
        actor=seller_profile,
        profile=seller_profile,
    )
    customer_profile = CustomerProfileFactory(user=user, is_active=False)
    result = RewardEngine.handle_event(
        RewardEventType.CUSTOMER_PROFILE_CREATED,
        actor=customer_profile,
        profile=customer_profile,
    )

    assert PointService.get_balance(user) == 100
    transaction = onboarding_transactions(user).get()
    assert transaction.profile_id == seller_profile.pk
    assert result["points_awarded"] == []
    assert {
        "code": RewardSourceType.ONBOARDING,
        "point_transaction_id": transaction.pk,
    } in result["already_awarded"]


def test_account_onboarding_helper_is_idempotent_across_profiles():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    seller_profile = SellerProfileFactory(user=user, is_active=False)

    first = RewardEngine.grant_account_onboarding_reward(user, customer_profile)
    second = RewardEngine.grant_account_onboarding_reward(user, customer_profile)
    third = RewardEngine.grant_account_onboarding_reward(user, seller_profile)

    assert first.pk == second.pk == third.pk
    assert third.profile_id == customer_profile.pk
    assert onboarding_transactions(user).count() == 1
    assert PointService.get_balance(user) == 100


def test_account_onboarding_rejects_profile_from_another_account():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    other_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    with pytest.raises(ValidationError):
        RewardEngine.grant_account_onboarding_reward(user, other_user.active_profile)

    assert not PointTransaction.objects.exists()


def test_legacy_welcome_points_count_as_account_onboarding():
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    legacy = PointService.earn_points(
        user=user,
        profile=customer_profile,
        points=100,
        reason=f"Reward: {BadgeType.WELCOME_BUYER}",
        source_type="CustomerProfile",
        source_id=str(customer_profile.pk),
        idempotency_key=(
            f"reward:{BadgeType.WELCOME_BUYER}:"
            f"{RewardEventType.CUSTOMER_PROFILE_CREATED}:CustomerProfile:"
            f"{customer_profile.pk}:profile:{customer_profile.pk}"
        ),
    )
    seller_profile = SellerProfileFactory(user=user, is_active=False)

    RewardEngine.handle_event(
        RewardEventType.SELLER_PROFILE_CREATED,
        actor=seller_profile,
        profile=seller_profile,
    )

    assert PointService.get_balance(user) == 100
    assert list(PointTransaction.objects.filter(user=user)) == [legacy]
    assert BadgeAward.objects.filter(
        profile=seller_profile,
        badge_type=BadgeType.WELCOME_SELLER,
    ).exists()


def test_account_onboarding_is_independent_per_user():
    user_a = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    user_b = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    tx_a = RewardEngine.grant_account_onboarding_reward(user_a, user_a.active_profile)
    tx_b = RewardEngine.grant_account_onboarding_reward(user_b, user_b.active_profile)

    assert tx_a.pk != tx_b.pk
    assert tx_a.idempotency_key != tx_b.idempotency_key
    assert PointService.get_balance(user_a) == 100
    assert PointService.get_balance(user_b) == 100


def test_welcome_badges_stay_profile_specific_with_single_onboarding_reward():
    user = register_customer()
    customer_profile = CustomerProfile.objects.get(user=user)
    seller_profile = create_seller_mode(user)

    assert BadgeAward.objects.filter(
        profile=customer_profile,
        badge_type=BadgeType.WELCOME_BUYER,
    ).count() == 1
    assert BadgeAward.objects.filter(
        profile=seller_profile,
        badge_type=BadgeType.WELCOME_SELLER,
    ).count() == 1
    assert onboarding_transactions(user).count() == 1
    assert PointService.get_balance(user) == 100


def test_onboarding_balance_is_shared_after_switching_to_seller_mode():
    user = register_customer()
    seller_profile = create_seller_mode(user)

    switched, profile_type = switch_user_profile(user, ProfileType.SELLER)
    user.refresh_from_db()

    assert switched is True
    assert profile_type == ProfileType.SELLER
    response = authenticated_client(user).get("/api/loyalty/points/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["active_profile_id"] == seller_profile.pk
    assert response.data["balance"] == 100


def test_lucky_wheel_onboarding_attempts_are_unchanged():
    user = register_customer()
    customer_profile = CustomerProfile.objects.get(user=user)
    seller_profile = create_seller_mode(user)
    RewardEngine.handle_event(
        RewardEventType.SELLER_PROFILE_CREATED,
        actor=seller_profile,
        profile=seller_profile,
    )

    grants = LuckyWheelGrant.objects.filter(
        user=user,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
    )
    # Existing behavior: one onboarding attempt per created profile.
    assert grants.filter(profile=customer_profile).count() == 1
    assert grants.filter(profile=seller_profile).count() == 1
    assert all(grant.remaining_attempts == 1 for grant in grants)
    assert PointService.get_balance(user) == 100
