from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.utils.translation import gettext_lazy as _
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.notifications.filters import NotificationFilter
from apps.notifications.models import (
    Notification,
    NotificationPreference,
    NotificationType,
)
from apps.notifications.serializers import (
    ManualNotificationCreateSerializer,
    MarkAllReadSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    UnreadCountSerializer,
)
from apps.notifications.services import (
    NotificationPreferenceService,
    NotificationService,
    notification_queryset,
)
from apps.users.constants import ProfileType
from apps.users.models import Profile


def active_profile(request):
    return request.user.active_profile


@extend_schema_view(
    list=extend_schema(
        tags=["Notifications"],
        responses={200: NotificationSerializer(many=True)},
    ),
)
class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "NOTIFICATION"
    manager_permission_map = {
        "list": "notifications.list",
        "create": "notifications.create",
    }
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = NotificationFilter
    http_method_names = ["get", "post"]

    def get_serializer_class(self):
        if self.action == "create":
            return ManualNotificationCreateSerializer
        return NotificationSerializer

    def get_queryset(self):
        return notification_queryset().filter(
            recipient_profile_id=active_profile(self.request).pk
        )

    def create(self, request, *args, **kwargs):
        actor_profile = None
        if request.user.is_superuser:
            try:
                actor_profile = active_profile(request)
            except DjangoPermissionDenied:
                actor_profile = None
        else:
            actor_profile = active_profile(request)
            if actor_profile.type != ProfileType.MANAGER:
                raise PermissionDenied(_("Only managers can create manual notifications."))

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        recipient = Profile.objects.get(pk=data["recipient_profile_id"])
        notification = NotificationService.create_notification(
            recipient_profile=recipient,
            actor_profile=actor_profile,
            notification_type=NotificationType.MANUAL_MANAGER_NOTIFICATION,
            title=data["title"],
            body=data["body"],
            object_type=data.get("object_type"),
            object_id=data.get("object_id"),
            metadata=data.get("metadata") or {},
            target_url=data.get("target_url"),
        )
        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Notifications"],
        responses={200: UnreadCountSerializer},
    ),
)
class NotificationUnreadCountViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "NOTIFICATION"
    manager_permission_map = {
        "list": "notifications.list",
    }
    queryset = Notification.objects.all()
    serializer_class = UnreadCountSerializer
    http_method_names = ["get"]

    def list(self, request, *args, **kwargs):
        unread_count = NotificationService.get_unread_count(active_profile(request))
        serializer = self.get_serializer({"unread_count": unread_count})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema_view(
    create=extend_schema(
        tags=["Notifications"],
        request=None,
        responses={200: NotificationSerializer},
    ),
)
class NotificationMarkReadViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "NOTIFICATION"
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        notification = NotificationService.mark_as_read(
            kwargs["pk"],
            active_profile(request),
        )
        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Notifications"],
        request=None,
        responses={200: MarkAllReadSerializer},
    ),
)
class NotificationMarkAllReadViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "NOTIFICATION"
    queryset = Notification.objects.all()
    serializer_class = MarkAllReadSerializer
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        updated_count = NotificationService.mark_all_as_read(active_profile(request))
        serializer = self.get_serializer({"updated_count": updated_count})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema_view(
    retrieve=extend_schema(
        tags=["Notifications"],
        description=(
            "Retrieve the authenticated account's Do Not Disturb (DND) settings. "
            "DND belongs to the whole UserAccount and suppresses mobile FCM push "
            "notifications only; in-app Notification rows and WebSocket realtime "
            "events are still created and delivered."
        ),
        responses={200: NotificationPreferenceSerializer},
    ),
    partial_update=extend_schema(
        tags=["Notifications"],
        description=(
            "Update the authenticated account's Do Not Disturb (DND) settings. "
            "Supports one daily time interval (including overnight ranges such as "
            "23:00 to 08:00). DND suppresses mobile FCM push delivery only without "
            "delaying or suppressing in-app notifications or WebSocket realtime delivery."
        ),
        request=NotificationPreferenceSerializer,
        responses={200: NotificationPreferenceSerializer},
    ),
)
class NotificationPreferenceViewSet(viewsets.GenericViewSet):
    """Account-wide DND preference endpoint scoped strictly to request.user."""

    permission_classes = [IsAuthenticated]
    queryset = NotificationPreference.objects.all()
    serializer_class = NotificationPreferenceSerializer
    http_method_names = ["get", "patch"]

    def retrieve(self, request, *args, **kwargs):
        preference = NotificationPreferenceService.get_preference(request.user)
        serializer = self.get_serializer(preference)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        preference = NotificationPreferenceService.update_preference(
            request.user,
            **serializer.validated_data,
        )
        return Response(
            self.get_serializer(preference).data,
            status=status.HTTP_200_OK,
        )

