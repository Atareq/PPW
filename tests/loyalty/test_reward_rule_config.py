"""BE-REW-03: reward rule values come from canonical defaults plus DB overrides."""

from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.models import UserAccount
from apps.authentication.services import build_token_for_user
from apps.loyalty.constants import (
    BadgeType,
    RewardRuleCode,
    RewardSourceType,
)
from apps.loyalty.models import BadgeAward, PointTransaction, RewardRuleConfig
from apps.loyalty.reward_engine import RewardEngine
from apps.loyalty.services import PointService, RewardRuleService
from apps.lucky_wheel.models import LuckyWheelGrant, LuckyWheelGrantSourceType
from apps.users.constants import ProfileType
from tests.factories import (
    ManagerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
    customer_registration_payload,
)

pytestmark = pytest.mark.django_db

RULES_URL = "/api/loyalty/admin/reward-rules/"
ONBOARDING = RewardRuleCode.ACCOUNT_ONBOARDING


def rule_url(code):
    return f"{RULES_URL}{code}/"


def authenticated_client(user=None):
    client = APIClient()
    if user is not None:
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def manager_user(permissions=None):
    user = UserAccountFactory(with_profile=False)
    kwargs = {"user": user, "is_active": True}
    if permissions is not None:
        kwargs["permissions"] = permissions
    ManagerProfileFactory(**kwargs)
    return user


