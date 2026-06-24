from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.referrals.models import LinkSharingCode
from apps.referrals.serializers import (
    LinkSharingCodeSerializer,
    ReferralRedeemSerializer,
)
from apps.referrals.services import ReferralService


class LinkSharingCodeViewSet(viewsets.GenericViewSet):
    """Create short-lived referral codes for the active profile."""

    permission_classes = [IsAuthenticated]
    serializer_class = LinkSharingCodeSerializer
    queryset = LinkSharingCode.objects.all()

    @extend_schema(
        tags=["Referrals"],
        request=None,
        responses={201: LinkSharingCodeSerializer},
    )
    def create(self, request, *args, **kwargs):
        code = ReferralService.create_code(request.user.active_profile)
        return Response(
            LinkSharingCodeSerializer(code).data,
            status=status.HTTP_201_CREATED,
        )


class ReferralRedeemViewSet(viewsets.GenericViewSet):
    """Redeem a valid code for a new account and reward the inviter."""

    permission_classes = [IsAuthenticated]
    serializer_class = ReferralRedeemSerializer
    queryset = LinkSharingCode.objects.all()

    @extend_schema(
        tags=["Referrals"],
        request=ReferralRedeemSerializer,
        responses={200: LinkSharingCodeSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = ReferralService.redeem_code(
            request.user.active_profile,
            serializer.validated_data["code"],
        )
        return Response(LinkSharingCodeSerializer(code).data, status=status.HTTP_200_OK)
