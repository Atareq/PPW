from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.seasons.models import Season, SeasonStatus
from apps.seasons.serializers import (
    SeasonSellerInsightSerializer,
    SeasonSerializer,
)
from apps.seasons.services import SeasonService


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
    queryset = Season.objects.all().order_by("-starts_at", "-id")


class SeasonCalculateWinnersViewSet(viewsets.GenericViewSet):
    """Calculate seller rankings and award top season sellers once."""

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
            manager_profile=request.user.active_profile,
        )
        return Response(
            SeasonSellerInsightSerializer(insights, many=True).data,
            status=status.HTTP_200_OK,
        )


class SeasonFinalizeViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "SEASONS_ADMIN"
    manager_permission_map = {"create": "seasons.finalize"}
    serializer_class = SeasonSerializer
    queryset = Season.objects.all()

    @extend_schema(tags=["Seasons"], request=None, responses={200: SeasonSerializer})
    def create(self, request, *args, **kwargs):
        season = self.get_object()
        if season.status != SeasonStatus.CALCULATED:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                {"detail": "Only calculated seasons can be finalized."}
            )
        season.status = SeasonStatus.FINALIZED
        season.finalized_by = request.user.active_profile
        season.save(update_fields=["status", "finalized_by", "modified"])
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
