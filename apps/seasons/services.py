from django.db import transaction
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.loyalty.constants import RewardSourceType
from apps.loyalty.services import PointService
from apps.orders.models import OrderItem, OrderItemStatus
from apps.seasons.models import Season, SeasonSellerInsight, SeasonStatus
from apps.users.models import SellerProfile


MINIMUM_COMPLETED_ORDERS = 10
SEASON_WINNER_POINTS = {1: 100000, 2: 50000, 3: 25000}


class SeasonService:
    @staticmethod
    @transaction.atomic
    def calculate_winners(season, manager_profile=None):
        season = Season.objects.select_for_update().get(pk=season.pk)
        seller_rows = (
            SellerProfile.objects.filter(
                sold_order_items__status=OrderItemStatus.COMPLETED,
                sold_order_items__modified__gte=season.starts_at,
                sold_order_items__modified__lt=season.ends_at,
                deleted__isnull=True,
            )
            .annotate(
                completed_orders_count=Count(
                    "sold_order_items",
                    filter=Q(
                        sold_order_items__status=OrderItemStatus.COMPLETED,
                        sold_order_items__modified__gte=season.starts_at,
                        sold_order_items__modified__lt=season.ends_at,
                        sold_order_items__deleted__isnull=True,
                    ),
                ),
                total_sales_amount=Coalesce(
                    Sum(
                        "sold_order_items__total_price",
                        filter=Q(
                            sold_order_items__status=OrderItemStatus.COMPLETED,
                            sold_order_items__modified__gte=season.starts_at,
                            sold_order_items__modified__lt=season.ends_at,
                            sold_order_items__deleted__isnull=True,
                        ),
                    ),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .filter(completed_orders_count__gte=MINIMUM_COMPLETED_ORDERS)
            .order_by(
                "-completed_orders_count",
                "-avg_rating",
                "-total_sales_amount",
                "id",
            )
        )

        insights = []
        seller_ids = list(seller_rows.values_list("id", flat=True))
        SeasonSellerInsight.objects.filter(season=season).exclude(
            seller_id__in=seller_ids
        ).delete()
        for rank, seller in enumerate(seller_rows, start=1):
            points_awarded = SEASON_WINNER_POINTS.get(rank, 0)
            if points_awarded:
                PointService.earn_points(
                    user=seller.user,
                    profile=seller,
                    points=points_awarded,
                    reason=f"Season winner rank {rank}",
                    source_type=RewardSourceType.SEASON_WINNER,
                    source_id=str(season.pk),
                    idempotency_key=(
                        f"season:{season.pk}:seller:{seller.pk}:rank:{rank}"
                    ),
                    metadata={"season_id": season.pk, "rank": rank},
                )
            insight, _ = SeasonSellerInsight.all_objects.update_or_create(
                season=season,
                seller=seller,
                defaults={
                    "completed_orders_count": seller.completed_orders_count,
                    "avg_rating": seller.avg_rating,
                    "total_sales_amount": seller.total_sales_amount or 0,
                    "rank": rank,
                    "points_awarded": points_awarded,
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
    def insights(season):
        return (
            SeasonSellerInsight.objects.select_related("season", "seller")
            .filter(season=season)
            .order_by("rank", "-completed_orders_count", "-avg_rating", "seller_id")
        )
