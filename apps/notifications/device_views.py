from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.notifications.device_serializers import (
    PushDeviceRegisterSerializer,
    PushDeviceSerializer,
)
from apps.notifications.models import PushDevice


class PushDeviceViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "NOTIFICATION"
    serializer_class = PushDeviceSerializer
    queryset = PushDevice.objects.all()
    http_method_names = ["post", "delete"]

    def create(self, request, *args, **kwargs):
        serializer = PushDeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = request.user.active_profile
        token = serializer.validated_data["token"]
        platform = serializer.validated_data["platform"]
        now = timezone.now()

        device, created = PushDevice.objects.update_or_create(
            token=token,
            defaults={
                "user": request.user,
                "profile": profile,
                "platform": platform,
                "is_active": True,
                "last_seen_at": now,
            },
        )
        return Response(
            PushDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        device = PushDevice.objects.filter(
            pk=kwargs["pk"],
            user=request.user,
            profile=request.user.active_profile,
        ).first()
        if device is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        device.is_active = False
        device.last_seen_at = timezone.now()
        device.save(update_fields=["is_active", "last_seen_at", "modified"])
        return Response(status=status.HTTP_204_NO_CONTENT)
