from django.urls import path

from apps.complaints.views import (
    ComplaintAdminActionViewSet,
    ComplaintCancelViewSet,
    ComplaintReopenViewSet,
    ComplaintViewSet,
)

LIST_ACTION = {"get": "list"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
RETRIEVE_ACTION = {"get": "retrieve"}
CREATE_ACTION = {"post": "create"}

app_name = "complaints"

urlpatterns = [
    path(
        "",
        ComplaintViewSet.as_view(LIST_CREATE_ACTION),
        name="complaint-list-create",
    ),
    path(
        "my/",
        ComplaintViewSet.as_view(LIST_ACTION, scope="my"),
        name="my-complaint-list",
    ),
    path(
        "<int:pk>/",
        ComplaintViewSet.as_view(RETRIEVE_ACTION),
        name="complaint-detail",
    ),
    path(
        "<int:pk>/cancel/",
        ComplaintCancelViewSet.as_view(CREATE_ACTION),
        name="complaint-cancel",
    ),
    path(
        "<int:pk>/mark-in-review/",
        ComplaintAdminActionViewSet.as_view(
            CREATE_ACTION,
            action_type="mark_in_review",
        ),
        name="complaint-mark-in-review",
    ),
    path(
        "<int:pk>/resolve/",
        ComplaintAdminActionViewSet.as_view(
            CREATE_ACTION,
            action_type="resolve",
        ),
        name="complaint-resolve",
    ),
    path(
        "<int:pk>/reject/",
        ComplaintAdminActionViewSet.as_view(
            CREATE_ACTION,
            action_type="reject",
        ),
        name="complaint-reject",
    ),
    path(
        "<int:pk>/reopen/",
        ComplaintReopenViewSet.as_view(CREATE_ACTION),
        name="complaint-reopen",
    ),
]
