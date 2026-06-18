from django.urls import path

from apps.core.views import NotificationViewSet

LIST_CREATE = {"get": "list", "post": "create"}
MARK_READ = {"post": "mark_read"}
UNREAD_COUNT = {"get": "unread_count"}
DESTROY = {"delete": "destroy"}

app_name = "core"

urlpatterns = [
    path(
        "notifications/",
        NotificationViewSet.as_view(LIST_CREATE),
        name="notification-list",
    ),
    path(
        "notifications/unread-count/",
        NotificationViewSet.as_view(UNREAD_COUNT),
        name="notification-unread-count",
    ),
    path(
        "notifications/<int:pk>/read/",
        NotificationViewSet.as_view(MARK_READ),
        name="notification-mark-read",
    ),
    path(
        "notifications/<int:pk>/",
        NotificationViewSet.as_view(DESTROY),
        name="notification-detail",
    ),
]
