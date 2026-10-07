from django.urls import path

from apps.draws.views import (
    MyWeeklyDrawEntryViewSet,
    WeeklyDrawAdminViewSet,
    WeeklyDrawCloseViewSet,
    WeeklyDrawEntryAdminViewSet,
    WeeklyDrawFinalizeViewSet,
    WeeklyDrawOpenViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
RETRIEVE_PARTIAL_UPDATE_ACTION = {
    "get": "retrieve",
    "patch": "partial_update",
}

urlpatterns = [
    path(
        "weekly/",
        WeeklyDrawAdminViewSet.as_view(LIST_CREATE_ACTION),
        name="weekly-draw-list",
    ),
    path(
        "weekly/<int:pk>/",
        WeeklyDrawAdminViewSet.as_view(RETRIEVE_PARTIAL_UPDATE_ACTION),
        name="weekly-draw-detail",
    ),
    path(
        "weekly/<int:pk>/open/",
        WeeklyDrawOpenViewSet.as_view(CREATE_ACTION),
        name="weekly-draw-open",
    ),
    path(
        "weekly/<int:pk>/close/",
        WeeklyDrawCloseViewSet.as_view(CREATE_ACTION),
        name="weekly-draw-close",
    ),
    path(
        "weekly/<int:pk>/finalize/",
        WeeklyDrawFinalizeViewSet.as_view(CREATE_ACTION),
        name="weekly-draw-finalize",
    ),
    path(
        "weekly/<int:pk>/entries/",
        WeeklyDrawEntryAdminViewSet.as_view(LIST_ACTION),
        name="weekly-draw-entry-list",
    ),
    path(
        "my-weekly-entry/",
        MyWeeklyDrawEntryViewSet.as_view(LIST_ACTION),
        name="my-weekly-draw-entry",
    ),
]
