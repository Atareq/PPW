from django.urls import path

from apps.notifications.device_views import PushDeviceViewSet
from apps.notifications.views import (
    NotificationMarkAllReadViewSet,
    NotificationMarkReadViewSet,
    NotificationPreferenceViewSet,
    NotificationUnreadCountViewSet,
    NotificationViewSet,
)

LIST_CREATE_ACTION = {"get": "list", "post": "create"}
LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
DESTROY_ACTION = {"delete": "destroy"}
RETRIEVE_PARTIAL_UPDATE_ACTION = {"get": "retrieve", "patch": "partial_update"}

app_name = "notifications"

urlpatterns = [
    path("", NotificationViewSet.as_view(LIST_CREATE_ACTION), name="notification-list"),
    path("preferences/", NotificationPreferenceViewSet.as_view(RETRIEVE_PARTIAL_UPDATE_ACTION), name="notification-preferences"),
    path("unread-count/", NotificationUnreadCountViewSet.as_view(LIST_ACTION), name="notification-unread-count"),
    path("mark-all-read/", NotificationMarkAllReadViewSet.as_view(CREATE_ACTION), name="notification-mark-all-read"),
    path("<int:pk>/mark-read/", NotificationMarkReadViewSet.as_view(CREATE_ACTION), name="notification-mark-read"),
    path("devices/", PushDeviceViewSet.as_view(LIST_CREATE_ACTION), name="push-device-list"),
    path("devices/<int:pk>/", PushDeviceViewSet.as_view(DESTROY_ACTION), name="push-device-detail"),
]

