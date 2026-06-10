from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.filters import OrderingFilter

from apps.general.authorizations import HasAppAccess
from apps.races.filters import RaceResultFilter
from apps.races.models import Race, RaceResult
from apps.races.serializers import (
    BulkRaceResultCreateSerializer,
    RaceDetailSerializer,
    RaceResultCreateSerializer,
    RaceResultSerializer,
    RaceResultUpdateSerializer,
    RaceSerializer,
)
from apps.races.services import delete_race_result


def include_race_from_request(request) -> bool:
    value = request.query_params.get("include_race", "false")
    return value.lower() in {"1", "true", "yes", "on"}


@extend_schema_view(
    create=extend_schema(
        tags=["Races"],
        request=RaceSerializer,
        responses={201: RaceSerializer},
    ),
)
class RaceViewSet(viewsets.ModelViewSet):
    """Race CRUD; managers write while authenticated profiles can read."""

    permission_classes = [HasAppAccess]
    permission_resource = "RACE"
    allow_scoped_read_access = True
    queryset = Race.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = [
        "season_year",
        "station_name",
    ]
    ordering_fields = [
        "release_datetime",
        "season_year",
        "created",
        "modified",
        "title",
    ]

    def get_queryset(self):
        return Race.objects.prefetch_related("results").order_by(
            "-release_datetime",
            "-created",
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return RaceDetailSerializer
        return RaceSerializer


@extend_schema_view(
    create=extend_schema(
        tags=["Race Results"],
        request=RaceResultCreateSerializer,
        responses={201: RaceResultSerializer},
    ),
    list=extend_schema(
        tags=["Race Results"],
        responses={200: RaceResultSerializer(many=True)},
    ),
)
class RaceResultViewSet(viewsets.ModelViewSet):
    """Global race-result search and single-result management."""

    permission_classes = [HasAppAccess]
    permission_resource = "RACE_RESULT"
    allow_scoped_read_access = True
    queryset = RaceResult.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = RaceResultFilter
    ordering_fields = [
        "rank",
        "speed",
        "arrival_datetime",
        "created",
        "modified",
    ]

    def get_queryset(self):
        return RaceResult.objects.select_related("race").order_by(
            "rank",
            "id",
        )

    def get_serializer_class(self):
        if self.action == "create":
            return RaceResultCreateSerializer
        if self.action in ["update", "partial_update"]:
            return RaceResultUpdateSerializer
        return RaceResultSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["include_race"] = include_race_from_request(self.request)
        return context

    def perform_destroy(self, instance):
        delete_race_result(instance)


@extend_schema_view(
    list=extend_schema(
        tags=["Race Results"],
        responses={200: RaceResultSerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Race Results"],
        request=BulkRaceResultCreateSerializer,
        responses={201: BulkRaceResultCreateSerializer},
    ),
)
class RaceResultListCreateViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Nested race-result list and bulk create endpoint for one race."""

    permission_classes = [HasAppAccess]
    permission_resource = "RACE_RESULT"
    allow_scoped_read_access = True
    queryset = RaceResult.objects.all()

    def get_queryset(self):
        self.race = get_object_or_404(Race, pk=self.kwargs["race_pk"])
        return (
            RaceResult.objects.select_related("race")
            .filter(
                race=self.race,
            )
            .order_by("rank", "id")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return BulkRaceResultCreateSerializer
        return RaceResultSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["race"] = getattr(self, "race", None) or get_object_or_404(
            Race,
            pk=self.kwargs["race_pk"],
        )
        return context
