from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, ValidationError

from apps.deals.models import CompletedDeal
from apps.ratings.models import (
    AssetComment,
    AssetRating,
    DealRating,
    DealRatingDirection,
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
    _handle_rating_reward(customer_profile, rating)
    return rating


@transaction.atomic
def create_seller_rating(customer_profile, seller_id, stars, comment_data=None):
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
    _handle_rating_reward(customer_profile, rating)
    return rating


def _handle_rating_reward(customer_profile, rating):
    from apps.loyalty.constants import RewardEventType
    from apps.loyalty.reward_engine import RewardEngine

    RewardEngine.handle_event(
        RewardEventType.RATING_CREATED,
        actor=customer_profile,
        profile=customer_profile,
        context={"rating": rating},
    )


class DealRatingService:
    """Deal-scoped buyer <-> seller ratings and mutual-completion detection.

    Only the two parties of a ``CompletedDeal`` can rate, each once, and each
    rates the other party; the rated profile is never taken from the caller.
    Creating the second, reciprocal rating emits ``MUTUAL_RATING_COMPLETED``.
    """

    @staticmethod
    @transaction.atomic
    def create(rater_profile, deal_id, stars, comment=""):
        # Locking the deal serializes both parties' submissions, so whichever
        # rating is saved second always sees the first and completes the pair.
        deal = DealRatingService._get_participant_deal(
            rater_profile,
            deal_id,
            lock=True,
        )
        direction, rated_profile_id = DealRatingService._direction_for(
            deal,
            rater_profile,
        )
        if DealRating.all_objects.filter(deal=deal, direction=direction).exists():
            raise ValidationError({"deal": _("You have already rated this deal.")})
        try:
            with transaction.atomic():
                rating = DealRating.objects.create(
                    deal=deal,
                    direction=direction,
                    rater_profile_id=rater_profile.pk,
                    rated_profile_id=rated_profile_id,
                    stars=stars,
                    comment=comment or "",
                )
        except IntegrityError as exc:
            raise ValidationError(
                {"deal": _("You have already rated this deal.")}
            ) from exc

        if DealRatingService.is_mutual(deal):
            DealRatingService._emit_mutual_rating_completed(deal, rater_profile)
        return rating

    @staticmethod
    def status(rater_profile, deal_id):
        """Rating state of one deal from the active participant's side."""

        deal = DealRatingService._get_participant_deal(rater_profile, deal_id)
        direction, _rated = DealRatingService._direction_for(deal, rater_profile)
        ratings = {rating.direction: rating for rating in deal.ratings.all()}
        own = ratings.get(direction)
        counterparty = next(
            (rating for key, rating in ratings.items() if key != direction),
            None,
        )
        is_mutual = own is not None and counterparty is not None
        return {
            "deal_id": deal.pk,
            "role": "buyer"
            if direction == DealRatingDirection.BUYER_TO_SELLER
            else "seller",
            "has_rated": own is not None,
            "counterparty_has_rated": counterparty is not None,
            "is_mutual": is_mutual,
            "my_rating": own,
            # Hidden until both have rated, so neither side can retaliate.
            "counterparty_rating": counterparty if is_mutual else None,
        }

    @staticmethod
    def is_mutual(deal):
        return (
            DealRating.objects.filter(deal=deal)
            .values("direction")
            .distinct()
            .count()
            == 2
        )

    @staticmethod
    def _get_participant_deal(rater_profile, deal_id, lock=False):
        queryset = CompletedDeal.objects.all()
        if lock:
            queryset = queryset.select_for_update()
        deal = queryset.filter(pk=deal_id).first()
        if deal is None or rater_profile.pk not in (
            deal.buyer_profile_id,
            deal.seller_profile_id,
        ):
            raise NotFound(_("Completed deal was not found."))
        return deal

    @staticmethod
    def _direction_for(deal, rater_profile):
        if rater_profile.pk == deal.buyer_profile_id:
            return DealRatingDirection.BUYER_TO_SELLER, deal.seller_profile_id
        return DealRatingDirection.SELLER_TO_BUYER, deal.buyer_profile_id

    @staticmethod
    def _emit_mutual_rating_completed(deal, actor_profile):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.MUTUAL_RATING_COMPLETED,
            actor=actor_profile,
            profile=actor_profile,
            context={"deal": deal},
        )
