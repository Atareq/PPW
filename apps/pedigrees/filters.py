import django_filters
from django.db.models import Q

from apps.pedigrees.models import PedigreeBird, normalize_ring_number


class PedigreeBirdFilter(django_filters.FilterSet):
    ring_number = django_filters.CharFilter(method="filter_ring_number")
    ring_code = django_filters.CharFilter(
        field_name="ring_code", lookup_expr="icontains"
    )
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = PedigreeBird
        fields = [
            "ring_number",
            "ring_code",
            "name",
            "search",
            "sex",
            "colour",
            "life_status",
            "ownership_status",
            "activity_status",
            "job",
            "father",
            "mother",
        ]

    def filter_ring_number(self, queryset, name, value):
        return queryset.filter(
            normalized_ring_number__icontains=normalize_ring_number(value)
        )

    def filter_search(self, queryset, name, value):
        normalized = normalize_ring_number(value)
        return queryset.filter(
            Q(normalized_ring_number__icontains=normalized)
            | Q(ring_code__icontains=value)
            | Q(name__icontains=value)
            | Q(description__icontains=value)
        )
