from django.db.models import Q
import django_filters

from apps.assets.models import Accessory, Feed, Supplement, Supply


class ProductAssetFilter(django_filters.FilterSet):
    min_price = django_filters.NumberFilter(
        field_name="price",
        lookup_expr="gte",
    )
    max_price = django_filters.NumberFilter(
        field_name="price",
        lookup_expr="lte",
    )
    min_count = django_filters.NumberFilter(
        field_name="count",
        lookup_expr="gte",
    )
    max_count = django_filters.NumberFilter(
        field_name="count",
        lookup_expr="lte",
    )
    search = django_filters.CharFilter(method="filter_search")
    q = django_filters.CharFilter(method="filter_search")

    class Meta:
        fields = [
            "status",
            "owner",
            "is_market_listed",
            "is_for_auction",
        ]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(title__icontains=value) | Q(description__icontains=value)
        )


class SupplyFilter(ProductAssetFilter):
    class Meta(ProductAssetFilter.Meta):
        model = Supply


class AccessoryFilter(ProductAssetFilter):
    class Meta(ProductAssetFilter.Meta):
        model = Accessory


class FeedFilter(ProductAssetFilter):
    class Meta(ProductAssetFilter.Meta):
        model = Feed


class SupplementFilter(ProductAssetFilter):
    class Meta(ProductAssetFilter.Meta):
        model = Supplement
