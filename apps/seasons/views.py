from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.seasons.models import Season
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


class SeasonSellerInsightViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
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
