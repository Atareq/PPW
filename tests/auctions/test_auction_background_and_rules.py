from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.auctions.models import (
    Auction,
    AuctionEvent,
    AuctionStatus,
    AuctionType,
)
from apps.auctions.services import (
    AuctionBusinessValidator,
    AuctionService,
    BidBusinessValidator,
    BiddingService,
)
from apps.auctions.tasks import close_expired_auctions
from apps.authentication.services import build_token_for_user
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    BirdFactory,
    SellerProfileFactory,
    UserAccountFactory,
    create_active_auction_with_item,
)


@pytest.mark.django_db
class TestAuctionAutoClose:
    def test_close_expired_auctions_closes_and_settles_items(self):
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() - timedelta(minutes=1),
        )
        item = AuctionItemFactory(
            auction=auction,
            bird=BirdFactory(owner=seller_profile),
        )
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        bid = BidFactory(
            item=item,
            bidder=customer.active_profile,
            amount=Decimal("150.00"),
        )
        item.current_price = bid.amount
        item.save(update_fields=["current_price"])

        closed_count = AuctionService.close_expired_auctions(
            now=timezone.now()
        )

        auction.refresh_from_db()
        item.refresh_from_db()

        assert closed_count == 1
        assert auction.status == AuctionStatus.CLOSED
        assert item.status == "sold"
        assert item.winner_id == customer.active_profile.id
        assert item.winner_bid_id == bid.id
        assert (
            AuctionEvent.objects.filter(
                auction=auction,
                event_type="auction_ended",
            ).count()
            == 1
        )

    def test_close_auction_is_idempotent(self):
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() - timedelta(minutes=1),
        )
        AuctionItemFactory(
            auction=auction,
            bird=BirdFactory(owner=seller_profile),
        )

        AuctionService.close_auction(auction, now=timezone.now())
        AuctionService.close_auction(auction, now=timezone.now())

        auction.refresh_from_db()
        assert auction.status == AuctionStatus.CLOSED
        assert (
            AuctionEvent.objects.filter(
                auction=auction,
                event_type="auction_ended",
            ).count()
            == 1
        )

    def test_celery_task_delegates_to_service_layer(self):
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() - timedelta(minutes=1),
        )
        AuctionItemFactory(
            auction=auction,
            bird=BirdFactory(owner=seller_profile),
        )

        closed_count = close_expired_auctions()

        auction.refresh_from_db()
        assert closed_count == 1
        assert auction.status == AuctionStatus.CLOSED


@pytest.mark.django_db
class TestAuctionRules:
    def test_pair_gender_validation_accepts_male_and_female(self):
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        male = BirdFactory(owner=seller_profile, gender="male")
        female = BirdFactory(owner=seller_profile, gender="female")

        auction = Auction(
            owner=seller_profile,
            title="Pair Auction",
            description="Pair birds",
            auction_type=AuctionType.PAIR,
            start_time=timezone.now() + timedelta(minutes=10),
            end_time=timezone.now() + timedelta(days=1),
        )
        items_data = [
            {
                "bird_id": male.id,
                "paired_bird_id": female.id,
                "starting_price": Decimal("100.00"),
            }
        ]

        validator = AuctionBusinessValidator(
            auction=auction,
            items_data=items_data,
        )

        validator.validate()

    def test_duplicate_active_listing_is_rejected(self):
        seller_profile = SellerProfileFactory(type=ProfileType.SELLER)
        bird = BirdFactory(owner=seller_profile)
        existing_auction = AuctionFactory(
            owner=seller_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() - timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )
        AuctionItemFactory(auction=existing_auction, bird=bird)

        auction = Auction(
            owner=seller_profile,
            title="Duplicate Auction",
            description="Test duplicate listing",
            auction_type=AuctionType.SINGLE,
            start_time=timezone.now() + timedelta(minutes=10),
            end_time=timezone.now() + timedelta(days=2),
        )

        validator = AuctionBusinessValidator(
            auction=auction,
            items_data=[
                {
                    "bird_id": bird.id,
                    "starting_price": Decimal("100.00"),
                }
            ],
        )

        with pytest.raises(ValidationError, match="active auction"):
            validator.validate()

    def test_bid_is_revalidated_under_lock(self):
        auction, item, seller = create_active_auction_with_item()
        auction.min_bid_increment = Decimal("10.00")
        auction.save(update_fields=["min_bid_increment"])

        bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        BidBusinessValidator(
            item=item,
            bidder=bidder.active_profile,
            amount=Decimal("155.00"),
        ).validate()

        other_bidder = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        existing_bid = BidFactory(
            item=item,
            bidder=other_bidder.active_profile,
            amount=Decimal("150.00"),
        )
        item.current_price = existing_bid.amount
        item.save(update_fields=["current_price"])

        with pytest.raises(ValidationError, match="at least 160.00"):
            BiddingService.place_bid(
                item=item,
                bidder=bidder.active_profile,
                amount=Decimal("155.00"),
            )


