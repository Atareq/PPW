from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from safedelete.config import HARD_DELETE

from apps.core.models import Notification
from apps.core.serializers import (
    NotificationCreateSerializer,
    NotificationSerializer,
    UnreadCountSerializer,
)
from apps.core.services import NotificationService
from apps.general.authorizations import HasAppAccess
from apps.users.constants import ProfileType
from apps.users.models import Profile


def _parse_bool(value):
    if value is None:
        return None
    return value.lower() == "true"


@extend_schema_view(
    list=extend_schema(tags=["Core Notifications"]),
    create=extend_schema(
        tags=["Core Notifications"],
        request=NotificationCreateSerializer,
        responses={201: NotificationSerializer},
    ),
    destroy=extend_schema(tags=["Core Notifications"]),
)
class NotificationViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "NOTIFICATION"
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()

    def get_queryset(self):
        active_profile = self.request.user.active_profile
        qs = Notification.objects.select_related("profile")

        if active_profile.type == ProfileType.MANAGER:
            profile_id = self.request.query_params.get("profile_id")
            kind = self.request.query_params.get("kind")
            is_read = _parse_bool(self.request.query_params.get("is_read"))
            if profile_id:
                qs = qs.filter(profile_id=profile_id)
            if kind:
                qs = qs.filter(kind=kind)
            if is_read is not None:
                qs = qs.filter(is_read=is_read)
            return qs

        is_read = _parse_bool(self.request.query_params.get("is_read"))
        qs = qs.filter(profile=active_profile)
        if is_read is not None:
            qs = qs.filter(is_read=is_read)
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return NotificationCreateSerializer
        return NotificationSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = NotificationSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = NotificationSerializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = NotificationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        profile = Profile.objects.filter(pk=data["profile_id"]).first()
        if profile is None:
            raise NotFound("Profile not found.")
        notification = NotificationService.create(
            profile=profile,
            kind=data["kind"],
            title=data["title"],
            body=data.get("body", ""),
            metadata=data.get("metadata") or {},
            profile_type=data.get("profile_type", "All"),
        )
        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Core Notifications"],
        request=None,
        responses={200: NotificationSerializer},
    )
    def mark_read(self, request, pk=None, **kwargs):
        try:
            notification = NotificationService.mark_read(
                pk,
                request.user.active_profile,
            )
        except Notification.DoesNotExist as exc:
            raise NotFound("Notification not found.") from exc
        return Response(NotificationSerializer(notification).data)

    @extend_schema(
        tags=["Core Notifications"],
        responses={200: UnreadCountSerializer},
    )
    def unread_count(self, request, **kwargs):
        count = NotificationService.unread_count(request.user.active_profile)
        return Response({"count": count})

    def destroy(self, request, pk=None, **kwargs):
        self.get_object().delete(force_policy=HARD_DELETE)
        return Response(status=status.HTTP_204_NO_CONTENT)
