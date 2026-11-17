from django.urls import path

from apps.general.views import (
    AdminAnnouncementViewSet,
    AdminAppSettingsViewSet,
    AnnouncementDismissViewSet,
    AppSettingsViewSet,
    CurrentAnnouncementViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
DETAIL_UPDATE_DESTROY_ACTION = {
    "get": "retrieve",
    "patch": "partial_update",
    "delete": "destroy",
}
RETRIEVE_ACTION = {"get": "retrieve"}
RETRIEVE_PARTIAL_UPDATE_ACTION = {"get": "retrieve", "patch": "partial_update"}

urlpatterns = [
    path(
        "current/",
        CurrentAnnouncementViewSet.as_view(LIST_ACTION),
        name="current-announcement-list",
    ),
    path(
        "<int:pk>/dismiss/",
        AnnouncementDismissViewSet.as_view(CREATE_ACTION),
        name="announcement-dismiss",
    ),
    path(
        "admin/",
        AdminAnnouncementViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-announcement-list",
    ),
    path(
        "admin/<int:pk>/",
        AdminAnnouncementViewSet.as_view(DETAIL_UPDATE_DESTROY_ACTION),
        name="admin-announcement-detail",
    ),
]


# Mounted at /api/app-settings/ from ppw/urls.py.
app_settings_urlpatterns = [
    path(
        "",
        AppSettingsViewSet.as_view(RETRIEVE_ACTION),
        name="app-settings",
    ),
    path(
        "admin/",
        AdminAppSettingsViewSet.as_view(RETRIEVE_PARTIAL_UPDATE_ACTION),
        name="admin-app-settings",
    ),
]
