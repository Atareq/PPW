from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.response import Response

from apps.draws.models import (
    WeeklyDraw,
    WeeklyDrawEntry,
    WeeklyDrawEntryStatus,
)
from apps.draws.serializers import (
    MyWeeklyDrawEntryStatusSerializer,
    WeeklyDrawEntryAdminSerializer,
    WeeklyDrawSerializer,
)
from apps.draws.services import WeeklyDrawEntryService, WeeklyDrawService
from apps.general.authorizations import HasAppAccess
from apps.users.models import ManagerProfile


def _get_manager_profile(request):
    try:
        profile = request.user.active_profile
    except Exception:
        return None
    return profile if isinstance(profile, ManagerProfile) else None


@extend_schema_view(
    list=extend_schema(tags=["Weekly Draws"]),
    retrieve=extend_schema(tags=["Weekly Draws"]),
    create=extend_schema(
        tags=["Weekly Draws"],
        request=WeeklyDrawSerializer,
        responses={201: WeeklyDrawSerializer},
    ),
    partial_update=extend_schema(
        tags=["Weekly Draws"],
        request=WeeklyDrawSerializer,
        responses={200: WeeklyDrawSerializer},
    ),
)
class WeeklyDrawAdminViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manager CRUD for weekly onboarding draw windows (BE-DRW-01 / BE-DRW-02)."""

    permission_classes = [HasAppAccess]
    permission_resource = "WEEKLY_DRAW_ADMIN"
    manager_permission_map = {
        "list": "draws.weekly.list",
        "retrieve": "draws.weekly.retrieve",
        "create": "draws.weekly.create",
        "partial_update": "draws.weekly.update",
    }
    serializer_class = WeeklyDrawSerializer

    def get_queryset(self):
        return (
            WeeklyDraw.objects.filter(deleted__isnull=True)
            .select_related("created_by")
            .annotate(
                entries_count=Count(
                    "entries",
                    filter=Q(
                        entries__deleted__isnull=True,
                        entries__status=WeeklyDrawEntryStatus.ASSIGNED,
                    ),
                )
            )
            .order_by("starts_at", "id")
        )

    def perform_create(self, serializer):
        serializer.instance = WeeklyDrawService.create_draw(
            serializer.validated_data,
            created_by=_get_manager_profile(self.request),
        )

    def perform_update(self, serializer):
        serializer.instance = WeeklyDrawService.update_draw(
            serializer.instance,
            serializer.validated_data,
        )


class WeeklyDrawOpenViewSet(viewsets.GenericViewSet):
    """Manager action to open a draft weekly draw and assign pending entries."""

    permission_classes = [HasAppAccess]
    permission_resource = "WEEKLY_DRAW_ADMIN"
    manager_permission_map = {"create": "draws.weekly.open"}
    serializer_class = WeeklyDrawSerializer
    queryset = WeeklyDraw.objects.filter(deleted__isnull=True)

    @extend_schema(
        tags=["Weekly Draws"],
        request=None,
        responses={200: WeeklyDrawSerializer},
    )
    def create(self, request, *args, **kwargs):
        draw = self.get_object()
        opened = WeeklyDrawService.open_draw(draw)
        return Response(
            WeeklyDrawSerializer(opened).data,
            status=status.HTTP_200_OK,
        )


class WeeklyDrawCloseViewSet(viewsets.GenericViewSet):
    """Manager action to close an ended open weekly draw and freeze participants."""

    permission_classes = [HasAppAccess]
    permission_resource = "WEEKLY_DRAW_ADMIN"
    manager_permission_map = {"create": "draws.weekly.close"}
    serializer_class = WeeklyDrawSerializer
    queryset = WeeklyDraw.objects.filter(deleted__isnull=True)

    @extend_schema(
        tags=["Weekly Draws"],
        request=None,
        responses={200: WeeklyDrawSerializer},
    )
    def create(self, request, *args, **kwargs):
        draw = self.get_object()
        closed = WeeklyDrawService.close_draw(
            draw,
            closed_by=_get_manager_profile(request),
        )
        return Response(
            WeeklyDrawSerializer(closed).data,
            status=status.HTTP_200_OK,
        )


class WeeklyDrawFinalizeViewSet(viewsets.GenericViewSet):
    """Manager action to execute weekly draw finalization / freeze foundation (BE-DRW-02)."""

    permission_classes = [HasAppAccess]
    permission_resource = "WEEKLY_DRAW_ADMIN"
    manager_permission_map = {"create": "draws.weekly.finalize"}
    serializer_class = WeeklyDrawSerializer
    queryset = WeeklyDraw.objects.filter(deleted__isnull=True)

    @extend_schema(
        tags=["Weekly Draws"],
        request=None,
        responses={200: WeeklyDrawSerializer},
    )
    def create(self, request, *args, **kwargs):
        draw = self.get_object()
        frozen = WeeklyDrawService.finalize_draw(
            draw,
            finalized_by=_get_manager_profile(request),
        )
        return Response(
            WeeklyDrawSerializer(frozen).data,
            status=status.HTTP_200_OK,
        )


class WeeklyDrawEntryAdminViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Manager read-only list of eligible (`ASSIGNED`) entries on a weekly draw."""

    permission_classes = [HasAppAccess]
    permission_resource = "WEEKLY_DRAW_ADMIN"
    manager_permission_map = {"list": "draws.weekly.entries.list"}
    serializer_class = WeeklyDrawEntryAdminSerializer
    queryset = WeeklyDrawEntry.objects.filter(
        status=WeeklyDrawEntryStatus.ASSIGNED,
        deleted__isnull=True,
    ).order_by("created", "id")

    def _get_draw(self) -> WeeklyDraw:
        return get_object_or_404(
            WeeklyDraw.objects.filter(deleted__isnull=True),
            pk=self.kwargs["pk"],
        )

    @extend_schema(tags=["Weekly Draws"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        draw = self._get_draw()
        return WeeklyDrawService.get_eligible_entries(draw)


class MyWeeklyDrawEntryViewSet(viewsets.GenericViewSet):
    """Authenticated user read-only view for the account's onboarding weekly draw entry."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MyWeeklyDrawEntryStatusSerializer

    @extend_schema(
        tags=["Weekly Draws"],
        responses={200: MyWeeklyDrawEntryStatusSerializer},
    )
    def list(self, request, *args, **kwargs):
        status_payload = WeeklyDrawEntryService.get_entry_status(request.user)
        serializer = self.get_serializer(status_payload)
        return Response(serializer.data, status=status.HTTP_200_OK)
