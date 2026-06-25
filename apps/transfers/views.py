from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.transfers.models import OwnershipTransfer
from apps.transfers.serializers import (
    CreateOwnershipTransferSerializer,
    OwnershipTransferSerializer,
)
from apps.users.constants import ProfileType


class OwnershipTransferViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "OWNERSHIP_TRANSFER"
    queryset = OwnershipTransfer.objects.all()

    def get_queryset(self):
        profile = self.request.user.active_profile
        if profile.type == ProfileType.MANAGER:
            qs = OwnershipTransfer.objects.all()
        else:
            qs = OwnershipTransfer.objects.filter(
                Q(from_profile=profile) | Q(to_profile=profile)
            )

        asset_id = self.request.query_params.get("asset_id")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)

        return qs.select_related(
            "asset",
            "from_profile__user",
            "from_profile__sellerprofile",
            "to_profile__user",
            "to_profile__sellerprofile",
        ).order_by("-created")

    def get_serializer_class(self):
        if self.action == "create":
            return CreateOwnershipTransferSerializer
        return OwnershipTransferSerializer

    def create(self, request, *args, **kwargs):
        profile = request.user.active_profile
        if profile.type != ProfileType.SELLER:
            return Response(
                {"detail": "فقط البائعون يمكنهم نقل الملكية مباشرة."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transfer = serializer.save()
        return Response(
            OwnershipTransferSerializer(transfer, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
