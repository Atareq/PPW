from rest_framework import viewsets

from apps.assets.models import Bird
from apps.assets.serializer import BirdSerializer
from apps.general.authorizations import HasAppAccess


class BirdViewSet(viewsets.ModelViewSet):
    queryset = Bird.objects.all()
    serializer_class = BirdSerializer
    permission_classes = [
        HasAppAccess,
    ]
    permission_resource = "BIRD"
    # when adding needs to check here if the user role is seller only
