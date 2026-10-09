from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, Exists, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from apps.auctions.models import (
    AuctionEvent,
    AuctionItem,
    AuctionItemStatus,
    AuctionStatus,
)
from apps.chat.models import Message
from apps.deals.models import CompletedDeal, CompletedDealSourceType
from apps.loyalty.constants import RewardSourceType
from apps.loyalty.models import PointTransaction
from apps.loyalty.services import PointService
from apps.orders.models import OrderEvent, OrderEventType, OrderItem, OrderItemStatus
from apps.ratings.models import DealRating, DealRatingDirection
from apps.seasons.models import (
    DEFAULT_SEASON_PRIZE_POINTS,
    Season,
    SeasonPrize,
    SeasonSellerInsight,
    SeasonStatus,
)
from apps.users.models import SellerProfile

SALES_WEIGHT_PERCENT = 40
RATING_WEIGHT_PERCENT = 30
RELIABILITY_WEIGHT_PERCENT = 20
INTERACTION_WEIGHT_PERCENT = 10
TOTAL_WEIGHT_PERCENT = (
    SALES_WEIGHT_PERCENT
    + RATING_WEIGHT_PERCENT
    + RELIABILITY_WEIGHT_PERCENT
    + INTERACTION_WEIGHT_PERCENT
)

SALES_WEIGHT = Decimal("0.40")
RATING_WEIGHT = Decimal("0.30")
RELIABILITY_WEIGHT = Decimal("0.20")
INTERACTION_WEIGHT = Decimal("0.10")

SCORE_QUANTIZE = Decimal("0.01")
HUNDRED = Decimal("100")
ZERO = Decimal("0.00")
FIVE = Decimal("5")

SEASON_WINNER_POINTS = DEFAULT_SEASON_PRIZE_POINTS

_NON_SELLER_CANCEL_MARKERS = ("admin", "manager", "buyer", "customer")


def _raise_drf_validation_error(exc: DjangoValidationError):
    if hasattr(exc, "message_dict"):
        raise ValidationError(exc.message_dict) from exc
    raise ValidationError({"detail": exc.messages}) from exc


class SeasonPrizeService:
    """Per-season Rank 1..3 prize configuration service (BE-SEA-03).

    New seasons receive default prizes (Rank 1 = 100,000 PP, Rank 2 = 50,000 PP,
    Rank 3 = 25,000 PP). Managers can customize prizes only while
    ``season.status == SeasonStatus.DRAFT`` and ``timezone.now() < season.starts_at``.
    Once a season starts, is calculated, or is finalized, prize configuration is
    frozen.
    """

    DEFAULT_PRIZES = DEFAULT_SEASON_PRIZE_POINTS

    @staticmethod
    def ensure_default_prizes(season, manager_profile=None):
        season.ensure_default_prizes(created_by=manager_profile)
        return SeasonPrizeService.list_prizes(season)

    @staticmethod
    def assert_editable(season):
        try:
            SeasonPrize.validate_season_editable(season)
        except DjangoValidationError as exc:
            _raise_drf_validation_error(exc)

    @staticmethod
    def list_prizes(season):
        return SeasonPrize.objects.filter(
            season=season,
            deleted__isnull=True,
        ).order_by("rank", "id")

    @staticmethod
    @transaction.atomic
    def create_prize(
        season,
        *,
        rank: int,
        pp_points: int,
        symbolic_prize_code: str = "",
        title: str = "",
        metadata=None,
        manager_profile=None,
    ):
        season = Season.objects.select_for_update().get(pk=season.pk)
        SeasonPrizeService.assert_editable(season)

        prize = SeasonPrize(
            season=season,
            rank=rank,
            pp_points=pp_points,
            symbolic_prize_code=symbolic_prize_code or "",
            title=title or "",
            metadata=dict(metadata or {}),
            created_by=manager_profile,
            modified_by=manager_profile,
        )
        try:
            prize.save()
        except DjangoValidationError as exc:
            _raise_drf_validation_error(exc)
        except IntegrityError as exc:
            raise ValidationError(
                {"rank": _("A prize for this rank already exists in this season.")}
            ) from exc
        return prize

    @staticmethod
    @transaction.atomic
    def update_prize(prize, *, manager_profile=None, **changes):
        season = Season.objects.select_for_update().get(pk=prize.season_id)
        SeasonPrizeService.assert_editable(season)

        prize = SeasonPrize.objects.select_for_update().get(
            pk=prize.pk,
            season=season,
            deleted__isnull=True,
        )
        for field in (
            "rank",
            "pp_points",
            "symbolic_prize_code",
            "title",
            "metadata",
        ):
            if field in changes:
                value = changes[field]
                if field in ("symbolic_prize_code", "title") and value is None:
                    value = ""
                if field == "metadata":
                    value = dict(value or {})
                setattr(prize, field, value)

        if manager_profile is not None:
            prize.modified_by = manager_profile

        try:
            prize.save()
        except DjangoValidationError as exc:
            _raise_drf_validation_error(exc)
        except IntegrityError as exc:
            raise ValidationError(
                {"rank": _("A prize for this rank already exists in this season.")}
            ) from exc
        return prize

    @staticmethod
    @transaction.atomic
    def delete_prize(prize, *, manager_profile=None):
        season = Season.objects.select_for_update().get(pk=prize.season_id)
        SeasonPrizeService.assert_editable(season)

        prize = SeasonPrize.objects.select_for_update().get(
            pk=prize.pk,
            season=season,
            deleted__isnull=True,
        )
        if manager_profile is not None:
            prize.modified_by = manager_profile
            prize._allow_system_write = True
            prize.save(update_fields=["modified_by", "modified"])
            prize._allow_system_write = False
        try:
            prize.delete()
        except DjangoValidationError as exc:
            _raise_drf_validation_error(exc)


