from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.general.models import AdminAnnouncement
from apps.general.serializers import (
    AdminAnnouncementSerializer,
    AnnouncementDismissResponseSerializer,
    AnnouncementDismissSerializer,
    CurrentAnnouncementSerializer,
)
from apps.general.services import AnnouncementService


@extend_schema_view(
    list=extend_schema(
        tags=["Announcements"],
        responses={200: CurrentAnnouncementSerializer(many=True)},
    ),
)
class CurrentAnnouncementViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Return currently active, unexpired announcements not dismissed by request.user."""

    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "ANNOUNCEMENT"
    manager_permission_map = {
        "list": "announcements.list",
    }
    serializer_class = CurrentAnnouncementSerializer
    queryset = AdminAnnouncement.objects.none()

    def get_queryset(self):
        return AnnouncementService.visible_for_user(self.request.user)


class AnnouncementDismissViewSet(viewsets.GenericViewSet):
    """Idempotently dismiss an announcement for the authenticated UserAccount."""

    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "ANNOUNCEMENT"
    manager_permission_map = {
        "create": "announcements.list",
    }
    serializer_class = AnnouncementDismissSerializer
    queryset = AdminAnnouncement.objects.all()

    @extend_schema(
        tags=["Announcements"],
        request=None,
        responses={200: AnnouncementDismissResponseSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dismissal = AnnouncementService.dismiss(
            announcement_id=kwargs["pk"],
            user=request.user,
        )
        return Response(
            AnnouncementDismissResponseSerializer(dismissal).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(tags=["Announcements"]),
    retrieve=extend_schema(tags=["Announcements"]),
    create=extend_schema(
        tags=["Announcements"],
        request=AdminAnnouncementSerializer,
        responses={201: AdminAnnouncementSerializer},
    ),
    partial_update=extend_schema(
        tags=["Announcements"],
        request=AdminAnnouncementSerializer,
        responses={200: AdminAnnouncementSerializer},
    ),
    destroy=extend_schema(tags=["Announcements"]),
)
class AdminAnnouncementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Manager CRUD for AdminAnnouncement bar records."""

    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "ANNOUNCEMENT_ADMIN"
    manager_permission_map = {
        "list": "announcements.list",
        "retrieve": "announcements.retrieve",
        "create": "announcements.create",
        "partial_update": "announcements.update",
        "update": "announcements.update",
        "destroy": "announcements.delete",
    }
    serializer_class = AdminAnnouncementSerializer
    queryset = AdminAnnouncement.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["is_active"]
    ordering_fields = ["created", "modified", "starts_at", "expires_at"]
    ordering = ["-created", "-id"]

    def get_queryset(self):
        return (
            AdminAnnouncement.objects.filter(deleted__isnull=True)
            .select_related("created_by")
            .order_by("-created", "-id")
        )

    def perform_create(self, serializer):
        serializer.instance = AnnouncementService.create_announcement(
            serializer.validated_data,
            created_by=AnnouncementService._resolve_creator_profile(
                self.request.user
            ),
        )

    def perform_update(self, serializer):
        serializer.instance = AnnouncementService.update_announcement(
            serializer.instance,
            serializer.validated_data,
        )

    def perform_destroy(self, instance):
        AnnouncementService.delete_announcement(instance)

