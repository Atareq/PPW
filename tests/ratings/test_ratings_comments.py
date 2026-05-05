from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.orders.models import OrderItemStatus
from apps.ratings.models import (
    AssetComment,
    AssetRating,
    SellerComment,
    SellerRating,
)
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    OrderFactory,
    OrderItemFactory,
    SellerProfileFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db

ASSET_COMMENTS_URL = "/api/asset-comments/"
SELLER_COMMENTS_URL = "/api/seller-comments/"
ASSET_RATINGS_URL = "/api/asset-ratings/"
SELLER_RATINGS_URL = "/api/seller-ratings/"


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def concrete_customer_profile(user):
    return CustomerProfile.objects.get(pk=user.active_profile.pk)


def concrete_seller_profile(user):
    return SellerProfile.objects.get(pk=user.active_profile.pk)


def create_order_item(customer, asset=None, seller=None, status=None):
    customer_profile = (
        customer
        if isinstance(customer, CustomerProfile)
        else concrete_customer_profile(customer)
    )
    if seller is None:
        seller = SellerProfileFactory()
    if asset is None:
        asset = SupplyFactory(owner=seller)
    order = OrderFactory(owner=customer_profile)
    return OrderItemFactory(
        order=order,
        asset=asset,
        seller=seller,
        status=status or OrderItemStatus.COMPLETED,
    )


def assert_integrity_error(callback):
    with pytest.raises(IntegrityError), transaction.atomic():
        callback()


