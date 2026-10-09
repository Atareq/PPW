import logging
import math
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Min, Q
from django.utils import timezone

from apps.loyalty.constants import (
    DEAL_POINTS_RATE_KEY_PREFIX,
    AUCTION_PURCHASE_POINTS,
    AWARD_LAST_MINUTE_POINTS_PER_BID,
    BadgeType,
    FIRST_AUCTION_PUBLISHED_POINTS,
    FIRST_AUCTION_SALE_POINTS,
    FIRST_BID_POINTS,
    FIRST_MARKET_ITEM_LISTED_POINTS,
    FIRST_MARKET_SALE_POINTS,
    FIRST_RATING_POINTS,
    FIRST_SELLER_FOLLOW_POINTS,
    LAST_MINUTE_BADGE_VALID_DAYS,
    LAST_MINUTE_BID_POINTS,
    LAST_MINUTE_BID_WINDOW_MINUTES,
    MARKET_PURCHASE_POINTS,
    TRUSTED_BADGE_VALID_DAYS,
    TRUSTED_BUYER_POINTS,
    TRUSTED_BUYER_PURCHASE_THRESHOLD,
    TRUSTED_SELLER_POINTS,
    TRUSTED_SELLER_SALE_THRESHOLD,
    RewardRuleCode,
    RewardSourceType,
    RewardEventType,
)
from apps.loyalty.services import BadgeService, PointService, RewardRuleService
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.loyalty.validators import PointValidator
from apps.users.constants import ProfileType
from apps.deals.models import CompletedDeal, CompletedDealType
from apps.users.models import Profile

logger = logging.getLogger(__name__)

# Before BE-REW-02 onboarding PP was paid per welcome badge; those ledger rows
# still prove the account was onboarded.
LEGACY_ONBOARDING_KEY_PREFIXES = (
    f"reward:{BadgeType.WELCOME_BUYER}:",
    f"reward:{BadgeType.WELCOME_SELLER}:",
)

# Completed-deal rewards keep the identity they had when payment approval drove
# them, so a deal rewarded before BE-REW-05 is never rewarded again. These are
# identifiers only; the engine no longer reads payment requests.
LEGACY_DEAL_REWARD_SOURCE_TYPE = "PaymentRequest"
LEGACY_DEAL_REWARD_EVENT_TYPES = {
    CompletedDealType.AUCTION: RewardEventType.AUCTION_PAYMENT_REQUEST_APPROVED,
    CompletedDealType.MARKET: RewardEventType.MARKET_PAYMENT_REQUEST_APPROVED,
}
# (badge, points) pairs a completed deal grants to the buyer and, on the
# seller's first sale of that type, to the seller.
DEAL_PURCHASE_REWARDS = {
    CompletedDealType.AUCTION: (
        BadgeType.AUCTION_PURCHASE_CONFIRMED,
        AUCTION_PURCHASE_POINTS,
    ),
    CompletedDealType.MARKET: (
        BadgeType.MARKET_PURCHASE_CONFIRMED,
        MARKET_PURCHASE_POINTS,
    ),
}
DEAL_FIRST_SALE_REWARDS = {
    CompletedDealType.AUCTION: (
        BadgeType.FIRST_AUCTION_SALE_CONFIRMED,
        FIRST_AUCTION_SALE_POINTS,
    ),
    CompletedDealType.MARKET: (
        BadgeType.FIRST_MARKET_SALE_CONFIRMED,
        FIRST_MARKET_SALE_POINTS,
    ),
}


@dataclass(frozen=True)
class RewardSource:
    """Reward identity when the source is not a model instance."""

    source_type: str
    source_id: str