@pytest.mark.django_db
class TestAuctionUpdateLocking:
    def test_started_auction_cannot_be_updated(
        self, authenticated_seller_client, seller_user
    ):
        auction = AuctionFactory(
            owner=seller_user.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() - timedelta(minutes=5),
            end_time=timezone.now() + timedelta(days=1),
        )

        response = authenticated_seller_client.patch(
            f"/api/auctions/{auction.id}/",
            {"title": "Updated"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestAuctionOwnershipPermissions:
    @staticmethod
    def _auth_client(user):
        client = APIClient()
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    def test_owner_can_update_own_auction(
        self, authenticated_seller_client, seller_user
    ):
        auction = AuctionFactory(
            owner=seller_user.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )

        response = authenticated_seller_client.patch(
            f"/api/auctions/{auction.id}/",
            {"title": "Owner Updated"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_manager_can_update_any_auction(self, manager_user):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )
        manager_client = self._auth_client(manager_user)

        response = manager_client.patch(
            f"/api/auctions/{auction.id}/",
            {"title": "Manager Updated"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_non_owner_seller_cannot_update_other_auction(
        self, seller_user, authenticated_seller_client_2
    ):
        auction = AuctionFactory(
            owner=seller_user.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )

        response = authenticated_seller_client_2.patch(
            f"/api/auctions/{auction.id}/",
            {"title": "Blocked Update"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_customer_cannot_update_auction(
        self, authenticated_customer_client
    ):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )

        response = authenticated_customer_client.patch(
            f"/api/auctions/{auction.id}/",
            {"title": "Blocked Update"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_can_destroy_own_auction(
        self, authenticated_seller_client, seller_user
    ):
        auction = AuctionFactory(
            owner=seller_user.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )

        response = authenticated_seller_client.delete(
            f"/api/auctions/{auction.id}/"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_manager_can_destroy_any_auction(self, manager_user):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )
        manager_client = self._auth_client(manager_user)

        response = manager_client.delete(f"/api/auctions/{auction.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_non_owner_seller_cannot_destroy_other_auction(
        self, seller_user, authenticated_seller_client_2
    ):
        auction = AuctionFactory(
            owner=seller_user.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )

        response = authenticated_seller_client_2.delete(
            f"/api/auctions/{auction.id}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_customer_cannot_destroy_auction(
        self, authenticated_customer_client
    ):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )

        response = authenticated_customer_client.delete(
            f"/api/auctions/{auction.id}/"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_auction_with_bid_cannot_be_updated(
        self, authenticated_seller_client, seller_user
    ):
        auction = AuctionFactory(
            owner=seller_user.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )
        item = AuctionItemFactory(
            auction=auction,
            bird=BirdFactory(owner=seller_user.active_profile),
        )
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        BidFactory(
            item=item,
            bidder=customer.active_profile,
            amount=Decimal("150.00"),
        )

        response = authenticated_seller_client.patch(
            f"/api/auctions/{auction.id}/",
            {"title": "Updated"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
