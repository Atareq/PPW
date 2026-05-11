from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.races.views import (
    RaceResultListCreateViewSet,
    RaceResultViewSet,
    RaceViewSet,
)

router = DefaultRouter()
router.register("races", RaceViewSet, basename="race")
router.register("race-results", RaceResultViewSet, basename="race-result")

race_result_list_create = RaceResultListCreateViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)

urlpatterns = [
    path(
        "races/<int:race_pk>/results/",
        race_result_list_create,
        name="race-result-list-create",
    ),
    path("", include(router.urls)),
]
