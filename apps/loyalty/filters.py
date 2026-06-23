import django_filters

from apps.loyalty.models import PointTransaction


class PointTransactionFilter(django_filters.FilterSet):
    user_id = django_filters.NumberFilter(field_name="user_id")
    profile_id = django_filters.NumberFilter(field_name="profile_id")
    transaction_type = django_filters.CharFilter(field_name="transaction_type")
    source_type = django_filters.CharFilter(field_name="source_type")

    class Meta:
        model = PointTransaction
        fields = [
            "user_id",
            "profile_id",
            "transaction_type",
            "source_type",
        ]
