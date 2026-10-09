from django.urls import path

from apps.seasons.views import (
    SeasonCalculateWinnersViewSet,
    SeasonFinalizeViewSet,
    SeasonPrizeViewSet,
    SeasonSellerInsightViewSet,
    SeasonViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
PARTIAL_UPDATE_ACTION = {"patch": "partial_update"}
PARTIAL_UPDATE_DESTROY_ACTION = {"patch": "partial_update", "delete": "destroy"}

urlpatterns = [
    path("", SeasonViewSet.as_view(LIST_CREATE_ACTION), name="season-list"),
    path(
        "<int:pk>/",
        SeasonViewSet.as_view(PARTIAL_UPDATE_ACTION),
        name="season-detail",
    ),
    path(
        "<int:season_pk>/prizes/",
        SeasonPrizeViewSet.as_view(LIST_CREATE_ACTION),
        name="season-prize-list",
    ),
    path(
        "<int:season_pk>/prizes/<int:pk>/",
        SeasonPrizeViewSet.as_view(PARTIAL_UPDATE_DESTROY_ACTION),
        name="season-prize-detail",
    ),
    path(
        "<int:pk>/calculate-winners/",
        SeasonCalculateWinnersViewSet.as_view(CREATE_ACTION),
        name="season-calculate-winners",
    ),
    path(
        "<int:pk>/finalize/",
        SeasonFinalizeViewSet.as_view(CREATE_ACTION),
        name="season-finalize",
    ),
    path(
        "<int:pk>/seller-insights/",
        SeasonSellerInsightViewSet.as_view(LIST_ACTION),
        name="season-seller-insight-list",
    ),
]
