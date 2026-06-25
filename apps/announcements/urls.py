from django.urls import path

from apps.announcements.views import AnnouncementViewSet, AdminAnnouncementViewSet

LIST_ACTION = {"get": "list"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
RETRIEVE_ACTION = {"get": "retrieve"}
RETRIEVE_UPDATE_DESTROY_ACTION = {
    "get": "retrieve",
    "patch": "partial_update",
    "delete": "destroy",
}

urlpatterns = [
    path("", AnnouncementViewSet.as_view(LIST_ACTION), name="announcement-list"),
    path(
        "<int:pk>/",
        AnnouncementViewSet.as_view(RETRIEVE_ACTION),
        name="announcement-detail",
    ),
    path(
        "admin/",
        AdminAnnouncementViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-announcement-list",
    ),
    path(
        "admin/<int:pk>/",
        AdminAnnouncementViewSet.as_view(RETRIEVE_UPDATE_DESTROY_ACTION),
        name="admin-announcement-detail",
    ),
]
