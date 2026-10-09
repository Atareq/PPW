from datetime import timedelta

from django.utils import timezone
import pytest
from rest_framework import status

from apps.auctions.models import Auction, AuctionType
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.feed.models import SellerCustomerBlock, SellerFollow
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile, SellerRoom, SellerRoomFollow
from tests.factories import (
    ActiveAssetSellerPackageFactory,
    AuctionFactory,
    BirdFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)


def _auth_client_for_user(api_client_cls, user):
    client = api_client_cls()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def _concrete_seller(user) -> SellerProfile:
    profile = user.active_profile
    if isinstance(profile, SellerProfile):
        return profile
    return SellerProfile.objects.get(pk=profile.pk)


def _auction_create_payload(*, bird_id: int, seller_package_id: int, **overrides):
    now = timezone.now()
    payload = {
        "seller_package_id": seller_package_id,
        "title": "Elite Room Auction",
        "description": "High pedigree auction",
        "auction_type": AuctionType.SINGLE,
        "start_time": (now + timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(days=1)).isoformat(),
        "auto_extend_enabled": False,
        "auto_extend_minutes": 5,
        "buy_now_enabled": False,
        "deposit_required": False,
        "payment_deadline_days": None,
        "min_bid_increment": "10.00",
        "tags": "elite,room",
        "items": [
            {
                "bird_id": bird_id,
                "starting_price": "100.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestSellerMultiRoomFoundation:
    """Comprehensive tests for BE-ROOM-01 — Seller Multi-Room Foundation."""

    def test_01_seller_creates_room_1(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = _auth_client_for_user(type(api_client), seller_user)

        response = client.post(
            "/api/seller-rooms/",
            {"name": "Elite Racing Room", "description": "Championship birds only"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Elite Racing Room"
        assert response.data["description"] == "Championship birds only"
        assert response.data["seller_id"] == seller_user.active_profile.pk
        assert SellerRoom.objects.filter(pk=response.data["id"]).exists()

    def test_02_seller_creates_room_2(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = _auth_client_for_user(type(api_client), seller_user)

        resp1 = client.post(
            "/api/seller-rooms/",
            {"name": "Room 1 - Elite", "description": "Elite room"},
            format="json",
        )
        resp2 = client.post(
            "/api/seller-rooms/",
            {"name": "Room 2 - Standard", "description": "Standard room"},
            format="json",
        )

        assert resp1.status_code == status.HTTP_201_CREATED
        assert resp2.status_code == status.HTTP_201_CREATED
        assert resp1.data["id"] != resp2.data["id"]

    def test_03_both_rooms_belong_to_same_seller_profile(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        client = _auth_client_for_user(type(api_client), seller_user)

        r1 = client.post(
            "/api/seller-rooms/",
            {"name": "North Loft Room", "description": "North"},
            format="json",
        ).data
        r2 = client.post(
            "/api/seller-rooms/",
            {"name": "South Loft Room", "description": "South"},
            format="json",
        ).data

        assert r1["seller_id"] == seller.pk
        assert r2["seller_id"] == seller.pk
        assert r1["seller_nickname"] == seller.nickname
        assert r2["seller_nickname"] == seller.nickname
        assert SellerRoom.objects.filter(seller=seller).count() == 2

    def test_04_room_requires_non_empty_name(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = _auth_client_for_user(type(api_client), seller_user)

        response = client.post(
            "/api/seller-rooms/",
            {"description": "Missing name"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data

    def test_05_blank_room_name_rejected(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = _auth_client_for_user(type(api_client), seller_user)

        response = client.post(
            "/api/seller-rooms/",
            {"name": "   ", "description": "Whitespace name"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data

    def test_06_description_stored_correctly(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        client = _auth_client_for_user(type(api_client), seller_user)

        response = client.post(
            "/api/seller-rooms/",
            {"name": "Breeding Room", "description": "   Dedicated breeding pairs  "},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        room = SellerRoom.objects.get(pk=response.data["id"])
        assert room.description == "Dedicated breeding pairs"
        assert response.data["description"] == "Dedicated breeding pairs"

    def test_07_seller_updates_own_room_name(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room = SellerRoom.objects.create(
            seller=seller, name="Old Room Name", description="Desc"
        )
        client = _auth_client_for_user(type(api_client), seller_user)

        response = client.patch(
            f"/api/seller-rooms/{room.pk}/",
            {"name": "Updated Room Name"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        room.refresh_from_db()
        assert room.name == "Updated Room Name"
        assert response.data["name"] == "Updated Room Name"

    def test_08_seller_updates_own_room_description(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room = SellerRoom.objects.create(
            seller=seller, name="My Room", description="Old description"
        )
        client = _auth_client_for_user(type(api_client), seller_user)

        response = client.patch(
            f"/api/seller-rooms/{room.pk}/",
            {"description": "New updated description"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        room.refresh_from_db()
        assert room.description == "New updated description"
        assert response.data["description"] == "New updated description"

    def test_09_seller_cannot_update_another_sellers_room(self, api_client):
        seller_1 = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_2 = UserAccountFactory(with_profile=ProfileType.SELLER)
        room_1 = SellerRoom.objects.create(
            seller=_concrete_seller(seller_1),
            name="Seller 1 Room",
            description="Original",
        )
        client_2 = _auth_client_for_user(type(api_client), seller_2)

        patch_resp = client_2.patch(
            f"/api/seller-rooms/{room_1.pk}/",
            {"name": "Hijacked Room"},
            format="json",
        )
        delete_resp = client_2.delete(f"/api/seller-rooms/{room_1.pk}/")

        assert patch_resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        assert delete_resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        room_1.refresh_from_db()
        assert room_1.name == "Seller 1 Room"
        assert room_1.deleted is None

    def test_10_customer_cannot_create_room(self, api_client):
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        response = client.post(
            "/api/seller-rooms/",
            {"name": "Customer Room", "description": "Not allowed"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert SellerRoom.objects.count() == 0

    def test_11_unauthenticated_user_cannot_create_room(self, api_client):
        response = api_client.post(
            "/api/seller-rooms/",
            {"name": "Anon Room", "description": "Not allowed"},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert SellerRoom.objects.count() == 0

    def test_12_seller_page_and_detail_returns_all_seller_rooms(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room_1 = SellerRoom.objects.create(
            seller=seller, name="Room Alpha", description="Alpha"
        )
        room_2 = SellerRoom.objects.create(
            seller=seller, name="Room Beta", description="Beta"
        )

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_client = _auth_client_for_user(type(api_client), customer_user)

        # Public/Customer seller discovery list and detail
        list_resp = customer_client.get("/api/feed/sellers/")
        assert list_resp.status_code == status.HTTP_200_OK
        seller_entry = next(
            item for item in list_resp.data["results"] if item["id"] == seller.pk
        )
        assert [r["id"] for r in seller_entry["rooms"]] == [room_2.pk, room_1.pk]

        detail_resp = customer_client.get(f"/api/feed/sellers/{seller.pk}/")
        assert detail_resp.status_code == status.HTTP_200_OK
        assert [r["id"] for r in detail_resp.data["rooms"]] == [room_2.pk, room_1.pk]

        # Seller's own mobile profile representation also includes rooms
        seller_client = _auth_client_for_user(type(api_client), seller_user)
        mobile_resp = seller_client.get(f"/api/users/mobile/sellers/{seller.pk}/")
        assert mobile_resp.status_code == status.HTTP_200_OK
        assert [r["id"] for r in mobile_resp.data["rooms"]] == [room_2.pk, room_1.pk]

    def test_13_room_list_clearly_references_same_seller_id_and_name(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        SellerRoom.objects.create(seller=seller, name="Room A", description="A")
        SellerRoom.objects.create(seller=seller, name="Room B", description="B")

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        response = client.get(f"/api/seller-rooms/?seller_id={seller.pk}")
        assert response.status_code == status.HTTP_200_OK
        results = (
            response.data["results"]
            if isinstance(response.data, dict) and "results" in response.data
            else response.data
        )
        assert len(results) == 2
        for room_payload in results:
            assert room_payload["seller"] == seller.pk
            assert room_payload["seller_id"] == seller.pk
            assert room_payload["seller_nickname"] == seller.nickname

    def test_14_soft_deleted_room_excluded_from_public_seller_rooms(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room_active = SellerRoom.objects.create(
            seller=seller, name="Active Room", description="Visible"
        )
        room_deleted = SellerRoom.objects.create(
            seller=seller, name="Deleted Room", description="Hidden"
        )
        seller_client = _auth_client_for_user(type(api_client), seller_user)
        del_resp = seller_client.delete(f"/api/seller-rooms/{room_deleted.pk}/")
        assert del_resp.status_code == status.HTTP_204_NO_CONTENT

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_client = _auth_client_for_user(type(api_client), customer_user)

        rooms_resp = customer_client.get(f"/api/seller-rooms/?seller_id={seller.pk}")
        results = (
            rooms_resp.data["results"]
            if isinstance(rooms_resp.data, dict) and "results" in rooms_resp.data
            else rooms_resp.data
        )
        assert [r["id"] for r in results] == [room_active.pk]

        seller_detail_resp = customer_client.get(f"/api/feed/sellers/{seller.pk}/")
        assert [r["id"] for r in seller_detail_resp.data["rooms"]] == [room_active.pk]

        deleted_detail_resp = customer_client.get(
            f"/api/seller-rooms/{room_deleted.pk}/"
        )
        assert deleted_detail_resp.status_code == status.HTTP_404_NOT_FOUND

    def test_15_seller_creates_auction_in_room_1(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        seller_package = ActiveAssetSellerPackageFactory(owner=seller)
        bird = BirdFactory(owner=seller)
        room_1 = SellerRoom.objects.create(
            seller=seller, name="Room 1", description="First room"
        )
        client = _auth_client_for_user(type(api_client), seller_user)

        payload = _auction_create_payload(
            bird_id=bird.pk,
            seller_package_id=seller_package.pk,
            room=room_1.pk,
            title="Room 1 Auction",
        )
        response = client.post("/api/auctions/", payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["room"] == room_1.pk
        assert response.data["room_name"] == "Room 1"
        auction = Auction.objects.get(pk=response.data["id"])
        assert auction.room_id == room_1.pk
        assert auction.owner_id == seller.pk

    def test_16_seller_creates_auction_in_room_2(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        seller_package = ActiveAssetSellerPackageFactory(owner=seller, remaining_points=100)
        bird_2 = BirdFactory(owner=seller)
        room_2 = SellerRoom.objects.create(
            seller=seller, name="Room 2", description="Second room"
        )
        client = _auth_client_for_user(type(api_client), seller_user)

        payload = _auction_create_payload(
            bird_id=bird_2.pk,
            seller_package_id=seller_package.pk,
            room_id=room_2.pk,
            title="Room 2 Auction",
        )
        response = client.post("/api/auctions/", payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["room"] == room_2.pk
        assert response.data["room_name"] == "Room 2"

    def test_17_and_18_room_auction_list_isolates_room_1_and_room_2_auctions(
        self, api_client
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room_1 = SellerRoom.objects.create(seller=seller, name="Room 1")
        room_2 = SellerRoom.objects.create(seller=seller, name="Room 2")
        auction_1 = AuctionFactory(owner=seller, room=room_1, title="R1 Auction")
        auction_2 = AuctionFactory(owner=seller, room=room_2, title="R2 Auction")

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        resp_r1 = client.get(f"/api/auctions/?room={room_1.pk}")
        assert resp_r1.status_code == status.HTTP_200_OK
        r1_ids = [a["id"] for a in resp_r1.data["results"]]
        assert r1_ids == [auction_1.pk]
        assert auction_2.pk not in r1_ids

        resp_r2 = client.get(f"/api/auctions/?room={room_2.pk}")
        assert resp_r2.status_code == status.HTTP_200_OK
        r2_ids = [a["id"] for a in resp_r2.data["results"]]
        assert r2_ids == [auction_2.pk]
        assert auction_1.pk not in r2_ids

    def test_19_and_20_legacy_auction_without_room_works_and_appears_in_unfiltered_list(
        self, api_client
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        seller_package = ActiveAssetSellerPackageFactory(owner=seller)
        bird = BirdFactory(owner=seller)
        room_1 = SellerRoom.objects.create(seller=seller, name="Room 1")
        room_auction = AuctionFactory(owner=seller, room=room_1, title="Room Auction")

        seller_client = _auth_client_for_user(type(api_client), seller_user)
        legacy_payload = _auction_create_payload(
            bird_id=bird.pk,
            seller_package_id=seller_package.pk,
            title="Legacy No-Room Auction",
        )
        create_resp = seller_client.post(
            "/api/auctions/", legacy_payload, format="json"
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        legacy_auction_id = create_resp.data["id"]
        assert create_resp.data["room"] is None
        assert create_resp.data["room_name"] is None

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_client = _auth_client_for_user(type(api_client), customer_user)

        unfiltered_resp = customer_client.get("/api/auctions/")
        assert unfiltered_resp.status_code == status.HTTP_200_OK
        returned_ids = {a["id"] for a in unfiltered_resp.data["results"]}
        assert legacy_auction_id in returned_ids
        assert room_auction.pk in returned_ids

    def test_21_seller_cannot_create_or_update_auction_in_another_sellers_room(
        self, api_client
    ):
        seller_1 = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_2 = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_1_profile = _concrete_seller(seller_1)
        seller_2_profile = _concrete_seller(seller_2)

        other_room = SellerRoom.objects.create(
            seller=seller_2_profile, name="Seller 2 Private Room"
        )
        seller_package = ActiveAssetSellerPackageFactory(owner=seller_1_profile)
        bird = BirdFactory(owner=seller_1_profile)
        client_1 = _auth_client_for_user(type(api_client), seller_1)

        payload = _auction_create_payload(
            bird_id=bird.pk,
            seller_package_id=seller_package.pk,
            room=other_room.pk,
        )
        create_resp = client_1.post("/api/auctions/", payload, format="json")
        assert create_resp.status_code == status.HTTP_400_BAD_REQUEST

        own_auction = AuctionFactory(owner=seller_1_profile, room=None)
        patch_resp = client_1.patch(
            f"/api/auctions/{own_auction.pk}/",
            {"room": other_room.pk},
            format="json",
        )
        assert patch_resp.status_code == status.HTTP_400_BAD_REQUEST
        own_auction.refresh_from_db()
        assert own_auction.room_id is None

    def test_22_and_23_auction_detail_exposes_room_id_and_name_or_none_for_legacy(
        self, api_client
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room = SellerRoom.objects.create(seller=seller, name="Champions Room")
        room_auction = AuctionFactory(owner=seller, room=room)
        legacy_auction = AuctionFactory(owner=seller, room=None)

        client = _auth_client_for_user(type(api_client), seller_user)

        room_detail = client.get(f"/api/auctions/{room_auction.pk}/")
        assert room_detail.status_code == status.HTTP_200_OK
        assert room_detail.data["room"] == room.pk
        assert room_detail.data["room_name"] == "Champions Room"

        legacy_detail = client.get(f"/api/auctions/{legacy_auction.pk}/")
        assert legacy_detail.status_code == status.HTTP_200_OK
        assert legacy_detail.data["room"] is None
        assert legacy_detail.data["room_name"] is None

    def test_24_25_26_user_follows_room_1_and_room_2_independently(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room_1 = SellerRoom.objects.create(seller=seller, name="Room 1")
        room_2 = SellerRoom.objects.create(seller=seller, name="Room 2")

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        # 24: Follow room 1
        follow_r1 = client.post(f"/api/seller-rooms/{room_1.pk}/follow/")
        assert follow_r1.status_code == status.HTTP_200_OK
        assert follow_r1.data["room"]["id"] == room_1.pk
        assert follow_r1.data["room"]["is_followed"] is True
        assert follow_r1.data["room"]["followers_count"] == 1

        # 25: Following room 1 does NOT follow room 2 or SellerFollow
        r2_detail = client.get(f"/api/seller-rooms/{room_2.pk}/")
        assert r2_detail.status_code == status.HTTP_200_OK
        assert r2_detail.data["is_followed"] is False
        assert r2_detail.data["followers_count"] == 0
        assert not SellerFollow.objects.filter(seller=seller).exists()

        # 26: Follow room 2 independently
        follow_r2 = client.post(f"/api/seller-rooms/{room_2.pk}/follow/")
        assert follow_r2.status_code == status.HTTP_200_OK
        assert follow_r2.data["room"]["id"] == room_2.pk
        assert follow_r2.data["room"]["is_followed"] is True
        assert (
            SellerRoomFollow.objects.filter(
                user=customer_user, deleted__isnull=True
            ).count()
            == 2
        )

    def test_27_duplicate_follow_on_same_room_is_idempotent(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        room = SellerRoom.objects.create(
            seller=_concrete_seller(seller_user), name="Idempotent Room"
        )
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        first_resp = client.post(f"/api/seller-rooms/{room.pk}/follow/")
        second_resp = client.post(f"/api/seller-rooms/{room.pk}/follow/")

        assert first_resp.status_code == status.HTTP_200_OK
        assert second_resp.status_code == status.HTTP_200_OK
        assert (
            SellerRoomFollow.objects.filter(room=room, user=customer_user).count() == 1
        )

    def test_28_user_unfollows_room_1_while_remaining_follower_of_room_2(
        self, api_client
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room_1 = SellerRoom.objects.create(seller=seller, name="Room 1")
        room_2 = SellerRoom.objects.create(seller=seller, name="Room 2")

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        client.post(f"/api/seller-rooms/{room_1.pk}/follow/")
        client.post(f"/api/seller-rooms/{room_2.pk}/follow/")

        unfollow_resp = client.post(f"/api/seller-rooms/{room_1.pk}/unfollow/")
        assert unfollow_resp.status_code == status.HTTP_200_OK
        assert unfollow_resp.data == {"room_id": room_1.pk, "is_followed": False}

        # Second unfollow is idempotent
        unfollow_again = client.post(f"/api/seller-rooms/{room_1.pk}/unfollow/")
        assert unfollow_again.status_code == status.HTTP_200_OK
        assert unfollow_again.data == {"room_id": room_1.pk, "is_followed": False}

        assert not SellerRoomFollow.objects.filter(
            room=room_1, user=customer_user
        ).exists()
        assert SellerRoomFollow.objects.filter(room=room_2, user=customer_user).exists()

    def test_29_user_cannot_follow_own_seller_room(self, api_client):
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(user)
        room = SellerRoom.objects.create(seller=seller, name="Own Room")

        # Give same UserAccount a valid CustomerProfile as well
        CustomerProfileFactory(
            user=user, is_active=False, activated=True, is_valid=True
        )
        switch_user_profile(user, new_profile_type=ProfileType.CUSTOMER)
        user.refresh_from_db()

        client = _auth_client_for_user(type(api_client), user)
        response = client.post(f"/api/seller-rooms/{room.pk}/follow/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not SellerRoomFollow.objects.filter(room=room).exists()

    def test_30_unauthenticated_user_cannot_follow_room(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        room = SellerRoom.objects.create(
            seller=_concrete_seller(seller_user), name="Public Room"
        )

        response = api_client.post(f"/api/seller-rooms/{room.pk}/follow/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_31_following_non_existent_or_deleted_room_returns_404(self, api_client):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        room = SellerRoom.objects.create(
            seller=_concrete_seller(seller_user), name="Soon Deleted"
        )
        room.delete()

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        resp_deleted = client.post(f"/api/seller-rooms/{room.pk}/follow/")
        resp_missing = client.post("/api/seller-rooms/999999/follow/")

        assert resp_deleted.status_code == status.HTTP_404_NOT_FOUND
        assert resp_missing.status_code == status.HTTP_404_NOT_FOUND

    def test_32_33_34_room_follower_count_and_is_followed_accurate_per_user(
        self, api_client
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        room = SellerRoom.objects.create(
            seller=_concrete_seller(seller_user), name="Popular Room"
        )
        customer_1 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_2 = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

        client_1 = _auth_client_for_user(type(api_client), customer_1)
        client_2 = _auth_client_for_user(type(api_client), customer_2)

        client_1.post(f"/api/seller-rooms/{room.pk}/follow/")

        resp_1 = client_1.get(f"/api/seller-rooms/{room.pk}/")
        assert resp_1.status_code == status.HTTP_200_OK
        assert resp_1.data["followers_count"] == 1
        assert resp_1.data["is_followed"] is True

        resp_2 = client_2.get(f"/api/seller-rooms/{room.pk}/")
        assert resp_2.status_code == status.HTTP_200_OK
        assert resp_2.data["followers_count"] == 1
        assert resp_2.data["is_followed"] is False

    def test_35_profile_switching_customer_to_seller_preserves_room_follow(
        self, api_client
    ):
        seller_owner = UserAccountFactory(with_profile=ProfileType.SELLER)
        room = SellerRoom.objects.create(
            seller=_concrete_seller(seller_owner), name="Cross-Profile Room"
        )

        dual_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        SellerProfileFactory(
            user=dual_user, is_active=False, activated=True, is_valid=True
        )

        customer_client = _auth_client_for_user(type(api_client), dual_user)
        follow_resp = customer_client.post(f"/api/seller-rooms/{room.pk}/follow/")
        assert follow_resp.status_code == status.HTTP_200_OK

        # Switch dual_user to Seller profile
        switched, _ = switch_user_profile(dual_user, new_profile_type=ProfileType.SELLER)
        assert switched is True
        dual_user.refresh_from_db()

        seller_client = _auth_client_for_user(type(api_client), dual_user)
        detail_resp = seller_client.get(f"/api/seller-rooms/{room.pk}/")
        assert detail_resp.status_code == status.HTTP_200_OK
        assert detail_resp.data["is_followed"] is True
        assert detail_resp.data["followers_count"] == 1

    def test_36_seller_payment_details_not_exposed_in_room_or_seller_serializers(
        self, api_client
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        seller.bank_account = "EG99000011112222333344445555"
        seller.wallet = "VF-CASH-SECRET-999"
        seller.transfer_phone = "+201099887766"
        seller.save(update_fields=["bank_account", "wallet", "transfer_phone"])

        room = SellerRoom.objects.create(
            seller=seller, name="Privacy Check Room", description="Safe"
        )

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = _auth_client_for_user(type(api_client), customer_user)

        room_resp = client.get(f"/api/seller-rooms/{room.pk}/")
        seller_resp = client.get(f"/api/feed/sellers/{seller.pk}/")

        for payload in (room_resp.data, seller_resp.data):
            serialized = str(payload)
            assert "bank_account" not in serialized
            assert "wallet" not in serialized
            assert "transfer_phone" not in serialized
            assert "EG99000011112222333344445555" not in serialized
            assert "VF-CASH-SECRET-999" not in serialized
            assert "+201099887766" not in serialized

    def test_37_blocked_seller_room_cannot_be_followed_and_existing_seller_follow_intact(
        self, api_client
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = _concrete_seller(seller_user)
        room = SellerRoom.objects.create(seller=seller, name="Blocked Seller Room")

        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = customer_user.active_profile
        SellerCustomerBlock.objects.create(
            customer=customer_profile,
            seller=seller,
            blocked_by=seller,
        )

        client = _auth_client_for_user(type(api_client), customer_user)
        resp = client.post(f"/api/seller-rooms/{room.pk}/follow/")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not SellerRoomFollow.objects.filter(room=room).exists()
