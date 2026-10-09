from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.notifications.device_serializers import (
    PushDeviceRegisterSerializer,
    PushDeviceSerializer,
)
from apps.notifications.models import PushDevice


@extend_schema_view(
    list=extend_schema(
        tags=["Push Devices"],
        responses={200: PushDeviceSerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Push Devices"],
        request=PushDeviceRegisterSerializer,
        responses={201: PushDeviceSerializer, 200: PushDeviceSerializer},
    ),
    destroy=extend_schema(
        tags=["Push Devices"],
        responses={204: None},
    ),
)
class PushDeviceViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PushDeviceSerializer
    queryset = PushDevice.objects.all()
    http_method_names = ["get", "post", "delete"]

    def list(self, request, *args, **kwargs):
        profile = request.user.active_profile
        devices = PushDevice.objects.filter(
            profile=profile,
            is_active=True,
        ).order_by("-created")
        serializer = self.get_serializer(devices, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = PushDeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = request.user.active_profile
        token = serializer.validated_data["token"]
        platform = serializer.validated_data["platform"]
        now = timezone.now()

        existing = PushDevice.objects.filter(token=token).first()
        if existing is not None:
            # Reassign token to the current authenticated user and profile
            existing.user = request.user
            existing.profile = profile
            existing.platform = platform
            existing.is_active = True
            existing.last_seen_at = now
            existing.save(
                update_fields=[
                    "user",
                    "profile",
                    "platform",
                    "is_active",
                    "last_seen_at",
                    "modified",
                ]
            )
            return Response(
                PushDeviceSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        device = PushDevice.objects.create(
            user=request.user,
            profile=profile,
            token=token,
            platform=platform,
            is_active=True,
            last_seen_at=now,
        )
        return Response(
            PushDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        profile = request.user.active_profile
        device = PushDevice.objects.filter(
            pk=kwargs["pk"],
            profile=profile,
        ).first()
        if device is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        device.is_active = False
        device.last_seen_at = timezone.now()
        device.save(update_fields=["is_active", "last_seen_at", "modified"])
        return Response(status=status.HTTP_204_NO_CONTENT)
