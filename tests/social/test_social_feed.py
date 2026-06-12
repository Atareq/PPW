from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.assets.models import AssetStatus
from apps.auctions.models import Auction, AuctionStatus
from apps.authentication.services import build_token_for_user
from apps.social.models import SellerFollow
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    SellerProfileFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def set_created(instance, created):
    instance.__class__.objects.filter(pk=instance.pk).update(created=created)
    instance.refresh_from_db()
    return instance


def create_active_auction(owner, title, created):
    auction = AuctionFactory(
        owner=owner,
        title=title,
        status=AuctionStatus.ACTIVE,
        is_visible=True,
        start_time=timezone.now() - timedelta(hours=1),
        end_time=timezone.now() + timedelta(days=1),
    )
    return set_created(auction, created)


def create_market_item(owner, title, created, **overrides):
    item = SupplyFactory(owner=owner, title=title, **overrides)
    return set_created(item, created)


def result_ids(response):
    return [entry["item"]["id"] for entry in response.data["results"]]


def result_sources(response):
    return [entry["source"] for entry in response.data["results"]]


class TestSellerFollow:
    def test_customer_can_follow_seller(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(customer)

        response = client.post(
            f"/api/social/sellers/{seller.active_profile.pk}/follow/"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_active"] is True
        assert response.data["seller"]["id"] == seller.active_profile.pk
        assert SellerFollow.objects.filter(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
            is_active=True,
        ).exists()

    def test_customer_cannot_follow_same_seller_with_duplicate_row(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                SellerFollow.objects.create(
                    follower_id=customer.active_profile.pk,
                    seller_id=seller.active_profile.pk,
                )

    def test_follow_is_idempotent_and_reactivates_existing_follow(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(customer)

        first = client.post(
            f"/api/social/sellers/{seller.active_profile.pk}/follow/"
        )
        second = client.post(
            f"/api/social/sellers/{seller.active_profile.pk}/follow/"
        )
        SellerFollow.objects.filter(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        ).update(is_active=False)
        third = client.post(
            f"/api/social/sellers/{seller.active_profile.pk}/follow/"
        )

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert third.status_code == status.HTTP_200_OK
        assert first.data["id"] == second.data["id"] == third.data["id"]
        assert SellerFollow.objects.count() == 1
        assert SellerFollow.objects.get().is_active is True

    def test_unfollow_sets_inactive_and_repeated_unfollow_is_safe(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        follow = SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        )
        client = authenticated_client(customer)

        first = client.post(
            f"/api/social/sellers/{seller.active_profile.pk}/unfollow/"
        )
        second = client.post(
            f"/api/social/sellers/{seller.active_profile.pk}/unfollow/"
        )
        follow.refresh_from_db()

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert follow.is_active is False

    def test_customer_cannot_follow_own_seller_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_profile = SellerProfileFactory(user=user, is_active=False)
        client = authenticated_client(user)

        response = client.post(
            f"/api/social/sellers/{seller_profile.pk}/follow/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not SellerFollow.objects.exists()

    def test_customer_cannot_follow_inactive_seller(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        inactive_seller = SellerProfileFactory(is_active=False)
        client = authenticated_client(customer)

        response = client.post(
            f"/api/social/sellers/{inactive_seller.pk}/follow/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not SellerFollow.objects.exists()

    def test_follow_permissions_require_auth_and_customer_profile(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_client = authenticated_client(seller)

        unauthenticated = APIClient().post(
            f"/api/social/sellers/{seller.active_profile.pk}/follow/"
        )
        non_customer = seller_client.post(
            f"/api/social/sellers/{seller.active_profile.pk}/follow/"
        )

        assert unauthenticated.status_code == status.HTTP_401_UNAUTHORIZED
        assert non_customer.status_code == status.HTTP_403_FORBIDDEN

    def test_customer_sees_only_own_following_list(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        other_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        )
        SellerFollow.objects.create(
            follower_id=other_customer.active_profile.pk,
            seller_id=other_seller.active_profile.pk,
        )
        client = authenticated_client(customer)

        response = client.get("/api/social/sellers/following/")

        assert response.status_code == status.HTTP_200_OK
        assert [item["id"] for item in response.data] == [
            seller.active_profile.pk
        ]


class TestAuctionFeed:
    def test_followed_seller_auctions_appear_first_and_discovery_fills(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        followed_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        discovery_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=followed_seller.active_profile.pk,
        )
        followed = create_active_auction(
            followed_seller.active_profile,
            "Followed auction",
            now,
        )
        discovery = create_active_auction(
            discovery_seller.active_profile,
            "Discovery auction",
            now + timedelta(minutes=5),
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/auctions/", {"limit": 2})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response) == [followed.pk, discovery.pk]
        assert result_sources(response) == ["following", "discovery"]
        assert response.data["meta"] == {
            "following_count": 1,
            "discovery_count": 1,
            "fallback_used": True,
            "following_exhausted": True,
        }

    def test_no_followed_auctions_returns_discovery(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        auction = create_active_auction(
            seller.active_profile,
            "Discovery only",
            timezone.now(),
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/auctions/", {"limit": 1})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response) == [auction.pk]
        assert result_sources(response) == ["discovery"]

    def test_auction_feed_limit_invalid_cursor_and_next_cursor(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        newest = create_active_auction(
            seller.active_profile,
            "Newest",
            now + timedelta(minutes=2),
        )
        middle = create_active_auction(
            seller.active_profile,
            "Middle",
            now + timedelta(minutes=1),
        )
        oldest = create_active_auction(
            seller.active_profile,
            "Oldest",
            now,
        )
        client = authenticated_client(customer)

        first = client.get("/api/feed/auctions/", {"limit": 2})
        invalid = client.get(
            "/api/feed/auctions/",
            {"cursor": "not-a-valid-cursor"},
        )
        second = client.get(
            "/api/feed/auctions/",
            {"limit": 2, "cursor": first.data["next_cursor"]},
        )

        assert first.status_code == status.HTTP_200_OK
        assert result_ids(first) == [newest.pk, middle.pk]
        assert first.data["next_cursor"]
        assert first.data["has_more"] is True
        assert invalid.status_code == status.HTTP_400_BAD_REQUEST
        assert second.status_code == status.HTTP_200_OK
        assert result_ids(second) == [oldest.pk]
        assert not set(result_ids(first)) & set(result_ids(second))

    def test_auction_feed_excludes_inactive_seller_and_inactive_auctions(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        followed_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        inactive_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=inactive_seller.active_profile.pk,
        )
        inactive_seller_profile = inactive_seller.active_profile
        hidden = create_active_auction(
            inactive_seller_profile,
            "Inactive seller auction",
            timezone.now() + timedelta(minutes=1),
        )
        inactive_seller_profile.is_active = False
        inactive_seller_profile.save(update_fields=["is_active"])
        inactive = create_active_auction(
            followed_seller.active_profile,
            "Inactive auction",
            timezone.now() + timedelta(minutes=2),
        )
        inactive.status = AuctionStatus.CANCELLED
        inactive.save(update_fields=["status"])
        client = authenticated_client(customer)

        response = client.get("/api/feed/auctions/", {"limit": 5})

        assert response.status_code == status.HTTP_200_OK
        assert hidden.pk not in result_ids(response)
        assert inactive.pk not in result_ids(response)

    def test_non_customer_cannot_access_auction_feed(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(seller)

        response = client.get("/api/feed/auctions/")

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestMarketFeed:
    def test_followed_seller_market_items_appear_first_and_discovery_fills(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        followed_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        discovery_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=followed_seller.active_profile.pk,
        )
        followed = create_market_item(
            followed_seller.active_profile,
            "Followed market item",
            now,
        )
        discovery = create_market_item(
            discovery_seller.active_profile,
            "Discovery market item",
            now + timedelta(minutes=5),
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/market/", {"limit": 2})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response) == [followed.pk, discovery.pk]
        assert result_sources(response) == ["following", "discovery"]

    def test_no_followed_market_items_returns_discovery(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        item = create_market_item(
            seller.active_profile,
            "Discovery only",
            timezone.now(),
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/market/", {"limit": 1})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response) == [item.pk]
        assert result_sources(response) == ["discovery"]

    def test_market_feed_excludes_inactive_unlisted_and_sold_assets(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        visible = create_market_item(
            seller.active_profile,
            "Visible",
            timezone.now() + timedelta(minutes=3),
        )
        inactive = create_market_item(
            seller.active_profile,
            "Inactive",
            timezone.now() + timedelta(minutes=2),
            status=AssetStatus.INACTIVE,
        )
        sold = create_market_item(
            seller.active_profile,
            "Sold",
            timezone.now() + timedelta(minutes=1),
            status=AssetStatus.SOLD,
        )
        unlisted = create_market_item(
            seller.active_profile,
            "Unlisted",
            timezone.now(),
            is_market_listed=False,
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/market/", {"limit": 10})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response) == [visible.pk]
        assert inactive.pk not in result_ids(response)
        assert sold.pk not in result_ids(response)
        assert unlisted.pk not in result_ids(response)

    def test_market_feed_limit_cap_and_cursor_do_not_repeat_results(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        items = [
            create_market_item(
                seller.active_profile,
                f"Market {index}",
                now + timedelta(minutes=index),
            )
            for index in range(55)
        ]
        client = authenticated_client(customer)

        first = client.get("/api/feed/market/", {"limit": 100})
        second = client.get(
            "/api/feed/market/",
            {"limit": 100, "cursor": first.data["next_cursor"]},
        )

        assert first.status_code == status.HTTP_200_OK
        assert len(first.data["results"]) == 50
        assert first.data["has_more"] is True
        assert second.status_code == status.HTTP_200_OK
        assert len(second.data["results"]) == 5
        assert not set(result_ids(first)) & set(result_ids(second))
        assert items[-1].pk == result_ids(first)[0]

    def test_non_customer_cannot_access_market_feed(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(seller)

        response = client.get("/api/feed/market/")

        assert response.status_code == status.HTTP_403_FORBIDDEN
