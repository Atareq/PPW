from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.loyalty.constants import BadgeType, PointTransactionType
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.loyalty.serializers import BadgeAwardCreateSerializer
from apps.loyalty.services import BadgeService, PointService
from apps.users.constants import ProfileType
from tests.factories import (
    BadgeAwardFactory,
    PointWalletFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


class TestPointWallet:
    def test_wallet_is_created_lazily(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        assert PointWallet.objects.filter(user=user).count() == 0

        wallet = PointService.get_or_create_wallet(user)

        assert wallet.user_id == user.pk
        assert wallet.balance == 0
        assert PointWallet.objects.filter(user=user).count() == 1

    def test_one_wallet_per_user(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        first_wallet = PointService.get_or_create_wallet(user)
        second_wallet = PointService.get_or_create_wallet(user)

        assert first_wallet.pk == second_wallet.pk
        assert PointWallet.objects.filter(user=user).count() == 1

    def test_balance_cannot_be_negative(self):
        wallet = PointWalletFactory.build(balance=-1)

        with pytest.raises(DjangoValidationError):
            wallet.full_clean()

    def test_wallet_is_not_tied_to_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        SellerProfileFactory(user=user)

        wallet = PointService.get_or_create_wallet(user)

        assert wallet.user_id == user.pk
        assert not hasattr(wallet, "profile_id")


class TestPointService:
    def test_earn_points_updates_wallet_and_creates_transaction(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        transaction = PointService.earn_points(
            user,
            user.active_profile,
            25,
            "Completed order",
            source_type="orders.Order",
            source_id=42,
            metadata={"order_id": 42},
        )

        wallet = PointWallet.objects.get(user=user)
        assert wallet.balance == 25
        assert transaction.wallet_id == wallet.pk
        assert transaction.user_id == user.pk
        assert transaction.profile_id == user.active_profile.pk
        assert transaction.transaction_type == PointTransactionType.EARN
        assert transaction.balance_after == 25
        assert transaction.source_id == "42"
        assert transaction.metadata == {"order_id": 42}

    def test_spend_points_decreases_wallet_balance(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PointService.earn_points(user, None, 50, "Admin gift")

        transaction = PointService.spend_points(
            user,
            user.active_profile,
            15,
            "Redeemed points",
        )

        wallet = PointWallet.objects.get(user=user)
        assert wallet.balance == 35
        assert transaction.transaction_type == PointTransactionType.SPEND
        assert transaction.balance_after == 35

    def test_expire_points_decreases_wallet_balance(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PointService.earn_points(user, None, 50, "Admin gift")

        transaction = PointService.expire_points(
            user,
            None,
            20,
            "Expired points",
        )

        assert PointService.get_balance(user) == 30
        assert transaction.transaction_type == PointTransactionType.EXPIRE
        assert transaction.profile_id is None

    def test_spend_fails_with_insufficient_balance(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(ValidationError, match="Insufficient"):
            PointService.spend_points(user, user.active_profile, 1, "Redeem")

        assert PointService.get_balance(user) == 0
        assert PointTransaction.objects.count() == 0

    def test_idempotency_key_prevents_duplicate_earn(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        first = PointService.earn_points(
            user,
            user.active_profile,
            10,
            "Bonus",
            idempotency_key="bonus-1",
        )
        second = PointService.earn_points(
            user,
            user.active_profile,
            10,
            "Bonus",
            idempotency_key="bonus-1",
        )

        assert first.pk == second.pk
        assert PointTransaction.objects.count() == 1
        assert PointService.get_balance(user) == 10

    def test_idempotency_key_prevents_duplicate_spend(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PointService.earn_points(user, None, 30, "Admin gift")

        first = PointService.spend_points(
            user,
            user.active_profile,
            10,
            "Redeem",
            idempotency_key="redeem-1",
        )
        second = PointService.spend_points(
            user,
            user.active_profile,
            10,
            "Redeem",
            idempotency_key="redeem-1",
        )

        assert first.pk == second.pk
        assert PointTransaction.objects.count() == 2
        assert PointService.get_balance(user) == 20

    def test_idempotency_key_conflict_is_rejected(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PointService.earn_points(user, None, 10, "Bonus", idempotency_key="x")

        with pytest.raises(ValidationError, match="different transaction"):
            PointService.earn_points(
                user,
                None,
                20,
                "Bonus",
                idempotency_key="x",
            )

        assert PointService.get_balance(user) == 10

    def test_transaction_can_store_null_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        transaction = PointService.earn_points(
            user,
            None,
            5,
            "Registration bonus",
        )

        assert transaction.profile_id is None

    def test_transaction_points_must_be_positive(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(ValidationError, match="greater than 0"):
            PointService.earn_points(user, None, 0, "Invalid")

    def test_profile_must_belong_to_user_when_provided(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(ValidationError, match="belong to the user"):
            PointService.earn_points(
                user,
                other_user.active_profile,
                10,
                "Invalid profile",
            )


class TestBadgeService:
    def test_award_badge_creates_award_for_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        award = BadgeService.award_badge(
            user.active_profile,
            BadgeType.FIRST_ORDER,
            source_type="orders.Order",
            source_id=1,
            metadata={"order_id": 1},
        )

        assert award.profile_id == user.active_profile.pk
        assert award.badge_type == BadgeType.FIRST_ORDER
        assert award.source_id == "1"
        assert award.metadata == {"order_id": 1}

    def test_award_badge_is_idempotent_for_same_profile_and_badge(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        first = BadgeService.award_badge(
            user.active_profile,
            BadgeType.FIRST_ORDER,
        )
        second = BadgeService.award_badge(
            user.active_profile,
            BadgeType.FIRST_ORDER,
        )

        assert first.pk == second.pk
        assert BadgeAward.objects.count() == 1

    def test_same_badge_can_be_awarded_to_two_profiles_under_same_user(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_profile = SellerProfileFactory(user=user)

        customer_award = BadgeService.award_badge(
            user.active_profile,
            BadgeType.POINTS_STARTER,
        )
        seller_award = BadgeService.award_badge(
            seller_profile,
            BadgeType.POINTS_STARTER,
        )

        assert customer_award.profile_id != seller_award.profile_id
        assert BadgeAward.objects.count() == 2

    def test_customer_badge_cannot_be_awarded_to_seller(self):
        user = UserAccountFactory(with_profile=ProfileType.SELLER)

        with pytest.raises(ValidationError, match="not eligible"):
            BadgeService.award_badge(
                user.active_profile,
                BadgeType.FIRST_ORDER,
            )

    def test_seller_badge_cannot_be_awarded_to_customer(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(ValidationError, match="not eligible"):
            BadgeService.award_badge(
                user.active_profile,
                BadgeType.SELLER_STARTER,
            )

    def test_unsupported_badge_type_is_serializer_validated(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        serializer = BadgeAwardCreateSerializer(
            data={
                "profile_id": user.active_profile.pk,
                "badge_type": "unsupported",
            }
        )

        assert not serializer.is_valid()
        assert "badge_type" in serializer.errors

    def test_any_badge_can_be_awarded_to_any_profile(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        BadgeService.award_badge(
            customer.active_profile,
            BadgeType.POINTS_STARTER,
        )
        BadgeService.award_badge(
            seller.active_profile,
            BadgeType.POINTS_STARTER,
        )

        assert BadgeAward.objects.count() == 2

    def test_get_earned_badges_returns_only_profile_badges(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        own_award = BadgeService.award_badge(
            user.active_profile,
            BadgeType.FIRST_ORDER,
        )
        BadgeService.award_badge(
            other_user.active_profile,
            BadgeType.FIRST_ORDER,
        )

        awards = list(BadgeService.get_earned_badges(user.active_profile))

        assert awards == [own_award]


class TestLoyaltyEndpoints:
    def test_user_can_view_points_balance(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PointService.earn_points(user, user.active_profile, 12, "Bonus")
        client = authenticated_client(user)

        response = client.get("/api/loyalty/points/")

        assert response.status_code == 200
        assert response.data["user_id"] == user.pk
        assert response.data["active_profile_id"] == user.active_profile.pk
        assert response.data["balance"] == 12

    def test_point_endpoints_require_authentication(self, api_client):
        balance_response = api_client.get("/api/loyalty/points/")
        transactions_response = api_client.get(
            "/api/loyalty/points/transactions/"
        )

        assert balance_response.status_code == 401
        assert transactions_response.status_code == 401

    def test_point_endpoints_require_active_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(user)
        profile = user.active_profile
        profile.is_valid = False
        profile.save(update_fields=["is_valid"])

        response = client.get("/api/loyalty/points/")

        assert response.status_code == 403

    def test_user_can_list_own_transactions(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        own_transaction = PointService.earn_points(
            user,
            user.active_profile,
            10,
            "Bonus",
        )
        PointService.earn_points(
            other_user,
            other_user.active_profile,
            20,
            "Other bonus",
        )
        client = authenticated_client(user)

        response = client.get("/api/loyalty/points/transactions/")

        assert response.status_code == 200
        ids = {item["id"] for item in response_results(response)}
        assert ids == {own_transaction.pk}

    def test_user_cannot_filter_transactions_to_another_user(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PointService.earn_points(user, user.active_profile, 10, "Bonus")
        PointService.earn_points(
            other_user,
            other_user.active_profile,
            20,
            "Other bonus",
        )
        client = authenticated_client(user)

        response = client.get(
            f"/api/loyalty/points/transactions/?user_id={other_user.pk}"
        )

        assert response.status_code == 200
        assert response_results(response) == []

    def test_profile_filter_only_returns_current_users_profile_transactions(
        self,
    ):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        PointService.earn_points(user, user.active_profile, 10, "Bonus")
        PointService.earn_points(
            other_user,
            other_user.active_profile,
            20,
            "Other bonus",
        )
        client = authenticated_client(user)

        response = client.get(
            "/api/loyalty/points/transactions/"
            f"?profile_id={other_user.active_profile.pk}"
        )

        assert response.status_code == 200
        assert response_results(response) == []

    def test_manager_can_list_all_transactions_by_default(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        first_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        first = PointService.earn_points(
            first_user,
            first_user.active_profile,
            10,
            "Bonus",
        )
        second = PointService.earn_points(
            second_user,
            second_user.active_profile,
            20,
            "Bonus",
        )
        client = authenticated_client(manager)

        response = client.get("/api/loyalty/points/transactions/")

        assert response.status_code == 200
        ids = {item["id"] for item in response_results(response)}
        assert {first.pk, second.pk}.issubset(ids)

    def test_static_badge_catalog_is_listed_without_profile_filter(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(user)

        response = client.get("/api/loyalty/badges/")

        assert response.status_code == 200
        badge_types = {item["badge_type"] for item in response.data}
        assert badge_types == {
            BadgeType.FIRST_ORDER,
            BadgeType.POINTS_STARTER,
            BadgeType.SELLER_STARTER,
        }

    def test_active_profile_can_view_own_earned_badges(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        award = BadgeService.award_badge(
            user.active_profile,
            BadgeType.FIRST_ORDER,
        )
        client = authenticated_client(user)

        response = client.get("/api/loyalty/badges/my/")

        assert response.status_code == 200
        assert [item["id"] for item in response.data] == [award.pk]
        assert response.data[0]["badge_type"] == BadgeType.FIRST_ORDER

    def test_badge_endpoints_require_authentication(self, api_client):
        list_response = api_client.get("/api/loyalty/badges/")
        my_response = api_client.get("/api/loyalty/badges/my/")

        assert list_response.status_code == 401
        assert my_response.status_code == 401

    def test_non_manager_cannot_list_badges_by_profile_filter(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(user)

        response = client.get(
            f"/api/loyalty/badges/?profile_id={user.active_profile.pk}"
        )

        assert response.status_code == 403

    def test_manager_can_list_badge_awards_for_profile(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        award = BadgeService.award_badge(
            customer.active_profile,
            BadgeType.FIRST_ORDER,
        )
        BadgeAwardFactory()
        client = authenticated_client(manager)

        response = client.get(
            f"/api/loyalty/badges/?profile_id={customer.active_profile.pk}"
        )

        assert response.status_code == 200
        assert [item["id"] for item in response.data] == [award.pk]

    def test_switching_profile_changes_my_badges_result(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = user.active_profile
        seller_profile = SellerProfileFactory(user=user)
        customer_award = BadgeService.award_badge(
            customer_profile,
            BadgeType.FIRST_ORDER,
        )
        seller_award = BadgeService.award_badge(
            seller_profile,
            BadgeType.SELLER_STARTER,
        )
        client = authenticated_client(user)

        customer_response = client.get("/api/loyalty/badges/my/")
        switch_response = client.post(
            "/api/auth/switch-profile/",
            {"new_profile": ProfileType.SELLER},
            format="json",
        )
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {switch_response.data['access']}"
        )
        seller_response = client.get("/api/loyalty/badges/my/")

        assert customer_response.status_code == 200
        assert [item["id"] for item in customer_response.data] == [
            customer_award.pk
        ]
        assert seller_response.status_code == 200
        assert [item["id"] for item in seller_response.data] == [
            seller_award.pk
        ]

    def test_manager_can_award_badge(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(manager)

        response = client.post(
            "/api/loyalty/badges/",
            {
                "profile_id": customer.active_profile.pk,
                "badge_type": BadgeType.FIRST_ORDER,
                "source_type": "orders.Order",
                "source_id": "1",
                "metadata": {"order_id": 1},
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["profile_id"] == customer.active_profile.pk
        assert response.data["badge_type"] == BadgeType.FIRST_ORDER
        assert BadgeAward.objects.count() == 1

    def test_customer_cannot_award_badge(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)

        response = client.post(
            "/api/loyalty/badges/",
            {
                "profile_id": customer.active_profile.pk,
                "badge_type": BadgeType.FIRST_ORDER,
            },
            format="json",
        )

        assert response.status_code == 403

    def test_badge_award_unique_constraint(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        BadgeAwardFactory(
            profile=user.active_profile,
            badge_type=BadgeType.FIRST_ORDER,
        )

        with pytest.raises(IntegrityError):
            BadgeAwardFactory(
                profile=user.active_profile,
                badge_type=BadgeType.FIRST_ORDER,
            )
