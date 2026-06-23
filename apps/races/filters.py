from django.db.models import Q
import django_filters

from apps.races.models import RaceResult


class RaceResultFilter(django_filters.FilterSet):
    race_id = django_filters.NumberFilter(field_name="race_id")
    bird_ring_number = django_filters.CharFilter(
        field_name="bird_ring_number",
        lookup_expr="icontains",
    )
    competitor_name = django_filters.CharFilter(
        field_name="competitor_name",
        lookup_expr="icontains",
    )
    q = django_filters.CharFilter(method="filter_q")

    class Meta:
        model = RaceResult
        fields = [
            "race_id",
            "bird_ring_number",
            "competitor_name",
        ]

    def filter_q(self, queryset, name, value):
        return queryset.filter(
            Q(bird_ring_number__icontains=value) | Q(competitor_name__icontains=value)
        )
