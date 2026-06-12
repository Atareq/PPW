from django.urls import path

from apps.chat.views import (
    ConversationMarkReadViewSet,
    ConversationMessageViewSet,
    ConversationViewSet,
)

LIST_CREATE_ACTION = {"get": "list", "post": "create"}
CREATE_ACTION = {"post": "create"}

app_name = "chat"

urlpatterns = [
    path(
        "conversations/",
        ConversationViewSet.as_view(LIST_CREATE_ACTION),
        name="conversation-list-create",
    ),
    path(
        "conversations/<int:pk>/messages/",
        ConversationMessageViewSet.as_view(LIST_CREATE_ACTION),
        name="conversation-message-list-create",
    ),
    path(
        "conversations/<int:pk>/mark-read/",
        ConversationMarkReadViewSet.as_view(CREATE_ACTION),
        name="conversation-mark-read",
    ),
]
