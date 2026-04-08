from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.assets.views import BirdViewSet

router = DefaultRouter()

router.register("bird", BirdViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
