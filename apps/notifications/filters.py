import django_filters

from apps.notifications.models import Notification


class NotificationFilter(django_filters.FilterSet):
    type = django_filters.CharFilter(field_name="notification_type")
    is_read = django_filters.BooleanFilter(field_name="is_read")

    class Meta:
        model = Notification
        fields = ["type", "is_read"]
