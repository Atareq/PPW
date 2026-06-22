from django.db import models
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.chat.models import Conversation, Message
from apps.chat.serializers import (
    ConversationListSerializer,
    CreateConversationSerializer,
    MessageListQuerySerializer,
    MessageSerializer,
    SendMessageSerializer,
)
from apps.chat.services import (
    ChatService,
    conversation_queryset,
    message_queryset,
)
from apps.general.authorizations import HasAppAccess


def active_profile(request):
    return request.user.active_profile


@extend_schema_view(
    list=extend_schema(
        tags=["Chat"],
        responses={200: ConversationListSerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Chat"],
        request=CreateConversationSerializer,
        responses={201: ConversationListSerializer},
    ),
)
class ConversationViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Create and list direct conversations for the active profile."""

    permission_classes = [HasAppAccess]
    permission_resource = "CHAT"
    queryset = Conversation.objects.all()
    http_method_names = ["get", "post"]

    def get_serializer_class(self):
        if self.action == "create":
            return CreateConversationSerializer
        return ConversationListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["active_profile"] = active_profile(self.request)
        return context

    def get_queryset(self):
        profile = active_profile(self.request)
        return (
            conversation_queryset()
            .filter(Q(customer_id=profile.pk) | Q(seller_id=profile.pk))
            .annotate(
                latest_at=Coalesce(
                    "last_message_at",
                    "created",
                    output_field=models.DateTimeField(),
                ),
                unread_count_annotation=Count(
                    "messages",
                    filter=Q(
                        messages__receiver_id=profile.pk,
                        messages__is_read=False,
                    ),
                ),
            )
            .order_by("-latest_at", "-created", "-id")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()
        response_serializer = ConversationListSerializer(
            conversation,
            context=self.get_serializer_context(),
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(
        tags=["Chat"],
        parameters=[MessageListQuerySerializer],
        responses={200: MessageSerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Chat"],
        request=SendMessageSerializer,
        responses={201: MessageSerializer},
    ),
)
class ConversationMessageViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """List and send messages in one participant-scoped conversation."""

    permission_classes = [HasAppAccess]
    permission_resource = "CHAT"
    queryset = Message.objects.all()
    http_method_names = ["get", "post"]

    def get_serializer_class(self):
        if self.action == "create":
            return SendMessageSerializer
        return MessageSerializer

    def get_conversation(self):
        return ChatService.get_conversation_for_profile(
            self.kwargs["pk"],
            active_profile(self.request),
        )

    def list(self, request, *args, **kwargs):
        query_serializer = MessageListQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        conversation = self.get_conversation()
        queryset = message_queryset().filter(conversation=conversation)
        after_id = query_serializer.validated_data.get("after_id")
        if after_id:
            queryset = queryset.filter(id__gt=after_id)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = MessageSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = MessageSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = ChatService.send_message(
            self.kwargs["pk"],
            active_profile(request),
            serializer.validated_data["body"],
        )
        return Response(
            MessageSerializer(message).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Chat"],
        request=None,
        responses={200: MessageSerializer(many=True)},
    ),
)
class ConversationMarkReadViewSet(viewsets.GenericViewSet):
    """Mark unread messages for the active profile as read."""

    permission_classes = [HasAppAccess]
    permission_resource = "CHAT"
    queryset = Message.objects.all()
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        ChatService.mark_messages_read(
            kwargs["pk"],
            active_profile(request),
        )
        conversation = ChatService.get_conversation_for_profile(
            kwargs["pk"],
            active_profile(request),
        )
        queryset = message_queryset().filter(conversation=conversation)
        return Response(
            MessageSerializer(queryset, many=True).data,
            status=status.HTTP_200_OK,
        )