@dataclass
class RewardResult:
    points_awarded: list = field(default_factory=list)
    badges_awarded: list = field(default_factory=list)
    already_awarded: list = field(default_factory=list)
    deferred_tasks: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def as_dict(self):
        return {
            "points_awarded": self.points_awarded,
            "badges_awarded": self.badges_awarded,
            "already_awarded": self.already_awarded,
            "deferred_tasks": self.deferred_tasks,
            "skipped": self.skipped,
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
        elif event_type == RewardEventType.DEAL_COMPLETED:
            cls._handle_deal_completed(context, result, run_async_for_aggregates)
        elif event_type == RewardEventType.MUTUAL_RATING_COMPLETED:
            cls._handle_mutual_rating_completed(context, result)
        elif event_type == RewardEventType.REFERRAL_APPROVED:
            cls._handle_referral_approved(context, result)
        elif event_type == RewardEventType.ACCOUNT_ONBOARDED:
            cls.grant_account_onboarding_reward(profile.user, profile, result)
        elif event_type == RewardEventType.RATING_CREATED:
            cls._handle_rating_created(profile, event_type, context, result)
        elif event_type == RewardEventType.SELLER_FOLLOWED:
            cls._handle_seller_followed(profile, event_type, context, result)

        return result.as_dict()

    @classmethod
    def evaluate_trusted_buyer(cls, profile_id):
        profile = cls._get_profile(profile_id)
        if profile.type != ProfileType.CUSTOMER:
            return RewardResult().as_dict()

        completed_count = CompletedDeal.objects.filter(
            buyer_profile_id=profile.pk,
        ).count()
        result = RewardResult()
        if completed_count >= TRUSTED_BUYER_PURCHASE_THRESHOLD:
            cls._grant_points_and_badge(
                profile,
                BadgeType.TRUSTED_BUYER,
                TRUSTED_BUYER_POINTS,
                "trusted_buyer_aggregate",
                profile,
                result,
                expires_at=timezone.now() + timedelta(days=TRUSTED_BADGE_VALID_DAYS),
            )
        return result.as_dict()

    @classmethod
    def evaluate_trusted_seller(cls, profile_id):
        profile = cls._get_profile(profile_id)
        if profile.type != ProfileType.SELLER:
            return RewardResult().as_dict()

        completed_count = CompletedDeal.objects.filter(
            seller_profile_id=profile.pk,
        ).count()
        result = RewardResult()
        if completed_count >= TRUSTED_SELLER_SALE_THRESHOLD:
            cls._grant_points_and_badge(
                profile,
                BadgeType.TRUSTED_SELLER,
                TRUSTED_SELLER_POINTS,
                "trusted_seller_aggregate",
                profile,
                result,
                expires_at=timezone.now() + timedelta(days=TRUSTED_BADGE_VALID_DAYS),
            )
        return result.as_dict()

    @classmethod
    def grant_account_onboarding_reward(cls, user, profile, result=None):
        """Award the one-time onboarding PP to the account's shared wallet.

        Every mode-creation path may call this. Locking the user's wallet
        serializes concurrent callers, so only the first trigger creates the
        transaction (attributed to its profile) and later calls return it
        unchanged. The unique idempotency key remains the database backstop.
        The amount comes from the ACCOUNT_ONBOARDING rule; while that rule is
        disabled or pays 0 points, nothing is awarded and ``None`` is returned.
        """

        result = result if result is not None else RewardResult()
        PointValidator.validate_profile_belongs_to_user(profile, user)
        rule = RewardRuleService.get_rule(RewardRuleCode.ACCOUNT_ONBOARDING)
        idempotency_key = cls.account_onboarding_idempotency_key(user)
        onboarding_proof = Q(idempotency_key=idempotency_key)
        for prefix in LEGACY_ONBOARDING_KEY_PREFIXES:
            onboarding_proof |= Q(idempotency_key__startswith=prefix)
        with transaction.atomic():
            wallet = PointService.get_or_create_wallet(user)
            PointWallet.objects.select_for_update().get(pk=wallet.pk)
            existing = (
                PointTransaction.objects.filter(user_id=wallet.user_id)
                .filter(onboarding_proof)
                .order_by("created", "pk")
                .first()
            )
            if existing is not None:
                result.already_awarded.append(
                    {
                        "code": str(RewardSourceType.ONBOARDING),
                        "point_transaction_id": existing.pk,
                    }
                )
                return existing
            if not rule.enabled or not rule.points:
                return None
            return cls._grant_direct_points(
                profile=profile,
                points=rule.points,
                reason="Account onboarding reward",
                source_type=RewardSourceType.ONBOARDING,
                source_id=str(wallet.user_id),
                idempotency_key=idempotency_key,
                result=result,
                code=RewardSourceType.ONBOARDING,
            )

    @staticmethod
    def account_onboarding_idempotency_key(user):
        return f"reward:onboarding:user:{getattr(user, 'pk', user)}"

    @classmethod
    def _grant_active_auction_bidder_rewards(cls, bid, result):
        """Pay each distinct bidder account of a busy auction once (points only).

        An auction qualifies when its non-deleted customer bids across all
        items come from at least ``threshold`` distinct accounts. Every such
        account is then paid once per auction, including accounts that join
        later. Already-paid accounts are skipped up front; a duplicate insert
        from a concurrent evaluation hits the unique idempotency key and is
        treated as already paid. Accounts are processed in user-id order so
        concurrent evaluations lock wallets in the same order.
        """

        from apps.auctions.models import Bid

        rule = RewardRuleService.get_rule(RewardRuleCode.ACTIVE_AUCTION_BIDDER)
        if not rule.enabled or not rule.points or rule.threshold is None:
            return

        auction_id = bid.item.auction_id
        bidders = list(
            Bid.objects.filter(
                item__auction_id=auction_id,
                item__deleted__isnull=True,
                bidder__type=ProfileType.CUSTOMER,
            )
            .values("bidder__user_id")
            .annotate(profile_id=Min("bidder_id"))
            .order_by("bidder__user_id")
        )
        if len(bidders) < rule.threshold:
            return

        keys = {
            row["bidder__user_id"]: cls.active_auction_bidder_idempotency_key(
                auction_id,
                row["bidder__user_id"],
            )
            for row in bidders
        }
        paid_keys = cls._paid_keys(keys.values())
        unpaid = [
            row for row in bidders if keys[row["bidder__user_id"]] not in paid_keys
        ]
        profiles = Profile.objects.in_bulk([row["profile_id"] for row in unpaid])
        for row in unpaid:
            user_id = row["bidder__user_id"]
            try:
                with transaction.atomic():
                    PointService.earn_points(
                        user=user_id,
                        profile=profiles[row["profile_id"]],
                        points=rule.points,
                        reason="Active auction bidder reward",
                        source_type=RewardSourceType.ACTIVE_AUCTION_BIDDER,
                        source_id=str(auction_id),
                        idempotency_key=keys[user_id],
                        metadata={
                            "reward_code": str(RewardSourceType.ACTIVE_AUCTION_BIDDER),
                            "auction_id": auction_id,
                            "threshold": rule.threshold,
                        },
                    )
            except IntegrityError:
                continue
            result.points_awarded.append(
                {
                    "code": str(RewardSourceType.ACTIVE_AUCTION_BIDDER),
                    "points": rule.points,
                    "user_id": user_id,
                }
            )

    @staticmethod
    def _paid_keys(keys):
        return set(
            PointTransaction.objects.filter(idempotency_key__in=keys).values_list(
                "idempotency_key",
                flat=True,
            )
        )

    @staticmethod
    def active_auction_bidder_idempotency_key(auction_id, user_id):
        return f"reward:active_auction_bidder:auction:{auction_id}:user:{user_id}"

    @classmethod
    def _handle_welcome_buyer(cls, profile, event_type, result):
        if profile.type != ProfileType.CUSTOMER:
            return
        cls.grant_account_onboarding_reward(profile.user, profile, result)
        cls._award_badge(profile, BadgeType.WELCOME_BUYER, event_type, profile, result)

    @classmethod
    def _handle_welcome_seller(cls, profile, event_type, result):
        if profile.type != ProfileType.SELLER:
            return
        cls.grant_account_onboarding_reward(profile.user, profile, result)
        cls._award_badge(profile, BadgeType.WELCOME_SELLER, event_type, profile, result)

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
        cls._grant_points_and_badge(
            profile,
            BadgeType.FIRST_AUCTION_PUBLISHED,
            FIRST_AUCTION_PUBLISHED_POINTS,
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
        cls._grant_points_and_badge(
            profile,
            BadgeType.FIRST_MARKET_ITEM_LISTED,
            FIRST_MARKET_ITEM_LISTED_POINTS,
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
            cls._grant_points_and_badge(
                profile,
                BadgeType.FIRST_BIDDER,
                FIRST_BID_POINTS,
                event_type,
                bid,
                result,
            )

        if cls._is_last_minute_bid(bid, context):
            active_badge = (
                BadgeService.get_active_badges(profile)
                .filter(badge_type=BadgeType.LAST_MINUTE_FIGHTER)
                .first()
            )
            if AWARD_LAST_MINUTE_POINTS_PER_BID:
                cls._grant_points(
                    profile,
                    BadgeType.LAST_MINUTE_FIGHTER,
                    LAST_MINUTE_BID_POINTS,
                    event_type,
                    bid,
                    result,
                )
            if active_badge is None:
                cls._award_badge(
                    profile,
                    BadgeType.LAST_MINUTE_FIGHTER,
                    event_type,
                    bid,
                    result,
                    expires_at=timezone.now()
                    + timedelta(days=LAST_MINUTE_BADGE_VALID_DAYS),
                    one_per_profile=False,
                )
            else:
                result.already_awarded.append(
                    {
                        "code": BadgeType.LAST_MINUTE_FIGHTER,
                        "badge_id": active_badge.pk,
                    }
                )

        cls._grant_active_auction_bidder_rewards(bid, result)
        cls._grant_referral_first_bid_reward(bid, result)

    @classmethod
    def _grant_referral_first_bid_reward(cls, bid, result):
        """Pay the inviter ``REFERRAL_FIRST_BID`` points for the first bid.

        Qualifies only when the bidder's account was referred, the referral
        was already approved when the bid was placed, and this regular bid is
        the account's earliest bid ever: across all its profiles, auctions,
        and items, soft-deleted bids included, so older bids block it and
        deleting the first bid cannot create a second milestone. Buy-now rows
        (which never emit ``BID_PLACED``) are not bid history. Not
        retroactive. The reward is identified by the referral, not the bid,
        and its payload is the same for any bid, so a concurrent duplicate is
        a no-op. Inviter only; points only.
        """

        from apps.auctions.models import AuctionEvent, Bid
        from apps.referrals.models import Referral, ReferralStatus

        user_id = bid.bidder.user_id
        referral = Referral.objects.filter(
            referred_user_id=user_id,
            status=ReferralStatus.APPROVED,
        ).first()
        if (
            referral is None
            or referral.reviewed_at is None
            or referral.reviewed_at > bid.created
        ):
            return
        buy_now_bids = AuctionEvent.all_objects.filter(
            event_type="buy_now_triggered",
            bid__bidder__user_id=user_id,
        ).values("bid_id")
        earlier_bids = (
            Bid.all_objects.filter(bidder__user_id=user_id)
            .filter(Q(created__lt=bid.created) | Q(created=bid.created, pk__lt=bid.pk))
            .exclude(pk__in=buy_now_bids)
        )
        if earlier_bids.exists():
            return
        rule = RewardRuleService.get_rule(RewardRuleCode.REFERRAL_FIRST_BID)
        if not rule.enabled or not rule.points:
            return
        cls._grant_points_once(
            profile=cls._get_profile(referral.inviter_profile_id),
            points=rule.points,
            reason="Referral first bid reward",
            source_type=RewardSourceType.REFERRAL_FIRST_BID,
            source_id=str(referral.pk),
            idempotency_key=f"reward:referral_first_bid:referral:{referral.pk}:inviter",
            metadata={
                "reward_code": str(RewardSourceType.REFERRAL_FIRST_BID),
                "referral_id": referral.pk,
                "referred_user_id": user_id,
            },
            result=result,
        )

    @classmethod
    def _handle_deal_completed(cls, context, result, run_async_for_aggregates):
        """Grant the current completion rewards for a trusted CompletedDeal.

        ``CompletedDealService`` only records deals whose completion was already
        confirmed, so this handler does not re-check any confirmation workflow.
        """

        deal = cls._context_object(context, "deal", CompletedDeal)
        if deal is None:
            return

        buyer = cls._get_profile(deal.buyer_profile_id)
        seller = cls._get_profile(deal.seller_profile_id)
        source, reward_event = cls._deal_reward_identity(deal)
        purchase_badge, purchase_points = DEAL_PURCHASE_REWARDS[deal.deal_type]
        first_sale_badge, first_sale_points = DEAL_FIRST_SALE_REWARDS[deal.deal_type]
        # Every rewarded deal (before or after BE-REW-08) has this row; if it
        # already exists, the deal was rewarded before and gets no shared deal
        # reward under the current formula (no recalculation or compensation).
        first_processing = not PointTransaction.objects.filter(
            idempotency_key=cls._idempotency_key(
                purchase_badge,
                reward_event,
                source,
                buyer,
            )
        ).exists()

        cls._grant_points_and_badge(
            buyer,
            purchase_badge,
            purchase_points,
            reward_event,
            source,
            result,
        )
        if first_processing:
            cls._grant_completed_deal_points(deal, buyer, seller, result)
            cls._grant_completed_deal_bonus(deal, buyer, seller, result)
            cls._grant_successful_sale_reward(deal, seller, result)
        if deal.deal_type == CompletedDealType.AUCTION:
            cls._grant_auction_completion_extras(buyer, deal, source, result)
        if cls._is_first_sale(deal, seller, first_sale_badge):
            cls._grant_points_and_badge(
                seller,
                first_sale_badge,
                first_sale_points,
                reward_event,
                source,
                result,
            )
        cls._enqueue_deal_aggregates(
            buyer,
            seller,
            result,
            run_async_for_aggregates,
        )

    @staticmethod
    def _deal_reward_identity(deal):
        """Keep the pre-BE-REW-05 key for deals confirmed by a payment request."""

        payment_request_id = deal.metadata.get("payment_request_id")
        if payment_request_id is not None:
            return (
                RewardSource(LEGACY_DEAL_REWARD_SOURCE_TYPE, str(payment_request_id)),
                LEGACY_DEAL_REWARD_EVENT_TYPES[deal.deal_type],
            )
        return (
            RewardSource(deal.source_type, str(deal.source_id)),
            RewardEventType.DEAL_COMPLETED,
        )

    @staticmethod
    def _is_first_sale(deal, seller, badge_type):
        """First recorded deal of this type, unless the seller earned it before.

        Sales approved before CompletedDeal existed have no deal row, so the
        existing first-sale badge is what proves the reward was already paid.
        """

        if (
            CompletedDeal.objects.filter(
                seller_profile_id=seller.pk,
                deal_type=deal.deal_type,
            ).count()
            != 1
        ):
            return False
        return not BadgeAward.objects.filter(
            profile_id=seller.pk,
            badge_type=badge_type,
        ).exists()

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
        cls._grant_points_and_badge(
            profile,
            BadgeType.REVIEWER_BUYER,
            FIRST_RATING_POINTS,
            event_type,
            rating,
            result,
        )

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
        cls._grant_points_and_badge(
            profile,
            BadgeType.LOYAL_FOLLOWER,
            FIRST_SELLER_FOLLOW_POINTS,
            event_type,
            follow,
            result,
        )

    @classmethod
    def _grant_auction_completion_extras(cls, buyer, deal, source, result):
        """Attach wheel and first-bid rewards to a completed auction deal."""

        from apps.auctions.models import AuctionItem
        from apps.lucky_wheel.services import LuckyWheelGrantService

        LuckyWheelGrantService.grant_for_auction_completed(
            buyer,
            AuctionItem.objects.get(pk=deal.source_id),
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
            source_id=source.source_id,
            idempotency_key=f"points:first_bid_completed:user:{buyer.user_id}",
            result=result,
            code=RewardSourceType.FIRST_BID_COMPLETED,
        )

    @classmethod
    def _grant_completed_deal_points(cls, deal, buyer, seller, result):
        """Pay the shared completed-deal reward to both parties of any deal.

        ``floor(amount × rate) × multiplier`` from the ``DEAL_COMPLETED`` rule,
        using the deal's amount and currency snapshot. A currency without a
        configured rate earns nothing (never assumed 1:1) and is logged.
        """

        rule = RewardRuleService.get_rule(RewardRuleCode.DEAL_COMPLETED)
        if not rule.enabled:
            return
        rate = cls._deal_points_rate(rule, deal.currency)
        if rate is None or rule.multiplier is None:
            logger.warning(
                "Completed deal %s earns no deal points: no valid rate for "
                "currency %r or no multiplier.",
                deal.pk,
                deal.currency,
            )
            result.skipped.append(
                {
                    "code": str(RewardSourceType.DEAL_COMPLETED),
                    "reason": "unconfigured_currency",
                    "deal_id": deal.pk,
                    "currency": deal.currency,
                }
            )
            return
        base_points = math.floor(deal.amount * rate)
        # Integer multipliers keep this exact; fractional ones round down.
        deal_points = math.floor(base_points * rule.multiplier)
        if deal_points <= 0:
            return
        for profile, role in ((buyer, "buyer"), (seller, "seller")):
            cls._grant_deal_reward_once(
                profile=profile,
                points=deal_points,
                reason="Completed deal reward",
                source_type=RewardSourceType.DEAL_COMPLETED,
                deal=deal,
                idempotency_key=(
                    f"reward:completed_deal:{deal.source_type}:"
                    f"{deal.source_id}:{role}"
                ),
                metadata={
                    "reward_code": str(RewardSourceType.DEAL_COMPLETED),
                    "deal_id": deal.pk,
                    "deal_type": deal.deal_type,
                    "currency": deal.currency,
                    "rate": str(rate),
                    "base_points": base_points,
                    "multiplier": str(rule.multiplier),
                },
                result=result,
            )

    @classmethod
    def _grant_completed_deal_bonus(cls, deal, buyer, seller, result):
        """Pay the fixed completion bonus (``DEAL_COMPLETION_BONUS``) to both."""

        rule = RewardRuleService.get_rule(RewardRuleCode.DEAL_COMPLETION_BONUS)
        if not rule.enabled or not rule.points:
            return
        for profile, role in ((buyer, "buyer"), (seller, "seller")):
            cls._grant_deal_reward_once(
                profile=profile,
                points=rule.points,
                reason="Completed deal bonus",
                source_type=RewardSourceType.DEAL_COMPLETION_BONUS,
                deal=deal,
                idempotency_key=(
                    f"reward:completed_deal_bonus:{deal.source_type}:"
                    f"{deal.source_id}:{role}"
                ),
                metadata={
                    "reward_code": str(RewardSourceType.DEAL_COMPLETION_BONUS),
                    "deal_id": deal.pk,
                    "deal_type": deal.deal_type,
                },
                result=result,
            )

    @classmethod
    def _handle_referral_approved(cls, context, result):
        """Pay the inviter ``REFERRAL_APPROVED`` points once per referral.

        Emitted by ``ReferralService.approve``. Only approved referrals pay,
        and a referral whose code was already rewarded by the pre-BE-REF-01
        redeem-time reward (legacy ledger key) is treated as paid. Inviter
        only; points only.
        """

        from apps.referrals.models import Referral, ReferralStatus

        referral = cls._context_object(context, "referral", Referral)
        if referral is None or referral.status != ReferralStatus.APPROVED:
            return
        legacy_key = (
            f"points:referral:{referral.code_id}:inviter:{referral.inviter_profile_id}"
        )
        if cls._paid_keys([legacy_key]):
            result.already_awarded.append(
                {
                    "code": str(RewardSourceType.REFERRAL_APPROVED),
                    "idempotency_key": legacy_key,
                }
            )
            return
        rule = RewardRuleService.get_rule(RewardRuleCode.REFERRAL_APPROVED)
        if not rule.enabled or not rule.points:
            return
        cls._grant_points_once(
            profile=cls._get_profile(referral.inviter_profile_id),
            points=rule.points,
            reason="Referral approved reward",
            source_type=RewardSourceType.REFERRAL_APPROVED,
            source_id=str(referral.pk),
            idempotency_key=f"reward:referral_approved:referral:{referral.pk}:inviter",
            metadata={
                "reward_code": str(RewardSourceType.REFERRAL_APPROVED),
                "referral_id": referral.pk,
                "referred_user_id": referral.referred_user_id,
            },
            result=result,
        )

    @classmethod
    def _handle_mutual_rating_completed(cls, context, result):
        """Pay buyer and seller ``MUTUAL_RATING`` points once per deal.

        Emitted by ``DealRatingService`` when the second, reciprocal rating of
        a deal is saved. The pair is re-checked so a stray event pays nothing.
        Points only; independent of the public rating rewards.
        """

        from apps.ratings.services import DealRatingService

        deal = cls._context_object(context, "deal", CompletedDeal)
        if deal is None or not DealRatingService.is_mutual(deal):
            return
        rule = RewardRuleService.get_rule(RewardRuleCode.MUTUAL_RATING)
        if not rule.enabled or not rule.points:
            return
        parties = (
            (cls._get_profile(deal.buyer_profile_id), "buyer"),
            (cls._get_profile(deal.seller_profile_id), "seller"),
        )
        for profile, role in parties:
            cls._grant_deal_reward_once(
                profile=profile,
                points=rule.points,
                reason="Mutual deal rating reward",
                source_type=RewardSourceType.MUTUAL_RATING,
                deal=deal,
                idempotency_key=f"reward:mutual_rating:deal:{deal.pk}:{role}",
                metadata={
                    "reward_code": str(RewardSourceType.MUTUAL_RATING),
                    "deal_id": deal.pk,
                    "deal_type": deal.deal_type,
                },
                result=result,
            )

    @classmethod
    def _grant_successful_sale_reward(cls, deal, seller, result):
        """Pay the seller ``SUCCESSFUL_SALE`` points for every completed deal.

        Seller only and recurring; independent of the one-time first-sale
        incentive and of the completion bonus paid to both parties.
        """

        rule = RewardRuleService.get_rule(RewardRuleCode.SUCCESSFUL_SALE)
        if not rule.enabled or not rule.points:
            return
        cls._grant_deal_reward_once(
            profile=seller,
            points=rule.points,
            reason="Successful sale reward",
            source_type=RewardSourceType.SUCCESSFUL_SALE,
            deal=deal,
            idempotency_key=(
                f"reward:successful_sale:{deal.source_type}:{deal.source_id}:seller"
            ),
            metadata={
                "reward_code": str(RewardSourceType.SUCCESSFUL_SALE),
                "deal_id": deal.pk,
                "deal_type": deal.deal_type,
            },
            result=result,
        )

    @staticmethod
    def _deal_points_rate(rule, currency):
        value = (rule.configuration or {}).get(
            f"{DEAL_POINTS_RATE_KEY_PREFIX}{currency}"
        )
        if value is None or isinstance(value, bool) or not currency:
            return None
        try:
            rate = Decimal(str(value))
        except InvalidOperation:
            return None
        return rate if rate.is_finite() and rate >= 0 else None

    @classmethod
    def _grant_deal_reward_once(
        cls,
        *,
        profile,
        points,
        reason,
        source_type,
        deal,
        idempotency_key,
        metadata,
        result,
    ):
        """Write one deal reward row; an existing or concurrent row is a no-op."""

        cls._grant_points_once(
            profile=profile,
            points=points,
            reason=reason,
            source_type=source_type,
            source_id=str(deal.pk),
            idempotency_key=idempotency_key,
            metadata=metadata,
            result=result,
        )

    @classmethod
    def _grant_points_once(
        cls,
        *,
        profile,
        points,
        reason,
        source_type,
        source_id,
        idempotency_key,
        metadata,
        result,
    ):
        """Write one reward row; an existing or concurrent row is a no-op.

        Reports the outcome from the write itself, not from row timestamps.
        """

        if cls._paid_keys([idempotency_key]):
            result.already_awarded.append(
                {"code": str(source_type), "idempotency_key": idempotency_key}
            )
            return
        try:
            with transaction.atomic():
                PointService.earn_points(
                    user=profile.user,
                    profile=profile,
                    points=points,
                    reason=reason,
                    source_type=source_type,
                    source_id=source_id,
                    idempotency_key=idempotency_key,
                    metadata=metadata,
                )
        except IntegrityError:
            return
        result.points_awarded.append({"code": str(source_type), "points": points})

    @classmethod
    def _grant_points_and_badge(
        cls,
        profile,
        badge_type,
        points,
        event_type,
        source,
        result,
        *,
        expires_at=None,
        badge_one_per_profile=True,
    ):
        """Pay ``points`` and award ``badge_type`` for one event.

        Both are requested explicitly; the badge code only names the shared
        reward identity used by the point and badge idempotency keys.
        """

        point = cls._grant_points(
            profile,
            badge_type,
            points,
            event_type,
            source,
            result,
        )
        award = cls._award_badge(
            profile,
            badge_type,
            event_type,
            source,
            result,
            expires_at=expires_at,
            one_per_profile=badge_one_per_profile,
        )
        return point, award

    @classmethod
    def _award_badge(
        cls,
        profile,
        badge_type,
        event_type,
        source,
        result,
        *,
        expires_at=None,
        one_per_profile=True,
    ):
        """Award a badge only; never creates a point transaction."""

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
            one_per_profile=one_per_profile,
        )
        if (
            award.created == award.modified
            and award.awarded_at >= timezone.now() - timedelta(seconds=5)
        ):
            result.badges_awarded.append({"code": badge_type, "badge_id": award.pk})
        else:
            result.already_awarded.append({"code": badge_type, "badge_id": award.pk})
        return award

    @classmethod
    def _grant_points(cls, profile, reward_code, points, event_type, source, result):
        """Pay points under the ``reward:{code}:{event}:{source}`` identity."""

        transaction_obj = PointService.earn_points(
            user=profile.user,
            profile=profile,
            points=points,
            reason=f"Reward: {reward_code}",
            source_type=cls._source_type(source),
            source_id=cls._source_id(source),
            idempotency_key=cls._idempotency_key(
                reward_code,
                event_type,
                source,
                profile,
            ),
            metadata=cls._metadata(reward_code, event_type),
        )
        if transaction_obj.created == transaction_obj.modified:
            result.points_awarded.append({"code": reward_code, "points": points})
        else:
            result.already_awarded.append(
                {
                    "code": reward_code,
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
    def _enqueue_deal_aggregates(
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
        if isinstance(source, RewardSource):
            return source.source_type
        return source._meta.object_name

    @staticmethod
    def _source_id(source):
        if isinstance(source, RewardSource):
            return source.source_id
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
