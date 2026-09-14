from decimal import Decimal
import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.loyalty.constants import PointTransactionType
from apps.loyalty.models import PointTransaction, PointWallet
from apps.loyalty.services import PointService
from apps.users.constants import ProfileType
from tests.factories import (
    CustomerProfileFactory,
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


class TestSharedWalletOwnership:
    """Tests for BE-REW-01: Enforce Shared PP Coin Wallet Ownership."""

    def test_seller_earning_visible_to_buyer_mode(self):
        """TEST 1: Seller profile earns +50; after switching to Customer, balance is still 50."""
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_profile = user.active_profile
        customer_profile = CustomerProfileFactory(user=user, is_active=False)

        # Seller profile earns +50
        transaction = PointService.earn_points(
            user=user,
            profile=seller_profile,
            points=50,
            reason="Seller completed sale reward",
        )

        # Check account wallet balance = 50
        wallet = PointWallet.objects.get(user=user)
        assert wallet.balance == 50
        assert PointService.get_balance(user) == 50
        assert transaction.balance_after == 50

        # Switch active profile to CustomerProfile
        switched, profile_type = switch_user_profile(user, ProfileType.CUSTOMER)
        assert switched is True
        assert profile_type == ProfileType.CUSTOMER
        user.refresh_from_db()
        assert user.active_profile.pk == customer_profile.pk

        # Returned point balance is still 50
        assert PointService.get_balance(user) == 50

        # API verification: balance endpoint returns shared balance with new active_profile_id
        client = authenticated_client(user)
        response = client.get("/api/loyalty/points/")
        assert response.status_code == 200
        assert response.data["user_id"] == user.pk
        assert response.data["active_profile_id"] == customer_profile.pk
        assert response.data["balance"] == 50

    def test_buyer_spending_affects_same_wallet(self):
        """TEST 2: Seller earned +100; Customer mode spends 30 -> shared balance = 70."""
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_profile = user.active_profile
        customer_profile = CustomerProfileFactory(user=user, is_active=False)

        # Seller earned +100
        PointService.earn_points(
            user=user,
            profile=seller_profile,
            points=100,
            reason="Seller reward",
        )
        assert PointService.get_balance(user) == 100

        # Switch to Customer mode
        switch_user_profile(user, ProfileType.CUSTOMER)
        user.refresh_from_db()

        # Customer mode spends 30
        spend_tx = PointService.spend_points(
            user=user,
            profile=customer_profile,
            points=30,
            reason="Redeemed in customer mode",
        )

        # Shared balance is now 70
        assert PointService.get_balance(user) == 70
        wallet = PointWallet.objects.get(user=user)
        assert wallet.balance == 70
        assert spend_tx.balance_after == 70

        # Switch back to Seller mode: still 70
        switch_user_profile(user, ProfileType.SELLER)
        user.refresh_from_db()
        assert PointService.get_balance(user) == 70

    def test_single_wallet_per_user_account(self):
        """TEST 3: Creating/using both profiles results in exactly one PointWallet for UserAccount."""
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        CustomerProfileFactory.build()  # Ensure customer factory works
        seller_profile = SellerProfileFactory(user=user, is_active=False)

        # Before earning, wallet can be created
        wallet1 = PointService.get_or_create_wallet(user)
        assert PointWallet.objects.filter(user=user).count() == 1

        # Earn in customer mode
        PointService.earn_points(user, user.active_profile, 10, "Customer reward")
        # Earn in seller mode
        PointService.earn_points(user, seller_profile, 20, "Seller reward")

        # Get or create again
        wallet2 = PointService.get_or_create_wallet(user)

        assert wallet1.pk == wallet2.pk
        assert PointWallet.objects.filter(user=user).count() == 1
        assert PointWallet.objects.get(user=user).balance == 30

    def test_ledger_keeps_profile_attribution_while_user_remains_same_account(self):
        """TEST 4: Seller earning profile=Seller, Buyer spending profile=Customer, both user=same."""
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_profile = user.active_profile
        customer_profile = CustomerProfileFactory(user=user, is_active=False)

        earn_tx = PointService.earn_points(
            user=user,
            profile=seller_profile,
            points=100,
            reason="Seller earning",
        )

        spend_tx = PointService.spend_points(
            user=user,
            profile=customer_profile,
            points=40,
            reason="Buyer spending",
        )

        # Verify transaction profile attribution
        assert earn_tx.profile_id == seller_profile.pk
        assert spend_tx.profile_id == customer_profile.pk

        # Both transactions belong to the same user and same wallet
        assert earn_tx.user_id == user.pk
        assert spend_tx.user_id == user.pk
        assert earn_tx.wallet_id == spend_tx.wallet_id

        # Balances after are sequential
        assert earn_tx.balance_after == 100
        assert spend_tx.balance_after == 60

    def test_cross_account_profile_protection(self):
        """TEST 5: Calling PointService with user A and profile belonging to user B fails."""
        user_a = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        user_b = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        # Attempt to earn points for user_a with user_b's profile
        with pytest.raises(ValidationError, match="belong to the user"):
            PointService.earn_points(
                user=user_a,
                profile=user_b.active_profile,
                points=50,
                reason="Cross account attack",
            )

        # Attempt to spend points for user_a with user_b's profile
        PointService.earn_points(user=user_a, profile=user_a.active_profile, points=50, reason="Initial")
        with pytest.raises(ValidationError, match="belong to the user"):
            PointService.spend_points(
                user=user_a,
                profile=user_b.active_profile,
                points=20,
                reason="Cross account attack spend",
            )

        # Verify wallet balance of user_a unchanged by the failed spend
        assert PointService.get_balance(user_a) == 50
        # Verify no transaction was created for the invalid profile
        assert not PointTransaction.objects.filter(profile=user_b.active_profile, user=user_a).exists()

    def test_profile_switching_does_not_mutate_wallet(self):
        """TEST 6: Changing active_profile alone creates no wallet, no transactions, and no balance change."""
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = user.active_profile
        SellerProfileFactory(user=user, is_active=False)

        # Seed initial points
        PointService.earn_points(user, customer_profile, 80, "Seed points")
        assert PointWallet.objects.filter(user=user).count() == 1
        assert PointTransaction.objects.filter(user=user).count() == 1
        initial_balance = PointService.get_balance(user)

        # Switch to Seller
        switched, _ = switch_user_profile(user, ProfileType.SELLER)
        assert switched is True

        # Assert no new wallet was created
        assert PointWallet.objects.filter(user=user).count() == 1
        # Assert no new transaction was created
        assert PointTransaction.objects.filter(user=user).count() == 1
        # Assert balance is unchanged
        assert PointService.get_balance(user) == initial_balance

        # Switch back to Customer
        switched, _ = switch_user_profile(user, ProfileType.CUSTOMER)
        assert switched is True

        # Assert invariants still hold
        assert PointWallet.objects.filter(user=user).count() == 1
        assert PointTransaction.objects.filter(user=user).count() == 1
        assert PointService.get_balance(user) == initial_balance

    def test_idempotency_still_works(self):
        """TEST 7: Same earn operation with same idempotency key creates 1 tx and updates wallet once."""
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_profile = user.active_profile

        first = PointService.earn_points(
            user=user,
            profile=seller_profile,
            points=50,
            reason="Idempotent reward",
            idempotency_key="idemp-key-123",
        )

        second = PointService.earn_points(
            user=user,
            profile=seller_profile,
            points=50,
            reason="Idempotent reward",
            idempotency_key="idemp-key-123",
        )

        assert first.pk == second.pk
        assert PointTransaction.objects.filter(user=user).count() == 1
        assert PointService.get_balance(user) == 50

    def test_api_transaction_history_shows_all_profiles_for_user(self):
        """Transactions from both Seller and Customer modes appear in user's transaction history."""
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_profile = user.active_profile
        customer_profile = CustomerProfileFactory(user=user, is_active=False)

        tx_seller = PointService.earn_points(
            user=user,
            profile=seller_profile,
            points=100,
            reason="Earned as seller",
        )

        tx_buyer = PointService.spend_points(
            user=user,
            profile=customer_profile,
            points=30,
            reason="Spent as customer",
        )

        client = authenticated_client(user)
        response = client.get("/api/loyalty/points/transactions/")
        assert response.status_code == 200

        results = response_results(response)
        assert len(results) == 2
        result_ids = [item["id"] for item in results]
        assert tx_buyer.pk in result_ids
        assert tx_seller.pk in result_ids

        # Verify profile_id in serialized response
        tx_buyer_data = next(r for r in results if r["id"] == tx_buyer.pk)
        tx_seller_data = next(r for r in results if r["id"] == tx_seller.pk)
        assert tx_buyer_data["profile_id"] == customer_profile.pk
        assert tx_seller_data["profile_id"] == seller_profile.pk
        assert tx_buyer_data["user_id"] == user.pk
        assert tx_seller_data["user_id"] == user.pk
