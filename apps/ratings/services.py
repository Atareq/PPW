from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count

from apps.ratings.models import (
    AssetComment,
    AssetRating,
    SellerComment,
    SellerRating,
)
from apps.ratings.validators import CommentValidator, RatingValidator


def _format_rating_average(value):
    """Turn the rating into valid numbers"""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def update_asset_rating(asset):
    summary = AssetRating.objects.filter(asset=asset).aggregate(
        avg=Avg("stars"),
        count=Count("id"),
    )
    asset.avg_rating = _format_rating_average(summary["avg"])
    asset.ratings_count = summary["count"]
    asset.save(
        update_fields=[
            "avg_rating",
            "ratings_count",
            "modified",
        ]
    )
    return asset


def update_seller_rating(seller):
    summary = SellerRating.objects.filter(seller=seller).aggregate(
        avg=Avg("stars"),
        count=Count("id"),
    )
    seller.avg_rating = _format_rating_average(summary["avg"])
    seller.ratings_count = summary["count"]
    seller.save(
        update_fields=[
            "avg_rating",
            "ratings_count",
            "modified",
        ]
    )
    return seller


def create_asset_comment(owner, asset_id, text, *, is_rating_comment=False):
    asset = RatingValidator.get_asset(asset_id)
    CommentValidator.validate_comment_throttle(
        AssetComment,
        owner,
        "asset",
        asset,
    )
    return AssetComment.objects.create(
        owner=owner,
        asset=asset,
        text=text,
        is_rating_comment=is_rating_comment,
    )


def create_seller_comment(owner, seller_id, text, *, is_rating_comment=False):
    seller = RatingValidator.get_seller(seller_id)
    CommentValidator.validate_comment_throttle(
        SellerComment,
        owner,
        "seller",
        seller,
    )
    return SellerComment.objects.create(
        owner=owner,
        seller=seller,
        text=text,
        is_rating_comment=is_rating_comment,
    )


@transaction.atomic
def create_asset_rating(customer_profile, asset_id, stars, comment_data=None):
    """Create a rating only after a completed order item for the asset."""

    customer_profile = RatingValidator.get_customer_profile(customer_profile)
    asset = RatingValidator.get_asset(asset_id)
    RatingValidator.validate_asset_completed_order(customer_profile, asset)
    RatingValidator.validate_duplicate_asset_rating(customer_profile, asset)

    comment = None
    if comment_data:
        comment = create_asset_comment(
            customer_profile,
            asset.pk,
            comment_data["text"],
            is_rating_comment=True,
        )

    rating = AssetRating.objects.create(
        owner=customer_profile,
        asset=asset,
        stars=stars,
        comment=comment,
    )
    update_asset_rating(asset)
    return rating


@transaction.atomic
def create_seller_rating(
    customer_profile, seller_id, stars, comment_data=None
):
    """Create a rating only after a completed order item for the seller."""

    customer_profile = RatingValidator.get_customer_profile(customer_profile)
    seller = RatingValidator.get_seller(seller_id)
    RatingValidator.validate_seller_completed_order(customer_profile, seller)
    RatingValidator.validate_duplicate_seller_rating(customer_profile, seller)

    comment = None
    if comment_data:
        comment = create_seller_comment(
            customer_profile,
            seller.pk,
            comment_data["text"],
            is_rating_comment=True,
        )

    rating = SellerRating.objects.create(
        owner=customer_profile,
        seller=seller,
        stars=stars,
        comment=comment,
    )
    update_seller_rating(seller)
    return rating
