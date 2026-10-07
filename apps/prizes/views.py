from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.prizes.models import PrizeAward, PrizeClaim
from apps.prizes.serializers import (
    AdminPrizeAwardSerializer,
    AdminPrizeClaimActionSerializer,
    AdminPrizeClaimSerializer,
    MyPrizeAwardSerializer,
    MyPrizeClaimResponseSerializer,
    PrizeClaimSubmitSerializer,
)
from apps.prizes.services import PrizeAwardService, PrizeClaimService


@extend_schema_view(
    list=extend_schema(tags=["Prizes"]),
    retrieve=extend_schema(tags=["Prizes"]),
)
class MyPrizeAwardViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Authenticated read-only list and detail of the caller's own `PrizeAward` records."""

    permission_classes = [IsAuthenticated]
    serializer_class = MyPrizeAwardSerializer
    queryset = PrizeAward.objects.none()

    def get_queryset(self):
        queryset = PrizeAwardService.list_for_user(self.request.user)
        params = self.request.query_params
        status_param = params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        fulfillment_type_param = params.get("fulfillment_type")
        if fulfillment_type_param:
            queryset = queryset.filter(fulfillment_type=fulfillment_type_param)
        source_type_param = params.get("source_type")
        if source_type_param:
            queryset = queryset.filter(source_type=source_type_param)
        return queryset


@extend_schema_view(
    create=extend_schema(
        tags=["Prizes"],
        request=PrizeClaimSubmitSerializer,
        responses={201: MyPrizeClaimResponseSerializer},
    ),
)
class MyPrizeAwardClaimViewSet(viewsets.GenericViewSet):
    """Authenticated claim submission for the caller's own pending `CLAIM_REQUIRED` `PrizeAward`."""

    permission_classes = [IsAuthenticated]
    serializer_class = PrizeClaimSubmitSerializer
    queryset = PrizeClaim.objects.none()

    def create(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        claim = PrizeClaimService.submit_claim(
            request.user,
            pk,
            full_name=serializer.validated_data["full_name"],
            address=serializer.validated_data["address"],
            identity_number=serializer.validated_data["identity_number"],
        )
        return Response(
            MyPrizeClaimResponseSerializer(claim).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    list=extend_schema(tags=["Prizes"]),
    retrieve=extend_schema(tags=["Prizes"]),
)
class AdminPrizeAwardViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Manager read-only monitoring list and detail for cross-domain `PrizeAward` records."""

    permission_classes = [HasAppAccess]
    permission_resource = "PRIZE_AWARD_ADMIN"
    manager_permission_map = {
        "list": "prizes.awards.list",
        "retrieve": "prizes.awards.retrieve",
    }
    serializer_class = AdminPrizeAwardSerializer
    queryset = PrizeAward.objects.none()

    def get_queryset(self):
        queryset = (
            PrizeAward.objects.filter(deleted__isnull=True)
            .select_related("user", "profile")
            .order_by("-awarded_at", "-id")
        )
        params = self.request.query_params
        status_param = params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        fulfillment_type_param = params.get("fulfillment_type")
        if fulfillment_type_param:
            queryset = queryset.filter(fulfillment_type=fulfillment_type_param)
        source_type_param = params.get("source_type")
        if source_type_param:
            queryset = queryset.filter(source_type=source_type_param)
        user_param = params.get("user")
        if user_param:
            try:
                queryset = queryset.filter(user_id=int(user_param))
            except (TypeError, ValueError):
                queryset = queryset.none()
        return queryset


@extend_schema_view(
    list=extend_schema(tags=["Prizes"]),
    retrieve=extend_schema(tags=["Prizes"]),
)
class AdminPrizeClaimViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Manager read-only list and detail for submitted `PrizeClaim` records."""

    permission_classes = [HasAppAccess]
    permission_resource = "PRIZE_CLAIM_ADMIN"
    manager_permission_map = {
        "list": "prizes.claims.list",
        "retrieve": "prizes.claims.retrieve",
    }
    serializer_class = AdminPrizeClaimSerializer
    queryset = PrizeClaim.objects.none()

    def get_queryset(self):
        queryset = PrizeClaimService.list_for_admin()
        params = self.request.query_params
        status_param = params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        source_type_param = params.get("source_type") or params.get(
            "award__source_type"
        )
        if source_type_param:
            queryset = queryset.filter(award__source_type=source_type_param)
        return queryset


@extend_schema_view(
    create=extend_schema(
        tags=["Prizes"],
        request=AdminPrizeClaimActionSerializer,
        responses={200: AdminPrizeClaimSerializer},
    ),
)
class AdminPrizeClaimReviewViewSet(viewsets.GenericViewSet):
    """Manager command endpoint to mark a submitted `PrizeClaim` as `REVIEWED`."""

    permission_classes = [HasAppAccess]
    permission_resource = "PRIZE_CLAIM_ADMIN"
    manager_permission_map = {
        "create": "prizes.claims.review",
    }
    serializer_class = AdminPrizeClaimActionSerializer
    queryset = PrizeClaim.objects.none()

    def create(self, request, pk=None):
        claim = PrizeClaimService.review_claim(pk, request.user)
        return Response(
            AdminPrizeClaimSerializer(claim).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Prizes"],
        request=AdminPrizeClaimActionSerializer,
        responses={200: AdminPrizeClaimSerializer},
    ),
)
class AdminPrizeClaimDeliverViewSet(viewsets.GenericViewSet):
    """Manager command endpoint to mark a reviewed `PrizeClaim` as `DELIVERED` and fulfill its `PrizeAward`."""

    permission_classes = [HasAppAccess]
    permission_resource = "PRIZE_CLAIM_ADMIN"
    manager_permission_map = {
        "create": "prizes.claims.deliver",
    }
    serializer_class = AdminPrizeClaimActionSerializer
    queryset = PrizeClaim.objects.none()

    def create(self, request, pk=None):
        claim = PrizeClaimService.deliver_claim(pk, request.user)
        return Response(
            AdminPrizeClaimSerializer(claim).data,
            status=status.HTTP_200_OK,
        )


