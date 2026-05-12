from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.assets.models import Asset
from apps.orders.models import OrderItem, OrderItemStatus
from apps.ratings.models import AssetRating, SellerRating
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile


class RatingValidator:
    @staticmethod
    def get_customer_profile(profile):
        if profile.type != ProfileType.CUSTOMER:
            raise ValidationError(
                {"customer": "Only customer profiles can create ratings."}
            )
        return CustomerProfile.objects.get(pk=profile.pk)

    @staticmethod
    def get_asset(asset_id):
        try:
            return Asset.objects.get(pk=asset_id)
        except Asset.DoesNotExist as exc:
            raise ValidationError({"asset": "Asset was not found."}) from exc

    @staticmethod
    def get_seller(seller_id):
        try:
            return SellerProfile.objects.get(pk=seller_id)
        except SellerProfile.DoesNotExist as exc:
            raise ValidationError(
                {"seller": "Seller profile was not found."}
            ) from exc

    @staticmethod
    def validate_asset_completed_order(customer_profile, asset):
        has_completed_item = OrderItem.objects.filter(
            order__owner=customer_profile,
            asset=asset,
            status=OrderItemStatus.COMPLETED,
        ).exists()
        if not has_completed_item:
            raise ValidationError(
                {
                    "asset": (
                        "A completed order item is required to rate this "
                        "asset."
                    )
                }
            )

    @staticmethod
    def validate_seller_completed_order(customer_profile, seller):
        has_completed_item = OrderItem.objects.filter(
            order__owner=customer_profile,
            seller=seller,
            status=OrderItemStatus.COMPLETED,
        ).exists()
        if not has_completed_item:
            raise ValidationError(
                {
                    "seller": (
                        "A completed order item is required to rate this "
                        "seller."
                    )
                }
            )

    @staticmethod
    def validate_duplicate_asset_rating(customer_profile, asset):
        if AssetRating.objects.filter(
            owner=customer_profile,
            asset=asset,
        ).exists():
            raise ValidationError(
                {"asset": "You have already rated this asset."}
            )

    @staticmethod
    def validate_duplicate_seller_rating(customer_profile, seller):
        if SellerRating.objects.filter(
            owner=customer_profile,
            seller=seller,
        ).exists():
            raise ValidationError(
                {"seller": "You have already rated this seller."}
            )


class CommentValidator:
    @staticmethod
    def validate_comment_throttle(model, owner, target_field, target):
        cutoff = timezone.now() - timedelta(minutes=2)
        recent_comment_exists = model.objects.filter(
            owner=owner,
            created__gte=cutoff,
            **{target_field: target},
        ).exists()
        if recent_comment_exists:
            raise ValidationError(
                {
                    "detail": (
                        "You must wait before commenting again on this "
                        "target."
                    )
                }
            )
