from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.seasons.models import Season, SeasonPrize
from apps.seasons.serializers import (
    SeasonPrizeSerializer,
    SeasonSellerInsightSerializer,
    SeasonSerializer,
)
from apps.seasons.services import SeasonPrizeService, SeasonService
from apps.users.models import ManagerProfile


def _get_manager_profile(request):
    try:
        profile = request.user.active_profile
    except Exception:
        return None
    return profile if isinstance(profile, ManagerProfile) else None


@extend_schema_view(
    list=extend_schema(tags=["Seasons"]),
    create=extend_schema(tags=["Seasons"], request=SeasonSerializer),
    partial_update=extend_schema(tags=["Seasons"], request=SeasonSerializer),
)
class SeasonViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manager season configuration for reward snapshots."""

    permission_classes = [HasAppAccess]
    permission_resource = "SEASONS_ADMIN"
    manager_permission_map = {
        "list": "seasons.list",
        "create": "seasons.create",
        "partial_update": "seasons.update",
    }
    serializer_class = SeasonSerializer
    queryset = Season.objects.prefetch_related(
        Prefetch(
            "prizes",
            queryset=SeasonPrize.objects.filter(deleted__isnull=True).order_by(
                "rank", "id"
            ),
        )
    ).order_by("-starts_at", "-id")

    def perform_create(self, serializer):
        serializer.instance = SeasonService.create_season(
            manager_profile=_get_manager_profile(self.request),
            **serializer.validated_data,
        )


@extend_schema_view(
    list=extend_schema(tags=["Seasons"], responses={200: SeasonPrizeSerializer(many=True)}),
    create=extend_schema(
        tags=["Seasons"],
        request=SeasonPrizeSerializer,
        responses={201: SeasonPrizeSerializer},
    ),
    partial_update=extend_schema(
        tags=["Seasons"],
        request=SeasonPrizeSerializer,
        responses={200: SeasonPrizeSerializer},
    ),
    destroy=extend_schema(tags=["Seasons"], responses={204: None}),
)
class SeasonPrizeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Manager configuration for per-season Rank 1..3 prizes before season start."""

    permission_classes = [HasAppAccess]
    permission_resource = "SEASONS_ADMIN"
    manager_permission_map = {
        "list": "seasons.prizes.list",
        "create": "seasons.prizes.create",
        "partial_update": "seasons.prizes.update",
        "destroy": "seasons.prizes.delete",
    }
    serializer_class = SeasonPrizeSerializer
    queryset = SeasonPrize.objects.all().order_by("rank", "id")

    def _get_season(self):
        return get_object_or_404(Season, pk=self.kwargs["season_pk"])

    def get_queryset(self):
        season = self._get_season()
        return SeasonPrize.objects.filter(
            season=season,
            deleted__isnull=True,
        ).order_by("rank", "id")

    def perform_create(self, serializer):
        season = self._get_season()
        serializer.instance = SeasonPrizeService.create_prize(
            season,
            manager_profile=_get_manager_profile(self.request),
            **serializer.validated_data,
        )

    def perform_update(self, serializer):
        serializer.instance = SeasonPrizeService.update_prize(
            serializer.instance,
            manager_profile=_get_manager_profile(self.request),
            **serializer.validated_data,
        )

    def perform_destroy(self, instance):
        SeasonPrizeService.delete_prize(
            instance,
            manager_profile=_get_manager_profile(self.request),
        )


class SeasonCalculateWinnersViewSet(viewsets.GenericViewSet):
    """Calculate seller season scores and rankings without awarding rewards."""

    permission_classes = [HasAppAccess]
    permission_resource = "SEASONS_ADMIN"
    manager_permission_map = {"create": "seasons.calculate"}
    serializer_class = SeasonSellerInsightSerializer
    queryset = Season.objects.all()

    @extend_schema(tags=["Seasons"], request=None)
    def create(self, request, *args, **kwargs):
        season = self.get_object()
        insights = SeasonService.calculate_winners(
            season,
            manager_profile=_get_manager_profile(request),
        )
        return Response(
            SeasonSellerInsightSerializer(insights, many=True).data,
            status=status.HTTP_200_OK,
        )


class SeasonFinalizeViewSet(viewsets.GenericViewSet):
    """Finalize an ended season, refresh scores one last time, and pay Top-3 winners once."""

    permission_classes = [HasAppAccess]
    permission_resource = "SEASONS_ADMIN"
    manager_permission_map = {"create": "seasons.finalize"}
    serializer_class = SeasonSerializer
    queryset = Season.objects.all()

    @extend_schema(tags=["Seasons"], request=None, responses={200: SeasonSerializer})
    def create(self, request, *args, **kwargs):
        season = self.get_object()
        season = SeasonService.finalize(
            season,
            manager_profile=_get_manager_profile(request),
        )
        return Response(SeasonSerializer(season).data, status=status.HTTP_200_OK)


class SeasonSellerInsightViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Read calculated seller insight snapshots for one season."""

    permission_classes = [HasAppAccess]
    permission_resource = "SEASONS_ADMIN"
    manager_permission_map = {"list": "seasons.insights.list"}
    serializer_class = SeasonSellerInsightSerializer
    queryset = Season.objects.all()

    def list(self, request, *args, **kwargs):
        season = Season.objects.get(pk=kwargs["pk"])
        return Response(
            SeasonSellerInsightSerializer(
                SeasonService.insights(season),
                many=True,
            ).data,
            status=status.HTTP_200_OK,
        )
