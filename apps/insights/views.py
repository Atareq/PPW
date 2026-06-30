from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.insights.models import SellerInsightEvent
from apps.insights.serializers import SellerInsightsHomeSerializer
from apps.insights.services import SellerInsightsService
from apps.users.constants import ProfileType
from django.utils.translation import gettext_lazy as _


@extend_schema_view(
    list=extend_schema(
        tags=["Insights"],
        responses={200: SellerInsightsHomeSerializer},
    ),
)
class SellerHomeInsightsViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Seller home-page analytics summary from existing marketplace data."""

    permission_classes = [IsAuthenticated]
    serializer_class = SellerInsightsHomeSerializer
    queryset = SellerInsightEvent.objects.none()

    def list(self, request, *args, **kwargs):
        active_profile = request.user.active_profile
        if active_profile.type != ProfileType.SELLER:
            raise PermissionDenied(_("Active profile must be a seller."))

        summary = SellerInsightsService.get_home_summary(active_profile)
        return Response(
            SellerInsightsHomeSerializer(summary).data,
            status=status.HTTP_200_OK,
        )
