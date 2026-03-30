from rest_framework.routers import DefaultRouter
from apps.assets.views import BirdViewSet
from django.urls import path, include

router = DefaultRouter()

router.register("bird", BirdViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
