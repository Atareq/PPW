from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.assets.models import AssetStatus
from apps.auctions.models import Auction, AuctionStatus
from apps.authentication.services import build_token_for_user
from apps.feed.models import (
    SellerCustomerBlock,
    SellerFollow,
    SellerPackageFollow,
)
from apps.feed.services import (
    BlockService,
    SellerFollowService,
    SellerPackageFollowService,
)
from apps.packages.models import SellerPackageStatus
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    ActiveAssetSellerPackageFactory,
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


def create_active_auction(owner, title, created, **overrides):
    auction = AuctionFactory(
        owner=owner,
        title=title,
        status=AuctionStatus.ACTIVE,
        is_visible=True,
        start_time=timezone.now() - timedelta(hours=1),
        end_time=timezone.now() + timedelta(days=1),
        **overrides,
    )
    return set_created(auction, created)


def create_market_item(owner, title, created, **overrides):
    item = SupplyFactory(owner=owner, title=title, **overrides)
    return set_created(item, created)


def result_ids(response):
    return [entry["item"]["id"] for entry in response.data["results"]]


def result_sources(response):
    return [entry["source"] for entry in response.data["results"]]


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


class TestSellerCustomerBlockService:
    def test_customer_can_block_seller(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        block = BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
            reason="spam",
        )

        assert block.customer_id == customer.active_profile.pk
        assert block.seller_id == seller.active_profile.pk
        assert block.blocked_by_id == customer.active_profile.pk
        assert block.reason == "spam"
        assert block.is_active is True

    def test_seller_can_block_customer(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        block = BlockService.block_customer_seller(
            seller.active_profile,
            customer.active_profile,
        )

        assert block.customer_id == customer.active_profile.pk
        assert block.seller_id == seller.active_profile.pk
        assert block.blocked_by_id == seller.active_profile.pk

    def test_customer_cannot_block_customer(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        with pytest.raises(ValidationError):
            BlockService.block_customer_seller(
                customer.active_profile,
                other_customer.active_profile,
            )

    def test_seller_cannot_block_seller(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        other_seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        with pytest.raises(ValidationError):
            BlockService.block_customer_seller(
                seller.active_profile,
                other_seller.active_profile,
            )

    def test_manager_block_flow_is_unsupported(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        with pytest.raises(PermissionDenied):
            BlockService.block_customer_seller(
                manager.active_profile,
                seller.active_profile,
            )

    def test_user_cannot_block_own_seller_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_profile = SellerProfileFactory(user=user, is_active=False)

        with pytest.raises(ValidationError):
            BlockService.block_customer_seller(
                user.active_profile,
                seller_profile,
            )

    def test_duplicate_block_returns_and_reactivates_existing_block(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        first = BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
            reason="first",
        )
        duplicate = BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
            reason="ignored",
        )
        first.is_active = False
        first.save(update_fields=["is_active", "modified"])

        reactivated = BlockService.block_customer_seller(
            seller.active_profile,
            customer.active_profile,
            reason="again",
        )

        assert duplicate.pk == first.pk
        assert duplicate.reason == "first"
        assert reactivated.pk == first.pk
        assert reactivated.is_active is True
        assert reactivated.blocked_by_id == seller.active_profile.pk
        assert reactivated.reason == "again"
        assert SellerCustomerBlock.objects.count() == 1

    def test_unblock_sets_inactive_and_only_blocked_by_can_unblock(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        block = BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
        )

        with pytest.raises(PermissionDenied):
            BlockService.unblock_customer_seller(
                seller.active_profile,
                customer.active_profile,
            )

        unblocked = BlockService.unblock_customer_seller(
            customer.active_profile,
            seller.active_profile,
        )
        block.refresh_from_db()

        assert unblocked.pk == block.pk
        assert block.is_active is False

    def test_block_removes_follow_and_prevents_refollow(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        follow = SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        )

        BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
        )
        follow.refresh_from_db()

        assert follow.is_active is False
        with pytest.raises(ValidationError):
            SellerFollowService.follow(
                customer.active_profile,
                seller.active_profile.pk,
            )


class TestSellerCustomerBlockApi:
    def test_unauthenticated_block_request_is_denied(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)

        response = APIClient().post(
            "/api/feed/blocks/customer-seller/",
            {"profile_id": seller.active_profile.pk},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_customer_can_block_seller_via_api(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(customer)

        response = client.post(
            "/api/feed/blocks/customer-seller/",
            {"profile_id": seller.active_profile.pk, "reason": "spam"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["seller"]["id"] == seller.active_profile.pk
        assert response.data["customer"]["id"] == customer.active_profile.pk
        assert response.data["blocked_by"]["id"] == customer.active_profile.pk
        assert response.data["other_participant"]["id"] == seller.active_profile.pk
        assert response.data["reason"] == "spam"
        assert "phone_number" not in response.data["seller"]
        assert "email" not in response.data["seller"]
        assert "balance" not in response.data["seller"]
        assert "user" not in response.data["seller"]

    def test_seller_can_block_customer_via_api(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(seller)

        response = client.post(
            "/api/feed/blocks/customer-seller/",
            {"profile_id": customer.active_profile.pk},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["customer"]["id"] == customer.active_profile.pk
        assert response.data["seller"]["id"] == seller.active_profile.pk
        assert response.data["blocked_by"]["id"] == seller.active_profile.pk

    def test_list_returns_active_blocks_only(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        active_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        inactive_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        active = BlockService.block_customer_seller(
            customer.active_profile,
            active_seller.active_profile,
        )
        inactive = BlockService.block_customer_seller(
            customer.active_profile,
            inactive_seller.active_profile,
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active", "modified"])
        client = authenticated_client(customer)

        response = client.get("/api/feed/blocks/customer-seller/")

        assert response.status_code == status.HTTP_200_OK
        assert [item["id"] for item in response.data] == [active.pk]

    def test_blocked_by_can_delete_block_via_api(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        block = BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
        )
        client = authenticated_client(customer)

        response = client.delete(
            f"/api/feed/blocks/customer-seller/{seller.active_profile.pk}/"
        )
        block.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "profile_id": seller.active_profile.pk,
            "is_active": False,
        }
        assert block.is_active is False

    def test_other_side_cannot_delete_block_via_api(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        block = BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
        )
        client = authenticated_client(seller)

        response = client.delete(
            f"/api/feed/blocks/customer-seller/{customer.active_profile.pk}/"
        )
        block.refresh_from_db()

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert block.is_active is True


class TestSellerFollow:
    def test_customer_can_follow_seller(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = authenticated_client(customer)

        response = client.post(f"/api/feed/sellers/{seller.active_profile.pk}/follow/")

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

        first = client.post(f"/api/feed/sellers/{seller.active_profile.pk}/follow/")
        second = client.post(f"/api/feed/sellers/{seller.active_profile.pk}/follow/")
        SellerFollow.objects.filter(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        ).update(is_active=False)
        third = client.post(f"/api/feed/sellers/{seller.active_profile.pk}/follow/")

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

        first = client.post(f"/api/feed/sellers/{seller.active_profile.pk}/unfollow/")
        second = client.post(f"/api/feed/sellers/{seller.active_profile.pk}/unfollow/")
        follow.refresh_from_db()

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert follow.is_active is False

    def test_customer_cannot_follow_own_seller_profile(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_profile = SellerProfileFactory(user=user, is_active=False)
        client = authenticated_client(user)

        response = client.post(f"/api/feed/sellers/{seller_profile.pk}/follow/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not SellerFollow.objects.exists()

    def test_customer_cannot_follow_inactive_seller(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        inactive_seller = SellerProfileFactory(is_active=False)
        client = authenticated_client(customer)

        response = client.post(f"/api/feed/sellers/{inactive_seller.pk}/follow/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not SellerFollow.objects.exists()

    def test_follow_permissions_require_auth_and_customer_profile(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_client = authenticated_client(seller)

        unauthenticated = APIClient().post(
            f"/api/feed/sellers/{seller.active_profile.pk}/follow/"
        )
        non_customer = seller_client.post(
            f"/api/feed/sellers/{seller.active_profile.pk}/follow/"
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

        response = client.get("/api/feed/sellers/following/")

        assert response.status_code == status.HTTP_200_OK
        assert [item["id"] for item in response.data] == [seller.active_profile.pk]


class TestSellerPackageFollow:
    def test_customer_can_follow_unfollow_and_list_public_active_package(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=seller.active_profile,
            display_name="Sprint room",
        )
        client = authenticated_client(customer)

        follow_response = client.post(
            f"/api/feed/seller-packages/{seller_package.pk}/follow/"
        )
        list_response = client.get("/api/feed/seller-packages/following/")
        unfollow_response = client.post(
            f"/api/feed/seller-packages/{seller_package.pk}/unfollow/"
        )
        follow = SellerPackageFollow.objects.get(
            follower_id=customer.active_profile.pk,
            seller_package_id=seller_package.pk,
        )

        assert follow_response.status_code == status.HTTP_200_OK
        assert follow_response.data["is_active"] is True
        assert follow_response.data["seller_package"]["id"] == seller_package.pk
        assert list_response.status_code == status.HTTP_200_OK
        assert [item["seller_package"]["id"] for item in list_response.data] == [
            seller_package.pk
        ]
        assert unfollow_response.status_code == status.HTTP_200_OK
        assert unfollow_response.data == {
            "seller_package_id": seller_package.pk,
            "is_active": False,
        }
        follow.refresh_from_db()
        assert follow.is_active is False

    def test_customer_cannot_follow_own_seller_package(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_profile = SellerProfileFactory(user=user, is_active=False)
        seller_package = ActiveAssetSellerPackageFactory(owner=seller_profile)
        client = authenticated_client(user)

        response = client.post(
            f"/api/feed/seller-packages/{seller_package.pk}/follow/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not SellerPackageFollow.objects.exists()

    @pytest.mark.parametrize(
        "package_overrides",
        [
            {"is_public": False},
            {"status": SellerPackageStatus.PENDING, "activated": False},
            {"expires_at": timezone.now() - timedelta(days=1)},
        ],
    )
    def test_customer_cannot_follow_unfollowable_package(
        self,
        package_overrides,
    ):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=seller.active_profile,
            **package_overrides,
        )
        client = authenticated_client(customer)

        response = client.post(
            f"/api/feed/seller-packages/{seller_package.pk}/follow/"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not SellerPackageFollow.objects.exists()

    def test_customer_cannot_follow_blocked_seller_package(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(owner=seller.active_profile)
        BlockService.block_customer_seller(
            customer.active_profile,
            seller.active_profile,
        )

        with pytest.raises(ValidationError):
            SellerPackageFollowService.follow(
                customer.active_profile,
                seller_package.pk,
            )


class TestPeopleYouMayKnow:
    def test_customer_gets_public_safe_seller_suggestions(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory(is_active=True)
        UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        UserAccountFactory(with_profile=ProfileType.MANAGER)
        client = authenticated_client(customer)

        response = client.get("/api/feed/people-you-may-know/")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0] == {
            "id": seller.pk,
            "nickname": seller.nickname,
            "country": seller.country,
            "currency": seller.currency,
            "avg_rating": "0.00",
            "ratings_count": 0,
            "avatar_url": "",
            "is_followed": False,
            "completed_orders_count": 0,
            "seller_level": "basic",
            "seller_level_label": "Basic",
        }
        assert "user" not in response.data[0]
        assert "balance" not in response.data[0]
        assert "recoded_voice" not in response.data[0]
        assert "phone_number" not in response.data[0]
        assert "email" not in response.data[0]

    def test_suggestions_exclude_followed_own_inactive_and_deleted_sellers(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        suggested = UserAccountFactory(with_profile=ProfileType.SELLER)
        followed = UserAccountFactory(with_profile=ProfileType.SELLER)
        inactive = UserAccountFactory(with_profile=ProfileType.SELLER)
        deleted = UserAccountFactory(with_profile=ProfileType.SELLER)
        own_seller = SellerProfileFactory(user=customer, is_active=False)
        inactive_profile = inactive.active_profile
        inactive_profile.is_active = False
        inactive_profile.save(update_fields=["is_active"])
        deleted_profile_id = deleted.active_profile.pk
        deleted.active_profile.delete()
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=followed.active_profile.pk,
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/people-you-may-know/", {"limit": 10})
        suggestion_ids = {item["id"] for item in response.data}

        assert response.status_code == status.HTTP_200_OK
        assert suggestion_ids == {suggested.active_profile.pk}
        assert followed.active_profile.pk not in suggestion_ids
        assert inactive_profile.pk not in suggestion_ids
        assert deleted_profile_id not in suggestion_ids
        assert own_seller.pk not in suggestion_ids

    def test_suggestions_exclude_blocked_sellers_before_random_ordering(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        visible = UserAccountFactory(with_profile=ProfileType.SELLER)
        blocked = UserAccountFactory(with_profile=ProfileType.SELLER)
        BlockService.block_customer_seller(
            customer.active_profile,
            blocked.active_profile,
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/people-you-may-know/", {"limit": 10})
        suggestion_ids = {item["id"] for item in response.data}

        assert response.status_code == status.HTTP_200_OK
        assert visible.active_profile.pk in suggestion_ids
        assert blocked.active_profile.pk not in suggestion_ids

    def test_suggestions_limit_defaults_and_caps_at_fifty(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        sellers = [
            UserAccountFactory(with_profile=ProfileType.SELLER) for _ in range(55)
        ]
        client = authenticated_client(customer)

        default_response = client.get("/api/feed/people-you-may-know/")
        capped_response = client.get(
            "/api/feed/people-you-may-know/",
            {"limit": 100},
        )

        assert default_response.status_code == status.HTTP_200_OK
        assert len(default_response.data) == 10
        assert capped_response.status_code == status.HTTP_200_OK
        assert len(capped_response.data) == 50
        assert {item["id"] for item in capped_response.data}.issubset(
            {seller.active_profile.pk for seller in sellers}
        )

    def test_suggestions_auth_and_customer_permissions(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_client = authenticated_client(seller)

        unauthenticated = APIClient().get("/api/feed/people-you-may-know/")
        non_customer = seller_client.get("/api/feed/people-you-may-know/")

        assert unauthenticated.status_code == status.HTTP_401_UNAUTHORIZED
        assert non_customer.status_code == status.HTTP_403_FORBIDDEN

    def test_old_social_routes_are_removed(self):
        response = APIClient().get("/api/social/sellers/following/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestSellerDiscovery:
    def test_seller_list_endpoint_returns_public_safe_sellers(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        own_seller = SellerProfileFactory(user=customer, is_active=False)
        inactive = UserAccountFactory(with_profile=ProfileType.SELLER)
        inactive_profile = inactive.active_profile
        inactive_profile.is_active = False
        inactive_profile.save(update_fields=["is_active"])
        client = authenticated_client(customer)

        response = client.get("/api/feed/sellers/")
        results = response_results(response)
        ids = {item["id"] for item in results}
        seller_item = next(item for item in results if item["id"] == seller.active_profile.pk)

        assert response.status_code == status.HTTP_200_OK
        assert seller.active_profile.pk in ids
        assert own_seller.pk not in ids
        assert inactive_profile.pk not in ids
        assert seller_item["user_id"] == seller.pk
        assert seller_item["description"] == ""
        assert seller_item["avatar_url"] == ""

    def test_seller_list_excludes_blocked_sellers(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        visible = UserAccountFactory(with_profile=ProfileType.SELLER)
        blocked = UserAccountFactory(with_profile=ProfileType.SELLER)
        BlockService.block_customer_seller(
            customer.active_profile,
            blocked.active_profile,
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/sellers/")
        ids = {item["id"] for item in response_results(response)}

        assert response.status_code == status.HTTP_200_OK
        assert visible.active_profile.pk in ids
        assert blocked.active_profile.pk not in ids


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

    def test_followed_package_auctions_appear_in_following_feed(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        package_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        discovery_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=package_seller.active_profile
        )
        SellerPackageFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_package_id=seller_package.pk,
        )
        followed = create_active_auction(
            package_seller.active_profile,
            "Package followed auction",
            now,
            seller_package=seller_package,
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
        assert result_sources(response) == ["package_following", "discovery"]

    def test_auction_feed_deduplicates_seller_and_package_follow(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(owner=seller.active_profile)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        )
        SellerPackageFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_package_id=seller_package.pk,
        )
        auction = create_active_auction(
            seller.active_profile,
            "Followed twice auction",
            timezone.now(),
            seller_package=seller_package,
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/auctions/", {"limit": 10})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response).count(auction.pk) == 1
        result = next(
            item for item in response.data["results"] if item["item"]["id"] == auction.pk
        )
        assert result["source"] == "following"

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

    def test_auction_feed_excludes_blocked_following_and_discovery_sellers(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        visible_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        blocked_followed_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        blocked_discovery_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=visible_seller.active_profile.pk,
        )
        stale_follow = SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=blocked_followed_seller.active_profile.pk,
        )
        visible = create_active_auction(
            visible_seller.active_profile,
            "Visible followed",
            now + timedelta(minutes=3),
        )
        blocked_followed = create_active_auction(
            blocked_followed_seller.active_profile,
            "Blocked followed",
            now + timedelta(minutes=2),
        )
        blocked_discovery = create_active_auction(
            blocked_discovery_seller.active_profile,
            "Blocked discovery",
            now + timedelta(minutes=1),
        )
        BlockService.block_customer_seller(
            customer.active_profile,
            blocked_followed_seller.active_profile,
        )
        BlockService.block_customer_seller(
            blocked_discovery_seller.active_profile,
            customer.active_profile,
        )
        SellerFollow.objects.filter(pk=stale_follow.pk).update(is_active=True)
        client = authenticated_client(customer)

        response = client.get("/api/feed/auctions/", {"limit": 10})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response) == [visible.pk]
        assert blocked_followed.pk not in result_ids(response)
        assert blocked_discovery.pk not in result_ids(response)

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

    def test_followed_package_market_items_appear_in_following_feed(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        package_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        discovery_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=package_seller.active_profile
        )
        SellerPackageFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_package_id=seller_package.pk,
        )
        followed = create_market_item(
            package_seller.active_profile,
            "Package followed market item",
            now,
            seller_package=seller_package,
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
        assert result_sources(response) == ["package_following", "discovery"]

    def test_market_feed_deduplicates_seller_and_package_follow(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(owner=seller.active_profile)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=seller.active_profile.pk,
        )
        SellerPackageFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_package_id=seller_package.pk,
        )
        item = create_market_item(
            seller.active_profile,
            "Followed twice market item",
            timezone.now(),
            seller_package=seller_package,
        )
        client = authenticated_client(customer)

        response = client.get("/api/feed/market/", {"limit": 10})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response).count(item.pk) == 1
        result = next(
            entry for entry in response.data["results"] if entry["item"]["id"] == item.pk
        )
        assert result["source"] == "following"

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

    def test_market_feed_excludes_blocked_following_and_discovery_sellers(self):
        now = timezone.now()
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        visible_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        blocked_followed_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        blocked_discovery_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=visible_seller.active_profile.pk,
        )
        stale_follow = SellerFollow.objects.create(
            follower_id=customer.active_profile.pk,
            seller_id=blocked_followed_seller.active_profile.pk,
        )
        visible = create_market_item(
            visible_seller.active_profile,
            "Visible followed",
            now + timedelta(minutes=3),
        )
        blocked_followed = create_market_item(
            blocked_followed_seller.active_profile,
            "Blocked followed",
            now + timedelta(minutes=2),
        )
        blocked_discovery = create_market_item(
            blocked_discovery_seller.active_profile,
            "Blocked discovery",
            now + timedelta(minutes=1),
        )
        BlockService.block_customer_seller(
            customer.active_profile,
            blocked_followed_seller.active_profile,
        )
        BlockService.block_customer_seller(
            blocked_discovery_seller.active_profile,
            customer.active_profile,
        )
        SellerFollow.objects.filter(pk=stale_follow.pk).update(is_active=True)
        client = authenticated_client(customer)

        response = client.get("/api/feed/market/", {"limit": 10})

        assert response.status_code == status.HTTP_200_OK
        assert result_ids(response) == [visible.pk]
        assert blocked_followed.pk not in result_ids(response)
        assert blocked_discovery.pk not in result_ids(response)

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
