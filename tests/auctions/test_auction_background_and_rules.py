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
from apps.packages.models import (
    PackageUsageLog,
    PackageUsageType,
    SellerPackageStatus,
)
from apps.users.constants import ProfileType
from tests.factories import (
    ActiveAssetSellerPackageFactory,
    AssetMediaFactory,
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    BirdFactory,
    PackageFactory,
    SellerProfileFactory,
    UserAccountFactory,
    create_active_auction_with_item,
)


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.mark.django_db
def test_auction_list_and_detail_include_fe_fields(seller_user, customer_user):
    auction = AuctionFactory(
        owner=seller_user.active_profile,
        buy_now_enabled=True,
        buy_now_price=Decimal("250.00"),
        min_bid_increment=Decimal("15.00"),
    )
    item = AuctionItemFactory(
        auction=auction,
        current_price=Decimal("150.00"),
    )
    AssetMediaFactory(asset=item.bird, media_url="https://example.com/bird.jpg")
    owner_client = authenticated_client(seller_user)
    customer_client = authenticated_client(customer_user)

    list_response = owner_client.get("/api/auctions/")
    detail_response = owner_client.get(f"/api/auctions/{auction.pk}/")
    customer_list_response = customer_client.get("/api/auctions/")
    list_item = next(
        item for item in list_response.data["results"] if item["id"] == auction.pk
    )
    customer_list_item = next(
        item
        for item in customer_list_response.data["results"]
        if item["id"] == auction.pk
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert detail_response.status_code == status.HTTP_200_OK
    assert list_item["buy_now_enabled"] is True
    assert list_item["buy_now_price"] == "250.00"
    assert list_item["min_bid_increment"] == "15.00"
    assert list_item["current_price"] == Decimal("150.00")
    assert list_item["thumbnail_url"] == "https://example.com/bird.jpg"
    assert list_item["is_owner"] is True
    assert detail_response.data["is_owner"] is True
    assert customer_list_item["is_owner"] is False


@pytest.mark.django_db
def test_auction_thumbnail_is_null_when_item_has_no_media(seller_user):
    auction = AuctionFactory(owner=seller_user.active_profile)
    AuctionItemFactory(auction=auction)
    client = authenticated_client(seller_user)

    response = client.get(f"/api/auctions/{auction.pk}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["thumbnail_url"] is None


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

    def test_manager_cannot_update_auction_without_catalog_action(self, manager_user):
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

        assert response.status_code == status.HTTP_403_FORBIDDEN

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

    def test_manager_cannot_destroy_auction_without_catalog_action(self, manager_user):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        auction = AuctionFactory(
            owner=seller.active_profile,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(days=1),
        )
        manager_client = self._auth_client(manager_user)

        response = manager_client.delete(f"/api/auctions/{auction.id}/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

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


@pytest.mark.django_db
class TestAuctionPackageConsumption:
    @staticmethod
    def _auth_client(user):
        client = APIClient()
        refresh = build_token_for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    @staticmethod
    def _payload(bird, **overrides):
        payload = {
            "title": "Package-backed auction",
            "description": "Consumes auction package points",
            "auction_type": AuctionType.SINGLE,
            "start_time": (timezone.now() + timedelta(minutes=10)).isoformat(),
            "end_time": (timezone.now() + timedelta(days=1)).isoformat(),
            "items": [
                {
                    "bird_id": bird.pk,
                    "starting_price": "100.00",
                }
            ],
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def _active_package(
        seller,
        remaining_points=50,
        auction_cost=10,
        **overrides,
    ):
        return ActiveAssetSellerPackageFactory(
            owner=seller.active_profile,
            package=PackageFactory(
                points=50,
                auction_cost=auction_cost,
                product_cost=5,
            ),
            remaining_points=remaining_points,
            **overrides,
        )

    def test_seller_auction_creation_consumes_points_and_links_package(
        self,
        authenticated_seller_client,
        seller_user,
    ):
        seller_package = self._active_package(
            seller_user,
            remaining_points=50,
            auction_cost=12,
        )
        bird = BirdFactory(owner=seller_user.active_profile)

        response = authenticated_seller_client.post(
            "/api/auctions/",
            self._payload(bird, seller_package_id=seller_package.pk),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        auction = Auction.objects.get(pk=response.data["id"])
        seller_package.refresh_from_db()
        assert auction.seller_package_id == seller_package.pk
        assert seller_package.remaining_points == 38
        log = PackageUsageLog.objects.get()
        assert log.usage_type == PackageUsageType.AUCTION_CREATED
        assert log.points_used == 12
        assert log.related_object_type == "auctions.Auction"
        assert log.related_object_id == auction.pk

    def test_seller_auction_creation_fails_without_active_package(
        self,
        authenticated_seller_client,
        seller_user,
    ):
        bird = BirdFactory(owner=seller_user.active_profile)

        response = authenticated_seller_client.post(
            "/api/auctions/",
            self._payload(bird),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "seller_package_id" in str(response.data)
        assert not Auction.objects.filter(
            title="Package-backed auction"
        ).exists()
        assert PackageUsageLog.objects.count() == 0

    def test_seller_auction_creation_fails_with_insufficient_points(
        self,
        authenticated_seller_client,
        seller_user,
    ):
        seller_package = self._active_package(
            seller_user,
            remaining_points=3,
            auction_cost=10,
        )
        bird = BirdFactory(owner=seller_user.active_profile)

        response = authenticated_seller_client.post(
            "/api/auctions/",
            self._payload(bird, seller_package_id=seller_package.pk),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Insufficient package points" in str(response.data)
        seller_package.refresh_from_db()
        assert seller_package.remaining_points == 3
        assert PackageUsageLog.objects.count() == 0
        assert not Auction.objects.filter(
            title="Package-backed auction"
        ).exists()

    def test_seller_auction_creation_rejects_another_sellers_package(
        self,
        authenticated_seller_client,
        seller_user,
        seller_user_2,
    ):
        seller_package = self._active_package(seller_user)
        other_package = self._active_package(seller_user_2)
        bird = BirdFactory(owner=seller_user.active_profile)

        response = authenticated_seller_client.post(
            "/api/auctions/",
            self._payload(bird, seller_package_id=other_package.pk),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not belong" in str(response.data)
        seller_package.refresh_from_db()
        other_package.refresh_from_db()
        assert seller_package.remaining_points == 50
        assert other_package.remaining_points == 50
        assert PackageUsageLog.objects.count() == 0
        assert not Auction.objects.filter(
            title="Package-backed auction"
        ).exists()

    @pytest.mark.parametrize(
        "package_overrides,expected_error",
        [
            (
                {"status": SellerPackageStatus.PENDING, "activated": False},
                "Seller package is not active",
            ),
            ({"expires_at": timezone.now() - timedelta(days=1)}, "expired"),
        ],
    )
    def test_seller_auction_creation_rejects_unusable_selected_package(
        self,
        package_overrides,
        expected_error,
        authenticated_seller_client,
        seller_user,
    ):
        seller_package = self._active_package(seller_user, **package_overrides)
        bird = BirdFactory(owner=seller_user.active_profile)

        response = authenticated_seller_client.post(
            "/api/auctions/",
            self._payload(bird, seller_package_id=seller_package.pk),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert expected_error in str(response.data)
        seller_package.refresh_from_db()
        assert PackageUsageLog.objects.count() == 0
        assert not Auction.objects.filter(
            title="Package-backed auction"
        ).exists()

    def test_failed_auction_validation_does_not_consume_points(
        self,
        authenticated_seller_client,
        seller_user,
    ):
        seller_package = self._active_package(seller_user)
        first_bird = BirdFactory(owner=seller_user.active_profile)
        second_bird = BirdFactory(owner=seller_user.active_profile)
        payload = self._payload(
            first_bird,
            seller_package_id=seller_package.pk,
            items=[
                {"bird_id": first_bird.pk, "starting_price": "100.00"},
                {"bird_id": second_bird.pk, "starting_price": "100.00"},
            ],
        )

        response = authenticated_seller_client.post(
            "/api/auctions/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        seller_package.refresh_from_db()
        assert seller_package.remaining_points == 50
        assert PackageUsageLog.objects.count() == 0

    def test_seller_auction_creation_rejects_unowned_bird_before_consumption(
        self,
        authenticated_seller_client,
        seller_user,
        seller_user_2,
    ):
        seller_package = self._active_package(seller_user)
        other_seller_bird = BirdFactory(owner=seller_user_2.active_profile)

        response = authenticated_seller_client.post(
            "/api/auctions/",
            self._payload(other_seller_bird, seller_package_id=seller_package.pk),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Sellers can auction only birds they own" in str(response.data)
        seller_package.refresh_from_db()
        assert seller_package.remaining_points == 50
        assert PackageUsageLog.objects.count() == 0
        assert not Auction.objects.filter(
            title="Package-backed auction"
        ).exists()

    def test_auction_update_cancel_and_close_do_not_consume_points(
        self,
        authenticated_seller_client,
        seller_user,
        manager_user,
    ):
        seller_package = self._active_package(seller_user)
        bird = BirdFactory(owner=seller_user.active_profile)
        create_response = authenticated_seller_client.post(
            "/api/auctions/",
            self._payload(bird, seller_package_id=seller_package.pk),
            format="json",
        )
        auction_id = create_response.data["id"]
        seller_package.refresh_from_db()
        remaining_after_create = seller_package.remaining_points
        log_count_after_create = PackageUsageLog.objects.count()

        update_response = authenticated_seller_client.patch(
            f"/api/auctions/{auction_id}/",
            {"title": "Updated without consumption"},
            format="json",
        )
        cancel_response = authenticated_seller_client.post(
            f"/api/auctions/{auction_id}/cancel/",
            {},
            format="json",
        )
        expired_auction = AuctionFactory(
            owner=seller_user.active_profile,
            seller_package=seller_package,
            status=AuctionStatus.ACTIVE,
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() - timedelta(minutes=1),
        )
        AuctionItemFactory(
            auction=expired_auction,
            bird=BirdFactory(owner=seller_user.active_profile),
        )
        manager_client = self._auth_client(manager_user)
        close_response = manager_client.post(
            f"/api/auctions/{expired_auction.pk}/close/",
            {},
            format="json",
        )

        seller_package.refresh_from_db()
        assert update_response.status_code == status.HTTP_200_OK
        assert cancel_response.status_code == status.HTTP_200_OK
        assert close_response.status_code == status.HTTP_200_OK
        assert seller_package.remaining_points == remaining_after_create
        assert PackageUsageLog.objects.count() == log_count_after_create

    def test_manager_cannot_create_auction_without_catalog_action(
        self,
        manager_user,
        seller_user,
    ):
        manager_client = self._auth_client(manager_user)
        bird = BirdFactory(owner=seller_user.active_profile)

        response = manager_client.post(
            "/api/auctions/",
            self._payload(bird),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Auction.objects.count() == 0
        assert PackageUsageLog.objects.count() == 0
