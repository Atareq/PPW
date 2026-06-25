from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated

from apps.announcements.models import Announcement
from apps.announcements.serializers import AnnouncementSerializer


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_superuser
        )


def announcement_queryset():
    return Announcement.objects.select_related("created_by").order_by("-created", "-id")


@extend_schema_view(
    list=extend_schema(tags=["Announcements"]),
    retrieve=extend_schema(tags=["Announcements"]),
)
class AnnouncementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = AnnouncementSerializer
    queryset = Announcement.objects.all()
    filter_backends = [OrderingFilter]
    ordering_fields = ["created", "modified"]
    ordering = ["-created"]

    def get_queryset(self):
        return announcement_queryset().filter(
            is_active=True,
            target=self.request.user.active_profile.type,
        )


@extend_schema_view(
    list=extend_schema(tags=["Announcements"]),
    create=extend_schema(tags=["Announcements"], request=AnnouncementSerializer),
    retrieve=extend_schema(tags=["Announcements"]),
    partial_update=extend_schema(tags=["Announcements"], request=AnnouncementSerializer),
    destroy=extend_schema(tags=["Announcements"]),
)
class AdminAnnouncementViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsSuperAdmin]
    serializer_class = AnnouncementSerializer
    queryset = Announcement.objects.all()
    parser_classes = [JSONParser]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["is_active", "target"]
    ordering_fields = ["created", "modified"]
    ordering = ["-created"]

    def get_queryset(self):
        return announcement_queryset()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.active_profile)