class TestAssetComments:
    def test_authenticated_profile_can_comment_on_asset(self):
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory()
        client = authenticated_client(user)

        response = client.post(
            ASSET_COMMENTS_URL,
            {"asset": {"id": asset.pk}, "text": "Looks healthy."},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["asset"]["id"] == asset.pk
        assert response.data["text"] == "Looks healthy."
        assert response.data["is_rating_comment"] is False
        assert response.data["owner"] == {
            "id": user.active_profile.pk,
            "type": ProfileType.SELLER,
        }
        assert AssetComment.objects.filter(
            owner_id=user.active_profile.pk,
            asset=asset,
            is_rating_comment=False,
        ).exists()

    def test_unauthenticated_user_cannot_comment(self):
        asset = SupplyFactory()
        client = APIClient()

        response = client.post(
            ASSET_COMMENTS_URL,
            {"asset": {"id": asset.pk}, "text": "Looks healthy."},
            format="json",
        )

        assert response.status_code == 401

    def test_asset_comment_text_is_required(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        client = authenticated_client(user)

        response = client.post(
            ASSET_COMMENTS_URL,
            {"asset": {"id": asset.pk}, "text": ""},
            format="json",
        )

        assert response.status_code == 400
        assert "text" in response.data

    def test_asset_comment_target_is_required(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(user)

        response = client.post(
            ASSET_COMMENTS_URL,
            {"text": "Looks healthy."},
            format="json",
        )

        assert response.status_code == 400
        assert "asset" in response.data

    def test_same_owner_cannot_comment_on_same_asset_within_two_minutes(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        client = authenticated_client(user)
        client.post(
            ASSET_COMMENTS_URL,
            {"asset": {"id": asset.pk}, "text": "First comment."},
            format="json",
        )

        response = client.post(
            ASSET_COMMENTS_URL,
            {"asset": {"id": asset.pk}, "text": "Second comment."},
            format="json",
        )

        assert response.status_code == 400
        assert "You must wait before commenting again" in str(response.data)

    def test_same_owner_can_comment_again_after_two_minutes(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        client = authenticated_client(user)
        first_response = client.post(
            ASSET_COMMENTS_URL,
            {"asset": {"id": asset.pk}, "text": "First comment."},
            format="json",
        )
        AssetComment.objects.filter(pk=first_response.data["id"]).update(
            created=timezone.now() - timedelta(minutes=3)
        )

        response = client.post(
            ASSET_COMMENTS_URL,
            {"asset": {"id": asset.pk}, "text": "Second comment."},
            format="json",
        )

        assert response.status_code == 201
        assert (
            AssetComment.objects.filter(
                owner_id=user.active_profile.pk,
                asset=asset,
            ).count()
            == 2
        )

    def test_asset_comments_list_can_filter_by_asset_id(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        other_asset = SupplyFactory()
        AssetComment.objects.create(
            owner=user.active_profile,
            asset=asset,
            text="Matching comment.",
        )
        AssetComment.objects.create(
            owner=user.active_profile,
            asset=other_asset,
            text="Other comment.",
        )
        client = authenticated_client(user)

        response = client.get(ASSET_COMMENTS_URL, {"asset_id": asset.pk})

        assert response.status_code == 200
        assert [item["asset"]["id"] for item in response.data["results"]] == [
            asset.pk
        ]

    def test_asset_comments_list_can_filter_by_owner_id(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory()
        AssetComment.objects.create(
            owner=user.active_profile,
            asset=asset,
            text="Matching owner.",
        )
        AssetComment.objects.create(
            owner=other_user.active_profile,
            asset=asset,
            text="Other owner.",
        )
        client = authenticated_client(user)

        response = client.get(
            ASSET_COMMENTS_URL,
            {"owner_id": user.active_profile.pk},
        )

        assert response.status_code == 200
        assert [item["owner"]["id"] for item in response.data["results"]] == [
            user.active_profile.pk
        ]

    def test_soft_deleted_asset_comments_are_excluded_from_list(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        visible = AssetComment.objects.create(
            owner=user.active_profile,
            asset=asset,
            text="Visible comment.",
        )
        deleted = AssetComment.objects.create(
            owner=user.active_profile,
            asset=asset,
            text="Deleted comment.",
        )
        deleted.delete()
        client = authenticated_client(user)

        response = client.get(ASSET_COMMENTS_URL, {"asset_id": asset.pk})

        assert response.status_code == 200
        assert [item["id"] for item in response.data["results"]] == [
            visible.pk
        ]


class TestSellerComments:
    def test_authenticated_profile_can_comment_on_seller(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        client = authenticated_client(user)

        response = client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": "Reliable seller."},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["seller"]["id"] == seller.pk
        assert response.data["text"] == "Reliable seller."
        assert response.data["is_rating_comment"] is False
        assert SellerComment.objects.filter(
            owner_id=user.active_profile.pk,
            seller=seller,
            is_rating_comment=False,
        ).exists()

    def test_unauthenticated_user_cannot_comment_on_seller(self):
        seller = SellerProfileFactory()
        client = APIClient()

        response = client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": "Reliable seller."},
            format="json",
        )

        assert response.status_code == 401

    def test_seller_profile_can_create_seller_comment(self):
        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = SellerProfileFactory()
        client = authenticated_client(user)

        response = client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": "Reliable seller."},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["owner"] == {
            "id": user.active_profile.pk,
            "type": ProfileType.SELLER,
        }
        assert response.data["seller"]["id"] == seller.pk

    def test_seller_comment_text_is_required(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        client = authenticated_client(user)

        response = client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": ""},
            format="json",
        )

        assert response.status_code == 400
        assert "text" in response.data

    def test_seller_comment_target_is_required(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(user)

        response = client.post(
            SELLER_COMMENTS_URL,
            {"text": "Reliable seller."},
            format="json",
        )

        assert response.status_code == 400
        assert "seller" in response.data

    def test_same_owner_cannot_comment_on_same_seller_within_two_minutes(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        client = authenticated_client(user)
        client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": "First comment."},
            format="json",
        )

        response = client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": "Second comment."},
            format="json",
        )

        assert response.status_code == 400
        assert "You must wait before commenting again" in str(response.data)

    def test_same_owner_can_comment_on_same_seller_after_two_minutes(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        client = authenticated_client(user)
        first_response = client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": "First comment."},
            format="json",
        )
        SellerComment.objects.filter(pk=first_response.data["id"]).update(
            created=timezone.now() - timedelta(minutes=3)
        )

        response = client.post(
            SELLER_COMMENTS_URL,
            {"seller": {"id": seller.pk}, "text": "Second comment."},
            format="json",
        )

        assert response.status_code == 201
        assert (
            SellerComment.objects.filter(
                owner_id=user.active_profile.pk,
                seller=seller,
            ).count()
            == 2
        )

    def test_seller_comments_list_can_filter_by_seller_id(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        other_seller = SellerProfileFactory()
        SellerComment.objects.create(
            owner=user.active_profile,
            seller=seller,
            text="Matching comment.",
        )
        SellerComment.objects.create(
            owner=user.active_profile,
            seller=other_seller,
            text="Other comment.",
        )
        client = authenticated_client(user)

        response = client.get(SELLER_COMMENTS_URL, {"seller_id": seller.pk})

        assert response.status_code == 200
        assert [item["seller"]["id"] for item in response.data["results"]] == [
            seller.pk
        ]

    def test_seller_comments_list_can_filter_by_owner_id(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        other_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = SellerProfileFactory()
        SellerComment.objects.create(
            owner=user.active_profile,
            seller=seller,
            text="Matching owner.",
        )
        SellerComment.objects.create(
            owner=other_user.active_profile,
            seller=seller,
            text="Other owner.",
        )
        client = authenticated_client(user)

        response = client.get(
            SELLER_COMMENTS_URL,
            {"owner_id": user.active_profile.pk},
        )

        assert response.status_code == 200
        assert [item["owner"]["id"] for item in response.data["results"]] == [
            user.active_profile.pk
        ]

    def test_soft_deleted_seller_comments_are_excluded_from_list(self):
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        visible = SellerComment.objects.create(
            owner=user.active_profile,
            seller=seller,
            text="Visible comment.",
        )
        deleted = SellerComment.objects.create(
            owner=user.active_profile,
            seller=seller,
            text="Deleted comment.",
        )
        deleted.delete()
        client = authenticated_client(user)

        response = client.get(SELLER_COMMENTS_URL, {"seller_id": seller.pk})

        assert response.status_code == 200
        assert [item["id"] for item in response.data["results"]] == [
            visible.pk
        ]


class TestRatingModelValidation:
    def test_asset_rating_stars_less_than_one_is_invalid(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = concrete_customer_profile(customer)
        asset = SupplyFactory()
        rating = AssetRating(
            owner=customer_profile,
            asset=asset,
            stars=0,
        )

        with pytest.raises(DjangoValidationError) as exc:
            rating.full_clean()

        assert "stars" in exc.value.message_dict

    def test_seller_rating_stars_greater_than_five_is_invalid(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = concrete_customer_profile(customer)
        seller = SellerProfileFactory()
        rating = SellerRating(
            owner=customer_profile,
            seller=seller,
            stars=6,
        )

        with pytest.raises(DjangoValidationError) as exc:
            rating.full_clean()

        assert "stars" in exc.value.message_dict

    def test_duplicate_owner_asset_rating_is_rejected(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = concrete_customer_profile(customer)
        asset = SupplyFactory()
        AssetRating.objects.create(
            owner=customer_profile,
            asset=asset,
            stars=5,
        )

        assert_integrity_error(
            lambda: AssetRating.objects.create(
                owner=customer_profile,
                asset=asset,
                stars=4,
            )
        )

    def test_duplicate_owner_seller_rating_is_rejected(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = concrete_customer_profile(customer)
        seller = SellerProfileFactory()
        SellerRating.objects.create(
            owner=customer_profile,
            seller=seller,
            stars=5,
        )

        assert_integrity_error(
            lambda: SellerRating.objects.create(
                owner=customer_profile,
                seller=seller,
                stars=4,
            )
        )


class TestAssetRatingApi:
    def test_customer_can_rate_asset_after_completed_order_item(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {
                "asset": {"id": item.asset_id},
                "stars": 4,
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["asset"]["id"] == item.asset_id
        assert response.data["comment"] is None
        assert response.data["stars"] == 4
        assert response.data["owner"] == {
            "id": customer.active_profile.pk,
            "type": ProfileType.CUSTOMER,
        }

    def test_customer_can_rate_asset_with_nested_comment(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {
                "asset": {"id": item.asset_id},
                "stars": 5,
                "comment": {"text": "Written review."},
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["comment"]["text"] == "Written review."
        assert response.data["comment"]["is_rating_comment"] is True
        assert response.data["comment"]["asset"]["id"] == item.asset_id
        assert response.data["comment"]["owner"] == response.data["owner"]
        assert AssetComment.objects.filter(
            owner_id=customer.active_profile.pk,
            asset_id=item.asset_id,
            is_rating_comment=True,
        ).exists()

    @pytest.mark.parametrize("stars", [0, 6])
    def test_asset_rating_rejects_stars_outside_allowed_range(self, stars):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": item.asset_id}, "stars": stars},
            format="json",
        )

        assert response.status_code == 400
        assert "stars" in response.data

    def test_asset_rating_target_is_required(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {"stars": 5},
            format="json",
        )

        assert response.status_code == 400
        assert "asset" in response.data

    def test_asset_rating_nested_comment_text_is_required(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {
                "asset": {"id": item.asset_id},
                "stars": 5,
                "comment": {},
            },
            format="json",
        )

        assert response.status_code == 400
        assert "text" in str(response.data)

    def test_asset_rating_rejects_comment_id(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        comment = AssetComment.objects.create(
            owner=customer.active_profile,
            asset=item.asset,
            text="Standalone comment.",
        )
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {
                "asset": {"id": item.asset_id},
                "stars": 5,
                "comment": {"id": comment.pk, "text": "No link."},
            },
            format="json",
        )

        assert response.status_code == 400
        assert "created inline" in str(response.data)

    def test_customer_cannot_duplicate_asset_rating_through_api(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)
        client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": item.asset_id}, "stars": 5},
            format="json",
        )

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": item.asset_id}, "stars": 4},
            format="json",
        )

        assert response.status_code == 400
        assert "already rated" in str(response.data)
        assert (
            AssetRating.objects.filter(
                owner=concrete_customer_profile(customer),
                asset=item.asset,
            ).count()
            == 1
        )

    def test_nested_asset_rating_comment_uses_comment_throttle(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        AssetComment.objects.create(
            owner=customer.active_profile,
            asset=item.asset,
            text="Recent standalone comment.",
        )
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {
                "asset": {"id": item.asset_id},
                "stars": 5,
                "comment": {"text": "Nested comment."},
            },
            format="json",
        )

        assert response.status_code == 400
        assert "You must wait before commenting again" in str(response.data)
        assert not AssetRating.objects.filter(
            owner=concrete_customer_profile(customer),
            asset=item.asset,
        ).exists()

    def test_asset_rating_without_comment_does_not_create_rating_comment(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": item.asset_id}, "stars": 5},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["comment"] is None
        assert not AssetComment.objects.filter(
            owner_id=customer.active_profile.pk,
            asset_id=item.asset_id,
            is_rating_comment=True,
        ).exists()

    def test_customer_cannot_rate_asset_without_completed_order_item(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": asset.pk}, "stars": 4},
            format="json",
        )

        assert response.status_code == 400
        assert "completed order item" in str(response.data)

    @pytest.mark.parametrize(
        "blocked_status",
        [
            OrderItemStatus.PENDING_SELLER,
            OrderItemStatus.APPROVED,
            OrderItemStatus.REJECTED,
            OrderItemStatus.CANCELLED,
        ],
    )
    def test_customer_cannot_rate_while_order_item_is_not_completed(
        self,
        blocked_status,
    ):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer, status=blocked_status)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": item.asset_id}, "stars": 4},
            format="json",
        )

        assert response.status_code == 400

    def test_seller_profile_cannot_create_asset_rating(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = concrete_seller_profile(seller_user)
        asset = SupplyFactory(owner=seller)
        client = authenticated_client(seller_user)

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": asset.pk}, "stars": 4},
            format="json",
        )

        assert response.status_code == 403

    def test_seller_profile_can_list_asset_ratings(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=concrete_seller_profile(seller_user))
        AssetRating.objects.create(
            owner=concrete_customer_profile(customer),
            asset=asset,
            stars=5,
        )
        client = authenticated_client(seller_user)

        response = client.get(ASSET_RATINGS_URL, {"asset_id": asset.pk})

        assert response.status_code == 200
        assert [item["asset"]["id"] for item in response.data["results"]] == [
            asset.pk
        ]

    def test_unauthenticated_user_cannot_create_asset_rating(self):
        asset = SupplyFactory()
        client = APIClient()

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": asset.pk}, "stars": 4},
            format="json",
        )

        assert response.status_code == 401

    def test_old_flat_asset_rating_payload_is_rejected(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {"target_type": "asset", "target_id": item.asset_id, "stars": 5},
            format="json",
        )

        assert response.status_code == 400
        assert "asset" in response.data

    def test_soft_deleted_asset_rating_allows_customer_to_rate_again(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = concrete_customer_profile(customer)
        item = create_order_item(customer)
        old_rating = AssetRating.objects.create(
            owner=customer_profile,
            asset=item.asset,
            stars=2,
        )
        old_rating.delete()
        client = authenticated_client(customer)

        response = client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": item.asset_id}, "stars": 5},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["stars"] == 5
        assert (
            AssetRating.objects.filter(
                owner=customer_profile,
                asset=item.asset,
            ).count()
            == 1
        )


class TestSellerRatingApi:
    def test_customer_can_rate_seller_after_completed_order_item(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {
                "seller": {"id": item.seller_id},
                "stars": 5,
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["seller"]["id"] == item.seller_id
        assert response.data["comment"] is None
        assert response.data["stars"] == 5
        assert response.data["owner"] == {
            "id": customer.active_profile.pk,
            "type": ProfileType.CUSTOMER,
        }

    def test_customer_can_rate_seller_with_nested_comment(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {
                "seller": {"id": item.seller_id},
                "stars": 5,
                "comment": {"text": "Reliable seller."},
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["comment"]["text"] == "Reliable seller."
        assert response.data["comment"]["is_rating_comment"] is True
        assert response.data["comment"]["seller"]["id"] == item.seller_id
        assert response.data["comment"]["owner"] == response.data["owner"]
        assert SellerComment.objects.filter(
            owner_id=customer.active_profile.pk,
            seller_id=item.seller_id,
            is_rating_comment=True,
        ).exists()

    @pytest.mark.parametrize("stars", [0, 6])
    def test_seller_rating_rejects_stars_outside_allowed_range(self, stars):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": item.seller_id}, "stars": stars},
            format="json",
        )

        assert response.status_code == 400
        assert "stars" in response.data

    def test_seller_rating_target_is_required(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {"stars": 5},
            format="json",
        )

        assert response.status_code == 400
        assert "seller" in response.data

    def test_seller_rating_nested_comment_text_is_required(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {
                "seller": {"id": item.seller_id},
                "stars": 5,
                "comment": {},
            },
            format="json",
        )

        assert response.status_code == 400
        assert "text" in str(response.data)

    def test_seller_rating_rejects_comment_id(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        comment = SellerComment.objects.create(
            owner=customer.active_profile,
            seller=item.seller,
            text="Standalone comment.",
        )
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {
                "seller": {"id": item.seller_id},
                "stars": 5,
                "comment": {"id": comment.pk, "text": "No link."},
            },
            format="json",
        )

        assert response.status_code == 400
        assert "created inline" in str(response.data)

    def test_customer_cannot_duplicate_seller_rating_through_api(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)
        client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": item.seller_id}, "stars": 5},
            format="json",
        )

        response = client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": item.seller_id}, "stars": 4},
            format="json",
        )

        assert response.status_code == 400
        assert "already rated" in str(response.data)
        assert (
            SellerRating.objects.filter(
                owner=concrete_customer_profile(customer),
                seller=item.seller,
            ).count()
            == 1
        )

    def test_nested_seller_rating_comment_uses_comment_throttle(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        SellerComment.objects.create(
            owner=customer.active_profile,
            seller=item.seller,
            text="Recent standalone comment.",
        )
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {
                "seller": {"id": item.seller_id},
                "stars": 5,
                "comment": {"text": "Nested comment."},
            },
            format="json",
        )

        assert response.status_code == 400
        assert "You must wait before commenting again" in str(response.data)
        assert not SellerRating.objects.filter(
            owner=concrete_customer_profile(customer),
            seller=item.seller,
        ).exists()

    def test_customer_cannot_rate_seller_without_completed_order_item(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": seller.pk}, "stars": 4},
            format="json",
        )

        assert response.status_code == 400
        assert "completed order item" in str(response.data)

    def test_seller_profile_cannot_create_seller_rating(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = concrete_seller_profile(seller_user)
        client = authenticated_client(seller_user)

        response = client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": seller.pk}, "stars": 4},
            format="json",
        )

        assert response.status_code == 403

    def test_seller_profile_can_list_seller_ratings(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = concrete_seller_profile(seller_user)
        SellerRating.objects.create(
            owner=concrete_customer_profile(customer),
            seller=seller,
            stars=5,
        )
        client = authenticated_client(seller_user)

        response = client.get(SELLER_RATINGS_URL, {"seller_id": seller.pk})

        assert response.status_code == 200
        assert [item["seller"]["id"] for item in response.data["results"]] == [
            seller.pk
        ]

    def test_old_flat_seller_rating_payload_is_rejected(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        item = create_order_item(customer)
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {
                "target_type": "seller",
                "target_id": item.seller_id,
                "stars": 5,
            },
            format="json",
        )

        assert response.status_code == 400
        assert "seller" in response.data

    def test_soft_deleted_seller_rating_allows_customer_to_rate_again(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = concrete_customer_profile(customer)
        item = create_order_item(customer)
        old_rating = SellerRating.objects.create(
            owner=customer_profile,
            seller=item.seller,
            stars=2,
        )
        old_rating.delete()
        client = authenticated_client(customer)

        response = client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": item.seller_id}, "stars": 5},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["stars"] == 5
        assert (
            SellerRating.objects.filter(
                owner=customer_profile,
                seller=item.seller,
            ).count()
            == 1
        )


class TestRatingAggregatesAndLists:
    def test_asset_rating_average_and_count_update_after_rating_creation(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        create_order_item(first_customer, asset=asset, seller=asset.owner)
        create_order_item(second_customer, asset=asset, seller=asset.owner)
        first_client = authenticated_client(first_customer)
        second_client = authenticated_client(second_customer)

        first_client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": asset.pk}, "stars": 5},
            format="json",
        )
        second_client.post(
            ASSET_RATINGS_URL,
            {"asset": {"id": asset.pk}, "stars": 3},
            format="json",
        )

        asset.refresh_from_db()
        assert asset.ratings_count == 2
        assert asset.avg_rating == Decimal("4.00")

    def test_seller_rating_average_and_count_update_after_rating_creation(
        self,
    ):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        create_order_item(first_customer, seller=seller)
        create_order_item(second_customer, seller=seller)
        first_client = authenticated_client(first_customer)
        second_client = authenticated_client(second_customer)

        first_client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": seller.pk}, "stars": 5},
            format="json",
        )
        second_client.post(
            SELLER_RATINGS_URL,
            {"seller": {"id": seller.pk}, "stars": 3},
            format="json",
        )

        seller.refresh_from_db()
        assert seller.ratings_count == 2
        assert seller.avg_rating == Decimal("4.00")

    def test_asset_ratings_filter_by_asset_id(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory()
        other_asset = SupplyFactory()
        AssetRating.objects.create(
            owner=concrete_customer_profile(first_customer),
            asset=asset,
            stars=5,
        )
        AssetRating.objects.create(
            owner=concrete_customer_profile(second_customer),
            asset=other_asset,
            stars=3,
        )
        client = authenticated_client(first_customer)

        response = client.get(ASSET_RATINGS_URL, {"asset_id": asset.pk})

        assert response.status_code == 200
        assert [item["asset"]["id"] for item in response.data["results"]] == [
            asset.pk
        ]

    def test_seller_ratings_filter_by_seller_id(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = SellerProfileFactory()
        other_seller = SellerProfileFactory()
        SellerRating.objects.create(
            owner=concrete_customer_profile(first_customer),
            seller=seller,
            stars=5,
        )
        SellerRating.objects.create(
            owner=concrete_customer_profile(second_customer),
            seller=other_seller,
            stars=3,
        )
        client = authenticated_client(first_customer)

        response = client.get(SELLER_RATINGS_URL, {"seller_id": seller.pk})

        assert response.status_code == 200
        assert [item["seller"]["id"] for item in response.data["results"]] == [
            seller.pk
        ]

    def test_asset_ratings_filter_by_stars(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        AssetRating.objects.create(
            owner=concrete_customer_profile(first_customer),
            asset=SupplyFactory(),
            stars=5,
        )
        AssetRating.objects.create(
            owner=concrete_customer_profile(second_customer),
            asset=SupplyFactory(),
            stars=3,
        )
        client = authenticated_client(first_customer)

        response = client.get(ASSET_RATINGS_URL, {"stars": 5})

        assert response.status_code == 200
        assert [item["stars"] for item in response.data["results"]] == [5]

    def test_seller_ratings_filter_by_stars(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        SellerRating.objects.create(
            owner=concrete_customer_profile(first_customer),
            seller=SellerProfileFactory(),
            stars=5,
        )
        SellerRating.objects.create(
            owner=concrete_customer_profile(second_customer),
            seller=SellerProfileFactory(),
            stars=3,
        )
        client = authenticated_client(first_customer)

        response = client.get(SELLER_RATINGS_URL, {"stars": 3})

        assert response.status_code == 200
        assert [item["stars"] for item in response.data["results"]] == [3]

    def test_asset_rating_ordering_works(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        AssetRating.objects.create(
            owner=concrete_customer_profile(first_customer),
            asset=SupplyFactory(),
            stars=5,
        )
        AssetRating.objects.create(
            owner=concrete_customer_profile(second_customer),
            asset=SupplyFactory(),
            stars=2,
        )
        client = authenticated_client(first_customer)

        response = client.get(ASSET_RATINGS_URL, {"ordering": "stars"})

        assert response.status_code == 200
        assert [item["stars"] for item in response.data["results"]] == [2, 5]

    def test_seller_rating_ordering_works(self):
        first_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        second_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        SellerRating.objects.create(
            owner=concrete_customer_profile(first_customer),
            seller=SellerProfileFactory(),
            stars=5,
        )
        SellerRating.objects.create(
            owner=concrete_customer_profile(second_customer),
            seller=SellerProfileFactory(),
            stars=2,
        )
        client = authenticated_client(first_customer)

        response = client.get(SELLER_RATINGS_URL, {"ordering": "stars"})

        assert response.status_code == 200
        assert [item["stars"] for item in response.data["results"]] == [2, 5]
