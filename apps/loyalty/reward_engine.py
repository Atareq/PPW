import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.loyalty.constants import (
    AWARD_LAST_MINUTE_POINTS_PER_BID,
    BadgeType,
    LAST_MINUTE_BADGE_VALID_DAYS,
    LAST_MINUTE_BID_WINDOW_MINUTES,
    REWARD_POINTS,
    TRUSTED_BADGE_VALID_DAYS,
    TRUSTED_BUYER_PURCHASE_THRESHOLD,
    TRUSTED_SELLER_SALE_THRESHOLD,
    RewardSourceType,
    RewardEventType,
)
from apps.loyalty.services import BadgeService, PointService
from apps.loyalty.models import PointTransaction
from apps.users.constants import ProfileType
from apps.users.models import Profile

logger = logging.getLogger(__name__)


@dataclass
class RewardResult:
    points_awarded: list = field(default_factory=list)
    badges_awarded: list = field(default_factory=list)
    already_awarded: list = field(default_factory=list)
    deferred_tasks: list = field(default_factory=list)

    def as_dict(self):
        return {
            "points_awarded": self.points_awarded,
            "badges_awarded": self.badges_awarded,
            "already_awarded": self.already_awarded,
            "deferred_tasks": self.deferred_tasks,
        }


class RewardEngine:
    """Synchronous reward dispatcher plus small async aggregate entry points."""

    @classmethod
    def handle_event(
        cls,
        event_type,
        actor,
        profile=None,
        context=None,
        run_async_for_aggregates=True,
    ):
        context = context or {}
        profile = cls._resolve_profile(profile or actor)
        result = RewardResult()

        if event_type == RewardEventType.CUSTOMER_PROFILE_CREATED:
            cls._handle_welcome_buyer(profile, event_type, result)
        elif event_type == RewardEventType.SELLER_PROFILE_CREATED:
            cls._handle_welcome_seller(profile, event_type, result)
        elif event_type == RewardEventType.AUCTION_PUBLISHED:
            cls._handle_auction_published(profile, event_type, context, result)
        elif event_type == RewardEventType.MARKET_ITEM_LISTED:
            cls._handle_market_item_listed(profile, event_type, context, result)
        elif event_type == RewardEventType.BID_PLACED:
            cls._handle_bid_placed(profile, event_type, context, result)
        elif event_type == RewardEventType.AUCTION_PAYMENT_REQUEST_APPROVED:
            cls._handle_auction_payment_approved(
                profile,
                event_type,
                context,
                result,
                run_async_for_aggregates,
            )
        elif event_type == RewardEventType.MARKET_PAYMENT_REQUEST_APPROVED:
            cls._handle_market_payment_approved(
                profile,
                event_type,
                context,
                result,
                run_async_for_aggregates,
            )
        elif event_type == RewardEventType.RATING_CREATED:
            cls._handle_rating_created(profile, event_type, context, result)
        elif event_type == RewardEventType.SELLER_FOLLOWED:
            cls._handle_seller_followed(profile, event_type, context, result)

        return result.as_dict()

    @classmethod
    def evaluate_trusted_buyer(cls, profile_id):
        from apps.payments.models import PaymentRequest, PaymentRequestStatus

        profile = cls._get_profile(profile_id)
        if profile.type != ProfileType.CUSTOMER:
            return RewardResult().as_dict()

        completed_count = PaymentRequest.objects.filter(
            buyer_id=profile.pk,
            status=PaymentRequestStatus.APPROVED,
            deleted__isnull=True,
        ).count()
        result = RewardResult()
        if completed_count >= TRUSTED_BUYER_PURCHASE_THRESHOLD:
            cls._grant_reward(
                profile=profile,
                badge_type=BadgeType.TRUSTED_BUYER,
                event_type="trusted_buyer_aggregate",
                source=profile,
                result=result,
                expires_at=timezone.now() + timedelta(days=TRUSTED_BADGE_VALID_DAYS),
            )
        return result.as_dict()

    @classmethod
    def evaluate_trusted_seller(cls, profile_id):
        from apps.payments.models import PaymentRequest, PaymentRequestStatus

        profile = cls._get_profile(profile_id)
        if profile.type != ProfileType.SELLER:
            return RewardResult().as_dict()

        completed_count = PaymentRequest.objects.filter(
            seller_id=profile.pk,
            status=PaymentRequestStatus.APPROVED,
            deleted__isnull=True,
        ).count()
        result = RewardResult()
        if completed_count >= TRUSTED_SELLER_SALE_THRESHOLD:
            cls._grant_reward(
                profile=profile,
                badge_type=BadgeType.TRUSTED_SELLER,
                event_type="trusted_seller_aggregate",
                source=profile,
                result=result,
                expires_at=timezone.now() + timedelta(days=TRUSTED_BADGE_VALID_DAYS),
            )
        return result.as_dict()

    @classmethod
    def _handle_welcome_buyer(cls, profile, event_type, result):
        if profile.type != ProfileType.CUSTOMER:
            return
        cls._grant_reward(profile, BadgeType.WELCOME_BUYER, event_type, profile, result)

    @classmethod
    def _handle_welcome_seller(cls, profile, event_type, result):
        if profile.type != ProfileType.SELLER:
            return
        cls._grant_reward(
            profile, BadgeType.WELCOME_SELLER, event_type, profile, result
        )

    @classmethod
    def _handle_auction_published(cls, profile, event_type, context, result):
        """Award posting points on every auction and first-auction badge once."""

        from apps.auctions.models import Auction

        auction = cls._context_object(context, "auction", Auction)
        if profile.type != ProfileType.SELLER or auction is None:
            return
        cls._grant_direct_points(
            profile=profile,
            points=50,
            reason="Auction posted",
            source_type=RewardSourceType.AUCTION_POSTED,
            source_id=str(auction.pk),
            idempotency_key=(f"points:auction_posted:{auction.pk}:seller:{profile.pk}"),
            result=result,
            code=RewardSourceType.AUCTION_POSTED,
        )
        if (
            Auction.objects.filter(owner_id=profile.pk, deleted__isnull=True).count()
            != 1
        ):
            return
        cls._grant_reward(
            profile,
            BadgeType.FIRST_AUCTION_PUBLISHED,
            event_type,
            auction,
            result,
        )

    @classmethod
    def _handle_market_item_listed(cls, profile, event_type, context, result):
        from apps.assets.models import Asset, AssetCategory

        asset = cls._context_object(context, "asset", Asset)
        if profile.type != ProfileType.SELLER or asset is None:
            return
        if not asset.is_market_listed:
            return
        product_categories = {
            AssetCategory.SUPPLY,
            AssetCategory.ACCESSORY,
            AssetCategory.FEED,
            AssetCategory.SUPPLEMENT,
        }
        if asset.category not in product_categories:
            return
        listed_count = Asset.objects.filter(
            owner_id=profile.pk,
            category__in=product_categories,
            is_market_listed=True,
            deleted__isnull=True,
        ).count()
        if listed_count != 1:
            return
        cls._grant_reward(
            profile,
            BadgeType.FIRST_MARKET_ITEM_LISTED,
            event_type,
            asset,
            result,
        )

    @classmethod
    def _handle_bid_placed(cls, profile, event_type, context, result):
        from apps.auctions.models import Bid

        bid = cls._context_object(context, "bid", Bid)
        if profile.type != ProfileType.CUSTOMER or bid is None:
            return

        prior_bids = Bid.objects.filter(
            bidder_id=profile.pk,
            deleted__isnull=True,
        ).exclude(pk=bid.pk)
        if not prior_bids.exists():
            cls._grant_reward(profile, BadgeType.FIRST_BIDDER, event_type, bid, result)

        if cls._is_last_minute_bid(bid, context):
            active_badge = (
                BadgeService.get_active_badges(profile)
                .filter(badge_type=BadgeType.LAST_MINUTE_FIGHTER)
                .first()
            )
            if active_badge is None:
                cls._grant_reward(
                    profile,
                    BadgeType.LAST_MINUTE_FIGHTER,
                    event_type,
                    bid,
                    result,
                    expires_at=timezone.now()
                    + timedelta(days=LAST_MINUTE_BADGE_VALID_DAYS),
                    badge_one_per_profile=False,
                    award_points=AWARD_LAST_MINUTE_POINTS_PER_BID,
                )
            elif AWARD_LAST_MINUTE_POINTS_PER_BID:
                cls._grant_points(
                    profile,
                    BadgeType.LAST_MINUTE_FIGHTER,
                    event_type,
                    bid,
                    result,
                )
                result.already_awarded.append(
                    {
                        "code": BadgeType.LAST_MINUTE_FIGHTER,
                        "badge_id": active_badge.pk,
                    }
                )

    @classmethod
    def _handle_auction_payment_approved(
        cls,
        profile,
        event_type,
        context,
        result,
        run_async_for_aggregates,
    ):
        from apps.payments.models import (
            PaymentRequest,
            PaymentRequestStatus,
            PaymentRequestType,
        )

        payment_request = cls._context_object(
            context,
            "payment_request",
            PaymentRequest,
        )
        if (
            payment_request is None
            or payment_request.request_type != PaymentRequestType.AUCTION
            or payment_request.status != PaymentRequestStatus.APPROVED
        ):
            return

        buyer = cls._get_profile(payment_request.buyer_id)
        seller = cls._get_profile(payment_request.seller_id)
        cls._grant_reward(
            buyer,
            BadgeType.AUCTION_PURCHASE_CONFIRMED,
            event_type,
            payment_request,
            result,
        )
        cls._grant_auction_completion_extras(buyer, payment_request, result)
        approved_seller_count = PaymentRequest.objects.filter(
            seller_id=seller.pk,
            request_type=PaymentRequestType.AUCTION,
            status=PaymentRequestStatus.APPROVED,
            deleted__isnull=True,
        ).count()
        if approved_seller_count == 1:
            cls._grant_reward(
                seller,
                BadgeType.FIRST_AUCTION_SALE_CONFIRMED,
                event_type,
                payment_request,
                result,
            )
        cls._enqueue_payment_aggregates(
            buyer,
            seller,
            result,
            run_async_for_aggregates,
        )

    @classmethod
    def _handle_market_payment_approved(
        cls,
        profile,
        event_type,
        context,
        result,
        run_async_for_aggregates,
    ):
        from apps.payments.models import (
            PaymentRequest,
            PaymentRequestStatus,
            PaymentRequestType,
        )

        payment_request = cls._context_object(
            context,
            "payment_request",
            PaymentRequest,
        )
        if (
            payment_request is None
            or payment_request.request_type != PaymentRequestType.MARKET
            or payment_request.status != PaymentRequestStatus.APPROVED
        ):
            return

        buyer = cls._get_profile(payment_request.buyer_id)
        seller = cls._get_profile(payment_request.seller_id)
        cls._grant_reward(
            buyer,
            BadgeType.MARKET_PURCHASE_CONFIRMED,
            event_type,
            payment_request,
            result,
        )
        cls._grant_order_completion_points(payment_request, buyer, seller, result)
        approved_seller_count = PaymentRequest.objects.filter(
            seller_id=seller.pk,
            request_type=PaymentRequestType.MARKET,
            status=PaymentRequestStatus.APPROVED,
            deleted__isnull=True,
        ).count()
        if approved_seller_count == 1:
            cls._grant_reward(
                seller,
                BadgeType.FIRST_MARKET_SALE_CONFIRMED,
                event_type,
                payment_request,
                result,
            )
        cls._enqueue_payment_aggregates(
            buyer,
            seller,
            result,
            run_async_for_aggregates,
        )

    @classmethod
    def _handle_rating_created(cls, profile, event_type, context, result):
        from apps.ratings.models import AssetRating, SellerRating

        rating = context.get("rating")
        if profile.type != ProfileType.CUSTOMER or rating is None:
            return
        rated_profile = None
        if isinstance(rating, AssetRating):
            rated_profile = rating.asset.owner
        elif isinstance(rating, SellerRating):
            rated_profile = rating.seller
        if rated_profile is not None:
            cls._grant_direct_points(
                profile=rated_profile,
                points=30,
                reason="Rating received",
                source_type=RewardSourceType.RATING_RECEIVED,
                source_id=str(rating.pk),
                idempotency_key=(
                    f"points:rating_received:{rating.pk}:" f"profile:{rated_profile.pk}"
                ),
                result=result,
                code=RewardSourceType.RATING_RECEIVED,
            )
        rating_count = (
            AssetRating.objects.filter(
                owner_id=profile.pk, deleted__isnull=True
            ).count()
            + SellerRating.objects.filter(
                owner_id=profile.pk, deleted__isnull=True
            ).count()
        )
        if rating_count != 1:
            return
        cls._grant_reward(profile, BadgeType.REVIEWER_BUYER, event_type, rating, result)

    @classmethod
    def _handle_seller_followed(cls, profile, event_type, context, result):
        from apps.feed.models import SellerFollow

        follow = cls._context_object(context, "follow", SellerFollow)
        if profile.type != ProfileType.CUSTOMER or follow is None:
            return
        active_follow_count = SellerFollow.objects.filter(
            follower_id=profile.pk,
            is_active=True,
            deleted__isnull=True,
        ).count()
        if active_follow_count != 1:
            return
        cls._grant_reward(profile, BadgeType.LOYAL_FOLLOWER, event_type, follow, result)

    @classmethod
    def _grant_auction_completion_extras(cls, buyer, payment_request, result):
        """Attach wheel and first-bid rewards to trusted auction sale completion."""

        auction_request = getattr(payment_request, "auctionpaymentrequest", None)
        if auction_request is None:
            return

        from apps.lucky_wheel.services import LuckyWheelGrantService

        LuckyWheelGrantService.grant_for_auction_completed(
            buyer,
            auction_request.auction_item,
        )

        if PointTransaction.objects.filter(
            user=buyer.user,
            source_type=RewardSourceType.FIRST_BID_COMPLETED,
            deleted__isnull=True,
        ).exists():
            return
        cls._grant_direct_points(
            profile=buyer,
            points=200,
            reason="First successful auction purchase after bidding",
            source_type=RewardSourceType.FIRST_BID_COMPLETED,
            source_id=str(payment_request.pk),
            idempotency_key=f"points:first_bid_completed:user:{buyer.user_id}",
            result=result,
            code=RewardSourceType.FIRST_BID_COMPLETED,
        )

    @classmethod
    def _grant_order_completion_points(cls, payment_request, buyer, seller, result):
        """Award both sides once when a market payment approval completes an item."""

        market_request = getattr(payment_request, "marketpaymentrequest", None)
        if market_request is None:
            return
        item = market_request.order_item
        amount_points = int(item.total_price)
        for profile, role in ((buyer, "customer"), (seller, "seller")):
            if amount_points > 0:
                cls._grant_direct_points(
                    profile=profile,
                    points=amount_points,
                    reason="Order item completed amount reward",
                    source_type=RewardSourceType.ORDER_COMPLETED_AMOUNT,
                    source_id=str(item.pk),
                    idempotency_key=(f"points:order_item:{item.pk}:{role}:amount"),
                    result=result,
                    code=RewardSourceType.ORDER_COMPLETED_AMOUNT,
                )
            cls._grant_direct_points(
                profile=profile,
                points=100,
                reason="Order item completed bonus reward",
                source_type=RewardSourceType.ORDER_COMPLETED_BONUS,
                source_id=str(item.pk),
                idempotency_key=f"points:order_item:{item.pk}:{role}:bonus",
                result=result,
                code=RewardSourceType.ORDER_COMPLETED_BONUS,
            )

    @classmethod
    def _grant_reward(
        cls,
        profile,
        badge_type,
        event_type,
        source,
        result,
        *,
        expires_at=None,
        badge_one_per_profile=True,
        award_points=True,
    ):
        point = None
        if award_points:
            point = cls._grant_points(profile, badge_type, event_type, source, result)

        award = BadgeService.award_badge(
            profile=profile,
            badge_type=badge_type,
            source_type=cls._source_type(source),
            source_id=cls._source_id(source),
            idempotency_key=cls._idempotency_key(
                badge_type,
                event_type,
                source,
                profile,
            ),
            expires_at=expires_at,
            metadata=cls._metadata(badge_type, event_type),
            one_per_profile=badge_one_per_profile,
        )
        if (
            award.created == award.modified
            and award.awarded_at >= timezone.now() - timedelta(seconds=5)
        ):
            result.badges_awarded.append({"code": badge_type, "badge_id": award.pk})
        else:
            result.already_awarded.append({"code": badge_type, "badge_id": award.pk})
        return point, award

    @classmethod
    def _grant_points(cls, profile, badge_type, event_type, source, result):
        points = REWARD_POINTS[badge_type]
        transaction_obj = PointService.earn_points(
            user=profile.user,
            profile=profile,
            points=points,
            reason=f"Reward: {badge_type}",
            source_type=cls._source_type(source),
            source_id=cls._source_id(source),
            idempotency_key=cls._idempotency_key(
                badge_type,
                event_type,
                source,
                profile,
            ),
            metadata=cls._metadata(badge_type, event_type),
        )
        if transaction_obj.created == transaction_obj.modified:
            result.points_awarded.append({"code": badge_type, "points": points})
        else:
            result.already_awarded.append(
                {
                    "code": badge_type,
                    "point_transaction_id": transaction_obj.pk,
                }
            )
        return transaction_obj

    @classmethod
    def _grant_direct_points(
        cls,
        *,
        profile,
        points,
        reason,
        source_type,
        source_id,
        idempotency_key,
        result,
        code,
    ):
        """Create non-badge point rewards through the same idempotent wallet path."""

        transaction_obj = PointService.earn_points(
            user=profile.user,
            profile=profile,
            points=points,
            reason=reason,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            metadata={"reward_code": str(code)},
        )
        if transaction_obj.created == transaction_obj.modified:
            result.points_awarded.append({"code": str(code), "points": points})
        else:
            result.already_awarded.append(
                {
                    "code": str(code),
                    "point_transaction_id": transaction_obj.pk,
                }
            )
        return transaction_obj

    @staticmethod
    def _is_last_minute_bid(bid, context):
        if "was_last_minute" in context:
            return bool(context["was_last_minute"])
        auction = bid.item.auction
        remaining = auction.end_time - bid.created
        return (
            timedelta(0)
            < remaining
            <= timedelta(minutes=LAST_MINUTE_BID_WINDOW_MINUTES)
        )

    @classmethod
    def _enqueue_payment_aggregates(
        cls,
        buyer,
        seller,
        result,
        run_async_for_aggregates,
    ):
        if not run_async_for_aggregates:
            return

        def enqueue():
            from apps.loyalty.tasks import (
                evaluate_trusted_buyer,
                evaluate_trusted_seller,
            )

            try:
                evaluate_trusted_buyer.delay(buyer.pk)
                evaluate_trusted_seller.delay(seller.pk)
            except Exception:
                logger.exception("Failed to enqueue loyalty aggregate tasks.")

        transaction.on_commit(enqueue)
        result.deferred_tasks.extend(
            [
                "loyalty.tasks.evaluate_trusted_buyer",
                "loyalty.tasks.evaluate_trusted_seller",
            ]
        )

    @staticmethod
    def _resolve_profile(profile):
        if isinstance(profile, Profile):
            return profile
        if hasattr(profile, "active_profile"):
            return profile.active_profile
        return RewardEngine._get_profile(profile)

    @staticmethod
    def _get_profile(profile_id):
        return Profile.objects.select_related("user").get(pk=profile_id)

    @staticmethod
    def _context_object(context, key, model_class):
        obj = context.get(key)
        if obj is None:
            obj_id = context.get(f"{key}_id")
            if obj_id is None:
                return None
            return model_class.objects.get(pk=obj_id)
        return obj

    @staticmethod
    def _source_type(source):
        return source._meta.object_name

    @staticmethod
    def _source_id(source):
        return str(source.pk)

    @classmethod
    def _idempotency_key(cls, badge_type, event_type, source, profile):
        return (
            f"reward:{badge_type}:{event_type}:"
            f"{cls._source_type(source)}:{cls._source_id(source)}:"
            f"profile:{profile.pk}"
        )

    @staticmethod
    def _metadata(badge_type, event_type):
        return {
            "reward_code": str(badge_type),
            "event_type": str(event_type),
        }