def register_customer(username, phone_number):
    payload = customer_registration_payload(phone_number=phone_number)
    payload["username"] = username
    response = authenticated_client().post(
        "/api/auth/register/customer/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    return UserAccount.objects.get(username=username)


def onboarding_transactions(user):
    return PointTransaction.objects.filter(
        user=user,
        source_type=RewardSourceType.ONBOARDING,
    )


def set_rule(code, **changes):
    return RewardRuleService.update_rule(code, updated_by=None, **changes)


# --- Service: defaults and overrides ---------------------------------------


def test_default_onboarding_rule_is_100():
    rule = RewardRuleService.get_rule(ONBOARDING)

    assert rule.points == 100
    assert rule.enabled is True
    assert rule.is_overridden is False
    assert rule.is_implemented is True
    assert RewardRuleService.get_points(ONBOARDING) == 100
    assert RewardRuleService.is_enabled(ONBOARDING) is True
    assert not RewardRuleConfig.objects.exists()


def test_active_auction_bidder_default_has_points_and_threshold():
    rule = RewardRuleService.get_rule(RewardRuleCode.ACTIVE_AUCTION_BIDDER)

    assert rule.points == 50
    assert rule.threshold == 5
    assert RewardRuleService.get_threshold(RewardRuleCode.ACTIVE_AUCTION_BIDDER) == 5
    assert rule.required_fields == ("points", "threshold")
    # Read by RewardEngine since BE-REW-07.
    assert rule.is_implemented is True


def test_pending_rules_have_no_invented_points():
    for code in (RewardRuleCode.POSITIVE_RATING,):
        rule = RewardRuleService.get_rule(code)
        assert rule.points is None
        assert rule.threshold is None
        assert rule.is_implemented is False


def test_unknown_rule_code_fails_loudly():
    with pytest.raises(ValidationError):
        RewardRuleService.get_rule("NOT_A_RULE")
    with pytest.raises(ValidationError):
        RewardRuleService.update_rule("NOT_A_RULE", updated_by=None, points=5)

    assert not RewardRuleConfig.objects.exists()


def test_missing_override_falls_back_to_canonical_default():
    set_rule(ONBOARDING, points=150)
    assert RewardRuleService.get_points(ONBOARDING) == 150

    RewardRuleConfig.objects.get(code=ONBOARDING).delete()

    rule = RewardRuleService.get_rule(ONBOARDING)
    assert rule.points == 100
    assert rule.is_overridden is False
    # A new override can be created after the old row was soft-deleted.
    assert set_rule(ONBOARDING, points=120).points == 120


# --- Onboarding integration -------------------------------------------------


def test_db_override_changes_onboarding_points_and_awards_once():
    set_rule(ONBOARDING, points=150)

    user = register_customer("configured", "+201001240001")
    seller_profile = SellerProfileFactory(user=user, is_active=False)
    RewardEngine.grant_account_onboarding_reward(user, seller_profile)

    transaction = onboarding_transactions(user).get()
    assert transaction.points == 150
    assert PointService.get_balance(user) == 150


def test_disabled_onboarding_awards_no_pp_but_keeps_welcome_badge():
    set_rule(ONBOARDING, enabled=False)

    user = register_customer("disabled", "+201001240002")

    assert not onboarding_transactions(user).exists()
    assert PointService.get_balance(user) == 0
    assert BadgeAward.objects.filter(
        profile__user=user,
        badge_type=BadgeType.WELCOME_BUYER,
    ).count() == 1
    from apps.draws.models import WeeklyDrawEntry, WeeklyDrawEntrySource

    assert LuckyWheelGrant.objects.filter(
        user=user,
        source_type=LuckyWheelGrantSourceType.ONBOARDING,
    ).count() == 0
    assert WeeklyDrawEntry.objects.filter(
        user=user,
        source=WeeklyDrawEntrySource.ONBOARDING,
    ).count() == 1


def test_zero_point_onboarding_awards_nothing():
    set_rule(ONBOARDING, points=0)

    user = register_customer("zero", "+201001240003")

    assert not onboarding_transactions(user).exists()
    assert PointService.get_balance(user) == 0


def test_onboarding_idempotency_holds_with_configured_value():
    set_rule(ONBOARDING, points=150)
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    customer_profile = user.active_profile
    seller_profile = SellerProfileFactory(user=user, is_active=False)

    first = RewardEngine.grant_account_onboarding_reward(user, customer_profile)
    set_rule(ONBOARDING, points=200)
    second = RewardEngine.grant_account_onboarding_reward(user, seller_profile)

    assert first.pk == second.pk
    assert onboarding_transactions(user).count() == 1
    assert second.points == 150
    assert PointService.get_balance(user) == 150


def test_config_change_does_not_alter_existing_transactions():
    user_a = register_customer("before-change", "+201001240004")
    tx_a = onboarding_transactions(user_a).get()
    assert tx_a.points == 100

    set_rule(ONBOARDING, points=150)
    user_b = register_customer("after-change", "+201001240005")

    tx_a.refresh_from_db()
    assert tx_a.points == 100
    assert tx_a.balance_after == 100
    assert PointService.get_balance(user_a) == 100
    assert onboarding_transactions(user_b).get().points == 150
    assert PointService.get_balance(user_b) == 150


# --- Management API ---------------------------------------------------------


def test_manager_lists_every_supported_rule_with_defaults():
    response = authenticated_client(manager_user()).get(RULES_URL)

    assert response.status_code == status.HTTP_200_OK
    rules = {item["code"]: item for item in response.data["results"]}
    assert set(rules) == set(RewardRuleCode.values)
    assert rules[ONBOARDING]["points"] == 100
    assert rules[ONBOARDING]["is_overridden"] is False
    assert rules[RewardRuleCode.ACTIVE_AUCTION_BIDDER]["threshold"] == 5


def test_manager_partial_update_creates_override():
    manager = manager_user()
    client = authenticated_client(manager)

    response = client.patch(
        rule_url(ONBOARDING),
        {"points": 150, "multiplier": "1.5", "configuration": {"note": "launch"}},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["points"] == 150
    assert response.data["is_overridden"] is True
    assert response.data["updated_by_id"] == manager.pk
    config = RewardRuleConfig.objects.get(code=ONBOARDING)
    assert config.points == 150
    assert config.multiplier == Decimal("1.5")
    assert config.configuration == {"note": "launch"}
    assert client.get(rule_url(ONBOARDING)).data["points"] == 150


def test_protected_fields_are_not_editable():
    client = authenticated_client(manager_user())

    response = client.patch(
        rule_url(ONBOARDING),
        {"code": RewardRuleCode.MUTUAL_RATING, "is_implemented": False, "points": 120},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["code"] == ONBOARDING
    assert response.data["is_implemented"] is True
    assert list(RewardRuleConfig.objects.values_list("code", flat=True)) == [
        ONBOARDING
    ]


@pytest.mark.parametrize(
    "payload",
    [{"points": -1}, {"threshold": -1}, {"multiplier": "-0.5"}],
)
def test_negative_values_are_rejected(payload):
    response = authenticated_client(manager_user()).patch(
        rule_url(RewardRuleCode.ACTIVE_AUCTION_BIDDER),
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(payload) <= set(response.data)
    assert not RewardRuleConfig.objects.exists()


@pytest.mark.parametrize(
    ("code", "payload"),
    [
        (ONBOARDING, {"points": None}),
        (RewardRuleCode.ACTIVE_AUCTION_BIDDER, {"threshold": None}),
    ],
)
def test_required_rule_fields_cannot_be_cleared(code, payload):
    response = authenticated_client(manager_user()).patch(
        rule_url(code),
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(payload) <= set(response.data)
    assert not RewardRuleConfig.objects.exists()


def test_pending_rule_accepts_null_points():
    response = authenticated_client(manager_user()).patch(
        rule_url(RewardRuleCode.DEAL_COMPLETED),
        {"points": None, "enabled": False},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["points"] is None
    assert response.data["enabled"] is False


@pytest.mark.parametrize(
    "configuration",
    [["not", "an", "object"], {"rule": {"if": "points > 5"}}, {"k": [{"a": 1}]}],
)
def test_configuration_must_be_a_flat_object(configuration):
    response = authenticated_client(manager_user()).patch(
        rule_url(ONBOARDING),
        {"configuration": configuration},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "configuration" in response.data


def test_unknown_rule_code_cannot_be_created_through_api():
    client = authenticated_client(manager_user())

    create_response = client.post(
        RULES_URL,
        {"code": "FREE_MONEY", "points": 999},
        format="json",
    )
    patch_response = client.patch(rule_url("FREE_MONEY"), {"points": 999}, format="json")
    get_response = client.get(rule_url("FREE_MONEY"))

    # No manager permission maps to POST/DELETE, so HasAppAccess denies them
    # before DRF would answer 405.
    assert create_response.status_code == status.HTTP_403_FORBIDDEN
    assert patch_response.status_code == status.HTTP_404_NOT_FOUND
    assert get_response.status_code == status.HTTP_404_NOT_FOUND
    assert client.delete(rule_url(ONBOARDING)).status_code == (
        status.HTTP_403_FORBIDDEN
    )
    assert not RewardRuleConfig.objects.exists()


def test_superuser_still_cannot_create_or_delete_rules():
    superuser = manager_user()
    superuser.is_superuser = True
    superuser.save(update_fields=["is_superuser"])
    client = authenticated_client(superuser)

    create_response = client.post(RULES_URL, {"code": "FREE_MONEY"}, format="json")
    delete_response = client.delete(rule_url(ONBOARDING))

    assert create_response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert delete_response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert not RewardRuleConfig.objects.exists()


@pytest.mark.parametrize("profile_type", [ProfileType.CUSTOMER, ProfileType.SELLER])
def test_non_manager_cannot_read_or_modify_reward_rules(profile_type):
    client = authenticated_client(UserAccountFactory(with_profile=profile_type))

    assert client.get(RULES_URL).status_code == status.HTTP_403_FORBIDDEN
    response = client.patch(rule_url(ONBOARDING), {"points": 999}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not RewardRuleConfig.objects.exists()


def test_manager_needs_exact_update_permission():
    client = authenticated_client(
        manager_user(permissions=["reward_rules.list", "reward_rules.retrieve"])
    )

    assert client.get(RULES_URL).status_code == status.HTTP_200_OK
    assert client.get(rule_url(ONBOARDING)).status_code == status.HTTP_200_OK
    response = client.patch(rule_url(ONBOARDING), {"points": 999}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not RewardRuleConfig.objects.exists()