class SeasonScoreService:
    """Pure seller season scoring service (BE-SEA-01).

    Calculates and persists normalized 0..100 component scores and the weighted
    0..100 composite ``season_score`` for qualifying ``SellerProfile`` rows in a
    season window ``[season.starts_at, season.ends_at)``.

    Invariant: score calculation is side-effect-free with respect to rewards and
    notifications — it never creates ``PointTransaction``, badge, trophy, prize,
    or notification records.
    """

    @staticmethod
    def quantize_score(value: Decimal) -> Decimal:
        return Decimal(value).quantize(SCORE_QUANTIZE, rounding=ROUND_HALF_UP)

    @staticmethod
    def normalize_sales_score(
        completed_sales_count: int,
        season_max_completed_sales: int,
    ) -> Decimal:
        if season_max_completed_sales <= 0 or completed_sales_count <= 0:
            return ZERO
        return (
            Decimal(completed_sales_count)
            / Decimal(season_max_completed_sales)
            * HUNDRED
        )

    @staticmethod
    def normalize_rating_score(
        avg_rating: Decimal,
        ratings_count: int,
    ) -> Decimal:
        if ratings_count <= 0:
            return ZERO
        return (Decimal(avg_rating) / FIVE) * HUNDRED

    @staticmethod
    def normalize_reliability_score(
        completed_sales_count: int,
        seller_cancellations_count: int,
    ) -> Decimal:
        opportunities = int(completed_sales_count) + int(seller_cancellations_count)
        if opportunities <= 0:
            return ZERO
        return (Decimal(completed_sales_count) / Decimal(opportunities)) * HUNDRED

    @staticmethod
    def normalize_interaction_score(
        interaction_replied_count: int,
        interaction_opportunities_count: int,
    ) -> Decimal:
        if interaction_opportunities_count <= 0:
            return ZERO
        return (
            Decimal(interaction_replied_count)
            / Decimal(interaction_opportunities_count)
            * HUNDRED
        )

    @staticmethod
    def compute_weighted_score(
        *,
        sales_score: Decimal,
        rating_score: Decimal,
        reliability_score: Decimal,
        interaction_score: Decimal,
    ) -> Decimal:
        raw_total = (
            Decimal(sales_score) * SALES_WEIGHT
            + Decimal(rating_score) * RATING_WEIGHT
            + Decimal(reliability_score) * RELIABILITY_WEIGHT
            + Decimal(interaction_score) * INTERACTION_WEIGHT
        )
        return SeasonScoreService.quantize_score(raw_total)

    @staticmethod
    @transaction.atomic
    def calculate_season_scores(season, manager_profile=None):
        """Calculate and persist seller scores and rankings for ``season``."""

        season = Season.objects.select_for_update().get(pk=season.pk)
        if season.status == SeasonStatus.FINALIZED:
            raise ValidationError(
                {"detail": _("Finalized seasons cannot be recalculated.")}
            )
        starts_at = season.starts_at
        ends_at = season.ends_at

        deal_rows = list(
            CompletedDeal.objects.filter(
                completed_at__gte=starts_at,
                completed_at__lt=ends_at,
                deleted__isnull=True,
                seller_profile__deleted__isnull=True,
                seller_profile__sellerprofile__isnull=False,
            )
            .values("seller_profile_id")
            .annotate(
                completed_sales_count=Count("id"),
                total_sales_amount=Coalesce(
                    Sum("amount"),
                    Value(ZERO),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
        )

        sales_by_seller = {
            row["seller_profile_id"]: {
                "completed_sales_count": row["completed_sales_count"],
                "total_sales_amount": row["total_sales_amount"] or ZERO,
            }
            for row in deal_rows
            if row["completed_sales_count"] >= 1
        }
        qualifying_seller_ids = sorted(sales_by_seller.keys())

        SeasonSellerInsight.objects.filter(season=season).exclude(
            seller_id__in=qualifying_seller_ids
        ).delete()

        if not qualifying_seller_ids:
            season.status = SeasonStatus.CALCULATED
            season.calculated_at = timezone.now()
            season.save(update_fields=["status", "calculated_at", "modified"])
            return []

        sellers_by_id = {
            seller.pk: seller
            for seller in SellerProfile.objects.filter(
                pk__in=qualifying_seller_ids,
                deleted__isnull=True,
            ).select_related("user")
        }
        qualifying_seller_ids = sorted(sellers_by_id.keys())

        season_max_completed_sales = max(
            (
                sales_by_seller[seller_id]["completed_sales_count"]
                for seller_id in qualifying_seller_ids
            ),
            default=0,
        )

        ratings_by_seller = SeasonScoreService._collect_buyer_ratings(
            qualifying_seller_ids=qualifying_seller_ids,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        cancellations_by_seller = SeasonScoreService._collect_seller_cancellations(
            qualifying_seller_ids=qualifying_seller_ids,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        interactions_by_seller = SeasonScoreService._collect_buyer_interactions(
            qualifying_seller_ids=qualifying_seller_ids,
            starts_at=starts_at,
            ends_at=ends_at,
        )

        scored_entries = []
        for seller_id in qualifying_seller_ids:
            completed_sales_count = sales_by_seller[seller_id]["completed_sales_count"]
            total_sales_amount = sales_by_seller[seller_id]["total_sales_amount"]

            rating_info = ratings_by_seller.get(seller_id, {})
            ratings_count = rating_info.get("ratings_count", 0)
            raw_avg_rating = rating_info.get("avg_rating", ZERO)

            seller_cancellations_count = cancellations_by_seller.get(seller_id, 0)

            interaction_info = interactions_by_seller.get(seller_id, {})
            interaction_opportunities_count = interaction_info.get(
                "opportunities_count", 0
            )
            interaction_replied_count = interaction_info.get("replied_count", 0)

            raw_sales_score = SeasonScoreService.normalize_sales_score(
                completed_sales_count=completed_sales_count,
                season_max_completed_sales=season_max_completed_sales,
            )
            raw_rating_score = SeasonScoreService.normalize_rating_score(
                avg_rating=raw_avg_rating,
                ratings_count=ratings_count,
            )
            raw_reliability_score = SeasonScoreService.normalize_reliability_score(
                completed_sales_count=completed_sales_count,
                seller_cancellations_count=seller_cancellations_count,
            )
            raw_interaction_score = SeasonScoreService.normalize_interaction_score(
                interaction_replied_count=interaction_replied_count,
                interaction_opportunities_count=interaction_opportunities_count,
            )

            season_score = SeasonScoreService.compute_weighted_score(
                sales_score=raw_sales_score,
                rating_score=raw_rating_score,
                reliability_score=raw_reliability_score,
                interaction_score=raw_interaction_score,
            )

            scored_entries.append(
                {
                    "seller": sellers_by_id[seller_id],
                    "seller_id": seller_id,
                    "completed_sales_count": completed_sales_count,
                    "sales_score": SeasonScoreService.quantize_score(raw_sales_score),
                    "ratings_count": ratings_count,
                    "avg_rating": SeasonScoreService.quantize_score(raw_avg_rating),
                    "rating_score": SeasonScoreService.quantize_score(raw_rating_score),
                    "seller_cancellations_count": seller_cancellations_count,
                    "reliability_score": SeasonScoreService.quantize_score(
                        raw_reliability_score
                    ),
                    "interaction_opportunities_count": interaction_opportunities_count,
                    "interaction_replied_count": interaction_replied_count,
                    "interaction_score": SeasonScoreService.quantize_score(
                        raw_interaction_score
                    ),
                    "season_score": season_score,
                    "total_sales_amount": SeasonScoreService.quantize_score(
                        total_sales_amount
                    ),
                }
            )

        # Deterministic ranking: 1. season_score DESC, 2. SellerProfile.id ASC.
        scored_entries.sort(key=lambda entry: (-entry["season_score"], entry["seller_id"]))

        insights = []
        for rank, entry in enumerate(scored_entries, start=1):
            insight, _created = SeasonSellerInsight.all_objects.update_or_create(
                season=season,
                seller=entry["seller"],
                defaults={
                    "completed_sales_count": entry["completed_sales_count"],
                    "sales_score": entry["sales_score"],
                    "ratings_count": entry["ratings_count"],
                    "avg_rating": entry["avg_rating"],
                    "rating_score": entry["rating_score"],
                    "seller_cancellations_count": entry["seller_cancellations_count"],
                    "reliability_score": entry["reliability_score"],
                    "interaction_opportunities_count": entry[
                        "interaction_opportunities_count"
                    ],
                    "interaction_replied_count": entry["interaction_replied_count"],
                    "interaction_score": entry["interaction_score"],
                    "season_score": entry["season_score"],
                    "rank": rank,
                    "completed_orders_count": entry["completed_sales_count"],
                    "total_sales_amount": entry["total_sales_amount"],
                    "points_awarded": 0,
                    "deleted": None,
                    "deleted_by_cascade": False,
                },
            )
            insights.append(insight)

        season.status = SeasonStatus.CALCULATED
        season.calculated_at = timezone.now()
        season.save(update_fields=["status", "calculated_at", "modified"])
        return insights

    @staticmethod
    def calculate_scores(season, manager_profile=None):
        return SeasonScoreService.calculate_season_scores(
            season,
            manager_profile=manager_profile,
        )

    @staticmethod
    def recalculate(season, manager_profile=None):
        return SeasonScoreService.calculate_season_scores(
            season,
            manager_profile=manager_profile,
        )

    @staticmethod
    def insights(season):
        return (
            SeasonSellerInsight.objects.select_related("season", "seller")
            .filter(season=season)
            .order_by("rank", "-season_score", "seller_id")
        )

    @staticmethod
    def _collect_buyer_ratings(*, qualifying_seller_ids, starts_at, ends_at):
        rating_rows = (
            DealRating.objects.filter(
                direction=DealRatingDirection.BUYER_TO_SELLER,
                rated_profile_id__in=qualifying_seller_ids,
                deal__seller_profile_id__in=qualifying_seller_ids,
                deal__completed_at__gte=starts_at,
                deal__completed_at__lt=ends_at,
                deal__deleted__isnull=True,
                created__gte=starts_at,
                created__lt=ends_at,
                deleted__isnull=True,
            )
            .values("rated_profile_id")
            .annotate(
                ratings_count=Count("id"),
                stars_sum=Sum("stars"),
            )
        )
        result = {}
        for row in rating_rows:
            count = row["ratings_count"] or 0
            stars_sum = row["stars_sum"] or 0
            avg_rating = (
                Decimal(stars_sum) / Decimal(count) if count > 0 else ZERO
            )
            result[row["rated_profile_id"]] = {
                "ratings_count": count,
                "avg_rating": avg_rating,
            }
        return result

    @staticmethod
    def _collect_seller_cancellations(*, qualifying_seller_ids, starts_at, ends_at):
        cancellations = defaultdict(int)

        # 1. Market order-item failures attributable to the seller:
        #    (a) explicit seller rejection (OrderItemStatus.REJECTED) in window
        #    (b) seller-timeout auto-cancellation (OrderEventType.SELLER_AUTO_CANCELLED) in window
        market_rows = (
            OrderItem.objects.filter(
                seller_id__in=qualifying_seller_ids,
                deleted__isnull=True,
                order__deleted__isnull=True,
            )
            .exclude(
                Exists(
                    CompletedDeal.objects.filter(
                        source_type=CompletedDealSourceType.ORDER_ITEM,
                        source_id=OuterRef("pk"),
                    )
                )
            )
            .filter(
                Q(
                    status=OrderItemStatus.REJECTED,
                    modified__gte=starts_at,
                    modified__lt=ends_at,
                )
                | Q(
                    Exists(
                        OrderEvent.objects.filter(
                            item_id=OuterRef("pk"),
                            event_type=OrderEventType.SELLER_AUTO_CANCELLED,
                            deleted__isnull=True,
                            created__gte=starts_at,
                            created__lt=ends_at,
                        )
                    ),
                    status=OrderItemStatus.CANCELLED,
                )
            )
            .values("seller_id")
            .annotate(cancelled_count=Count("id", distinct=True))
        )
        for row in market_rows:
            cancellations[row["seller_id"]] += row["cancelled_count"]

        # 2. Seller/owner auction cancellations at AuctionItem business-unit granularity.
        candidate_items = list(
            AuctionItem.objects.filter(
                auction__owner_id__in=qualifying_seller_ids,
                auction__deleted__isnull=True,
                auction__status=AuctionStatus.CANCELLED,
                status=AuctionItemStatus.CANCELLED,
                deleted__isnull=True,
            )
            .exclude(
                Exists(
                    CompletedDeal.objects.filter(
                        source_type=CompletedDealSourceType.AUCTION_ITEM,
                        source_id=OuterRef("pk"),
                    )
                )
            )
            .values(
                "id",
                "auction_id",
                "auction__owner_id",
                "auction__modified",
                "modified",
            )
        )
        if not candidate_items:
            return cancellations

        candidate_auction_ids = sorted({item["auction_id"] for item in candidate_items})
        cancel_events_by_auction = defaultdict(list)
        for event in AuctionEvent.objects.filter(
            auction_id__in=candidate_auction_ids,
            event_type=AuctionItemStatus.CANCELLED,
            deleted__isnull=True,
        ).values("auction_id", "performer_id", "created", "metadata"):
            cancel_events_by_auction[event["auction_id"]].append(event)

        for item in candidate_items:
            auction_id = item["auction_id"]
            owner_id = item["auction__owner_id"]
            events = cancel_events_by_auction.get(auction_id)

            if events:
                if SeasonScoreService._is_seller_auction_cancel_events(
                    events=events,
                    owner_id=owner_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                ):
                    cancellations[owner_id] += 1
            else:
                auction_modified = item["auction__modified"]
                if (
                    auction_modified is not None
                    and starts_at <= auction_modified < ends_at
                ):
                    cancellations[owner_id] += 1

        return cancellations

    @staticmethod
    def _is_seller_auction_cancel_events(*, events, owner_id, starts_at, ends_at):
        has_in_window_seller_event = False
        for event in events:
            performer_id = event["performer_id"]
            if performer_id is not None and performer_id != owner_id:
                return False

            metadata = event.get("metadata") or {}
            reason = str(metadata.get("reason", "")).lower()
            cancelled_by = str(metadata.get("cancelled_by", "")).lower()
            if cancelled_by in _NON_SELLER_CANCEL_MARKERS:
                return False
            if any(
                f"by {marker}" in reason or f"{marker} cancel" in reason
                for marker in _NON_SELLER_CANCEL_MARKERS
            ):
                return False

            created = event["created"]
            if created is not None and starts_at <= created < ends_at:
                has_in_window_seller_event = True

        return has_in_window_seller_event

    @staticmethod
    def _collect_buyer_interactions(*, qualifying_seller_ids, starts_at, ends_at):
        verified_pairs = set(
            CompletedDeal.objects.filter(
                seller_profile_id__in=qualifying_seller_ids,
                completed_at__gte=starts_at,
                completed_at__lt=ends_at,
                deleted__isnull=True,
            )
            .values_list("seller_profile_id", "buyer_user_id")
            .distinct()
        )
        if not verified_pairs:
            return {}

        season_messages = (
            Message.objects.filter(
                conversation__seller_id__in=qualifying_seller_ids,
                conversation__deleted__isnull=True,
                created__gte=starts_at,
                created__lt=ends_at,
                deleted__isnull=True,
            )
            .values(
                "id",
                "created",
                "sender_id",
                "receiver_id",
                "conversation__seller_id",
                "conversation__customer_id",
                "conversation__customer__user_id",
            )
            .order_by("created", "id")
        )

        first_buyer_message = {}
        opportunities_by_seller = defaultdict(set)
        replied_buyers_by_seller = defaultdict(set)

        for msg in season_messages:
            seller_id = msg["conversation__seller_id"]
            customer_id = msg["conversation__customer_id"]
            buyer_user_id = msg["conversation__customer__user_id"]
            pair_key = (seller_id, buyer_user_id)

            if pair_key not in verified_pairs:
                continue

            sender_id = msg["sender_id"]
            receiver_id = msg["receiver_id"]
            msg_created = msg["created"]
            msg_id = msg["id"]

            if sender_id == customer_id and receiver_id == seller_id:
                if pair_key not in first_buyer_message:
                    first_buyer_message[pair_key] = (msg_created, msg_id)
                    opportunities_by_seller[seller_id].add(buyer_user_id)
            elif sender_id == seller_id and receiver_id == customer_id:
                first_msg = first_buyer_message.get(pair_key)
                if first_msg is None:
                    continue
                first_created, first_id = first_msg
                if msg_created > first_created or (
                    msg_created == first_created and msg_id > first_id
                ):
                    replied_buyers_by_seller[seller_id].add(buyer_user_id)

        result = {}
        for seller_id in qualifying_seller_ids:
            result[seller_id] = {
                "opportunities_count": len(opportunities_by_seller[seller_id]),
                "replied_count": len(replied_buyers_by_seller[seller_id]),
            }
        return result


class SeasonPayoutService:
    """Winner PP payout service for season finalization (BE-SEA-02 & BE-SEA-03).

    Reads configured ``SeasonPrize`` rows for Ranks 1..3 (defaulting on season
    creation to 100,000 / 50,000 / 25,000 PP) and awards ``pp_points`` through
    ``PointService.earn_points`` only. Validates that every qualifying Top-3
    winner rank has a configured ``SeasonPrize`` before any payout executes,
    snapshots the winning prize onto ``SeasonSellerInsight.metadata["season_prize"]``,
    uses a rank-independent logical identity ``(season, seller)``, and honors
    any historical ``SEASON_WINNER`` transaction without top-ups, clawbacks, or
    duplicate payouts.
    """

    WINNER_POINTS = SEASON_WINNER_POINTS

    @staticmethod
    def season_winner_idempotency_key(season_id: int, seller_id: int) -> str:
        return f"reward:season_winner:season:{season_id}:seller:{seller_id}"

    @staticmethod
    def get_historical_winner_transactions(season, seller):
        """Return all non-deleted ``SEASON_WINNER`` ledger rows for ``(season, seller)``."""

        season_id_str = str(season.pk)
        canonical_key = SeasonPayoutService.season_winner_idempotency_key(
            season_id=season.pk,
            seller_id=seller.pk,
        )
        return list(
            PointTransaction.objects.filter(
                deleted__isnull=True,
                source_type=RewardSourceType.SEASON_WINNER,
            )
            .filter(
                Q(source_id=season_id_str, profile_id=seller.pk)
                | Q(source_id=season_id_str, user_id=seller.user_id)
                | Q(idempotency_key=canonical_key)
                | Q(idempotency_key__startswith=f"season:{season.pk}:seller:{seller.pk}:")
                | Q(
                    idempotency_key__startswith=(
                        f"legacy:season:{season.pk}:seller:{seller.pk}:"
                    )
                )
                | Q(metadata__season_id=season.pk, profile_id=seller.pk)
                | Q(metadata__season_id=season.pk, user_id=seller.user_id)
            )
            .order_by("id")
        )

    @staticmethod
    def _build_prize_snapshot(prize: SeasonPrize) -> dict:
        return {
            "rank": prize.rank,
            "pp_points": prize.pp_points,
            "symbolic_prize_code": prize.symbolic_prize_code,
            "title": prize.title,
        }

    @staticmethod
    @transaction.atomic
    def payout_season_winners(season, insights=None):
        """Pay eligible Top-3 sellers for ``season`` at most once using configured ``SeasonPrize`` rows."""

        if insights is None:
            insights = list(
                SeasonScoreService.insights(season).select_related(
                    "seller",
                    "seller__user",
                )
            )

        prizes_by_rank = {
            prize.rank: prize
            for prize in SeasonPrize.objects.filter(
                season=season,
                deleted__isnull=True,
            ).order_by("rank", "id")
        }

        top_insights = [
            insight for insight in insights if insight.rank in (1, 2, 3)
        ]
        missing_ranks = [
            insight.rank
            for insight in top_insights
            if insight.rank not in prizes_by_rank
        ]
        if missing_ranks:
            raise ValidationError(
                {
                    "detail": _("Season prize configuration is incomplete."),
                    "missing_ranks": missing_ranks,
                }
            )

        awarded_transactions = []
        for insight in insights:
            seller = insight.seller
            is_top_winner = insight.rank in (1, 2, 3)
            prize = prizes_by_rank.get(insight.rank) if is_top_winner else None

            updated_metadata = dict(insight.metadata or {})
            if prize is not None:
                updated_metadata["season_prize"] = (
                    SeasonPayoutService._build_prize_snapshot(prize)
                )

            historical_txs = SeasonPayoutService.get_historical_winner_transactions(
                season=season,
                seller=seller,
            )
            if len(historical_txs) == 1:
                legacy_points = historical_txs[0].points
                insight.points_awarded = legacy_points
                insight.metadata = updated_metadata
                insight.save(update_fields=["points_awarded", "metadata", "modified"])
                continue
            if len(historical_txs) > 1:
                # Multiple legacy rank-based payouts exist: do not pay again and
                # do not invent a reconciliation value on points_awarded.
                insight.points_awarded = 0
                insight.metadata = updated_metadata
                insight.save(update_fields=["points_awarded", "metadata", "modified"])
                continue

            if not is_top_winner or prize is None:
                if insight.points_awarded != 0:
                    insight.points_awarded = 0
                    insight.save(update_fields=["points_awarded", "modified"])
                continue

            points = int(prize.pp_points)
            if points <= 0:
                insight.points_awarded = 0
                insight.metadata = updated_metadata
                insight.save(update_fields=["points_awarded", "metadata", "modified"])
                continue

            idempotency_key = SeasonPayoutService.season_winner_idempotency_key(
                season_id=season.pk,
                seller_id=seller.pk,
            )
            tx_metadata = {
                "reward_code": str(RewardSourceType.SEASON_WINNER),
                "season_id": season.pk,
                "seller_id": seller.pk,
                "rank": insight.rank,
                "season_score": str(insight.season_score),
            }
            if prize.symbolic_prize_code:
                tx_metadata["symbolic_prize_code"] = prize.symbolic_prize_code
            if prize.title:
                tx_metadata["prize_title"] = prize.title

            try:
                with transaction.atomic():
                    tx = PointService.earn_points(
                        user=seller.user,
                        profile=seller,
                        points=points,
                        reason=f"Season winner rank {insight.rank}",
                        source_type=RewardSourceType.SEASON_WINNER,
                        source_id=str(season.pk),
                        idempotency_key=idempotency_key,
                        metadata=tx_metadata,
                    )
            except IntegrityError:
                tx = PointTransaction.objects.filter(
                    idempotency_key=idempotency_key,
                    deleted__isnull=True,
                ).first()
                if tx is None:
                    raise
            except ValidationError as exc:
                tx = PointTransaction.objects.filter(
                    idempotency_key=idempotency_key,
                    deleted__isnull=True,
                ).first()
                detail = getattr(exc, "detail", None)
                if tx is None or not (
                    isinstance(detail, dict) and "idempotency_key" in detail
                ):
                    raise

            insight.points_awarded = tx.points
            insight.metadata = updated_metadata
            insight.save(update_fields=["points_awarded", "metadata", "modified"])
            SeasonPayoutService._record_prize_award(
                season=season,
                insight=insight,
                seller=seller,
                prize=prize,
                tx=tx,
            )
            awarded_transactions.append(tx)

        return awarded_transactions

    @staticmethod
    def season_prize_award_idempotency_key(season_id: int, seller_id: int) -> str:
        return f"prize:source:season:{season_id}:seller:{seller_id}"

    @staticmethod
    def _record_prize_award(*, season, insight, seller, prize, tx):
        from apps.prizes.models import PrizeAwardSourceType
        from apps.prizes.services import PrizeAwardService

        title = (
            str(prize.title).strip()
            if prize.title
            else f"{season.title} - Rank #{insight.rank}"
        )
        return PrizeAwardService.record_digital_fulfilled(
            user=seller.user,
            profile=seller,
            source_type=PrizeAwardSourceType.SEASON,
            source_id=str(insight.pk),
            title=title,
            description=f"Season winner rank {insight.rank} ({tx.points} PP)",
            prize_snapshot={
                "season_id": season.pk,
                "season_title": season.title,
                "insight_id": insight.pk,
                "seller_id": seller.pk,
                "rank": insight.rank,
                "pp_points": tx.points,
                "symbolic_prize_code": prize.symbolic_prize_code,
                "title": prize.title,
            },
            idempotency_key=(
                SeasonPayoutService.season_prize_award_idempotency_key(
                    season_id=season.pk,
                    seller_id=seller.pk,
                )
            ),
            metadata={
                "season_id": season.pk,
                "insight_id": insight.pk,
                "seller_id": seller.pk,
                "rank": insight.rank,
                "point_transaction_id": tx.pk,
            },
        )


class SeasonService:
    """Orchestrates season scoring (BE-SEA-01), finalization + payouts (BE-SEA-02), and prize configuration (BE-SEA-03)."""

    @staticmethod
    @transaction.atomic
    def create_season(*, manager_profile=None, **data):
        season = Season(**data)
        season._created_by_manager = manager_profile
        try:
            season.save()
        except DjangoValidationError as exc:
            _raise_drf_validation_error(exc)
        return season

    @staticmethod
    def prizes(season):
        return SeasonPrizeService.list_prizes(season)

    @staticmethod
    def create_prize(season, **kwargs):
        return SeasonPrizeService.create_prize(season, **kwargs)

    @staticmethod
    def update_prize(prize, **kwargs):
        return SeasonPrizeService.update_prize(prize, **kwargs)

    @staticmethod
    def delete_prize(prize, **kwargs):
        return SeasonPrizeService.delete_prize(prize, **kwargs)

    @staticmethod
    def calculate_winners(season, manager_profile=None):
        """Calculate and persist seller season scores and rankings without awarding rewards."""

        return SeasonScoreService.calculate_season_scores(
            season,
            manager_profile=manager_profile,
        )

    @staticmethod
    def calculate_scores(season, manager_profile=None):
        return SeasonScoreService.calculate_season_scores(
            season,
            manager_profile=manager_profile,
        )

    @staticmethod
    def recalculate(season, manager_profile=None):
        return SeasonScoreService.calculate_season_scores(
            season,
            manager_profile=manager_profile,
        )

    @staticmethod
    @transaction.atomic
    def finalize(season, manager_profile=None):
        """Lock ``season``, refresh latest scores one final time, pay eligible
        Top-3 winners once from configured ``SeasonPrize`` rows, and freeze the
        season as ``FINALIZED``.
        """

        season = Season.objects.select_for_update().get(pk=season.pk)
        if season.status == SeasonStatus.FINALIZED:
            return season

        now = timezone.now()
        if now < season.ends_at:
            raise ValidationError(
                {"detail": _("Season cannot be finalized before it ends.")}
            )

        SeasonScoreService.calculate_season_scores(
            season,
            manager_profile=manager_profile,
        )
        insights = list(
            SeasonScoreService.insights(season).select_related(
                "seller",
                "seller__user",
            )
        )
        SeasonPayoutService.payout_season_winners(season, insights=insights)

        season.refresh_from_db()
        season.status = SeasonStatus.FINALIZED
        season.finalized_by = manager_profile
        season.finalized_at = now
        season.save(
            update_fields=[
                "status",
                "finalized_by",
                "metadata",
                "modified",
            ]
        )
        return season

    finalize_season = finalize

    @staticmethod
    def insights(season):
        return SeasonScoreService.insights(season)
