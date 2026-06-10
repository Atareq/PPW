from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.payments.models import PaymentRequest
from apps.payments.serializers import (
    CreateAuctionPaymentRequestSerializer,
    CreateMarketPaymentRequestSerializer,
    PaymentRequestDetailSerializer,
    PaymentRequestListSerializer,
    RejectPaymentRequestSerializer,
    UpdatePaymentRequestSerializer,
)
from apps.payments.services import (
    PaymentRequestService,
    payment_request_queryset,
)
from apps.users.constants import ProfileType


def active_profile_or_denied(request, expected_type):
    """Return the active profile only when it matches the endpoint actor."""

    active_profile = request.user.active_profile
    if active_profile.type != expected_type:
        raise PermissionDenied("Active profile is not allowed for this action.")
    return active_profile


@extend_schema_view(
    list=extend_schema(
        tags=["Payments"],
        responses={200: PaymentRequestListSerializer(many=True)},
    ),
    partial_update=extend_schema(
        tags=["Payments"],
        request=UpdatePaymentRequestSerializer,
        responses={200: PaymentRequestDetailSerializer},
    ),
)
class PaymentRequestViewSet(
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Role-scoped payment request list plus buyer note resubmission."""

    permission_classes = [HasAppAccess]
    permission_resource = "PAYMENT_REQUEST"
    queryset = PaymentRequest.objects.all()
    http_method_names = ["get", "patch"]

    def get_serializer_class(self):
        if self.action == "partial_update":
            return UpdatePaymentRequestSerializer
        return PaymentRequestListSerializer

    def get_queryset(self):
        """Managers see all, sellers see assigned requests, buyers see theirs."""

        queryset = payment_request_queryset()
        active_profile = self.request.user.active_profile

        if active_profile.type == ProfileType.MANAGER:
            return queryset

        if active_profile.type == ProfileType.SELLER:
            return queryset.filter(seller_id=active_profile.pk)

        if active_profile.type == ProfileType.CUSTOMER:
            return queryset.filter(buyer_id=active_profile.pk)

        return queryset.none()

    def partial_update(self, request, *args, **kwargs):
        profile = active_profile_or_denied(request, ProfileType.CUSTOMER)
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        payment_request = PaymentRequestService.update_by_buyer(
            kwargs["pk"],
            profile,
            buyer_note=serializer.validated_data.get("buyer_note"),
        )
        return Response(
            PaymentRequestDetailSerializer(payment_request).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Payments"],
        responses={201: PaymentRequestDetailSerializer},
    ),
)
class PaymentRequestCreateViewSet(viewsets.GenericViewSet):
    """Create buyer payment requests for either auction or market sources."""

    permission_classes = [HasAppAccess]
    permission_resource = "PAYMENT_REQUEST"
    queryset = PaymentRequest.objects.all()
    http_method_names = ["post"]
    source_type = None

    def get_serializer_class(self):
        if self.source_type == "auction":
            return CreateAuctionPaymentRequestSerializer
        return CreateMarketPaymentRequestSerializer

    def create(self, request, *args, **kwargs):
        profile = active_profile_or_denied(request, ProfileType.CUSTOMER)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if self.source_type == "auction":
            payment_request = PaymentRequestService.create_for_auction_item(
                serializer.validated_data["auction_item_id"],
                profile,
                buyer_note=serializer.validated_data.get("buyer_note", ""),
            )
        else:
            payment_request = PaymentRequestService.create_for_order_item(
                serializer.validated_data["order_item_id"],
                profile,
                buyer_note=serializer.validated_data.get("buyer_note", ""),
            )

        return Response(
            PaymentRequestDetailSerializer(payment_request).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Payments"],
        responses={200: PaymentRequestDetailSerializer},
    ),
)
class PaymentRequestDecisionViewSet(viewsets.GenericViewSet):
    """Seller approve/reject commands for pending manual payment requests."""

    permission_classes = [HasAppAccess]
    permission_resource = "PAYMENT_REQUEST"
    queryset = PaymentRequest.objects.all()
    http_method_names = ["post"]
    decision = None

    def get_serializer_class(self):
        if self.decision == "reject":
            return RejectPaymentRequestSerializer
        return PaymentRequestDetailSerializer

    def create(self, request, *args, **kwargs):
        profile = active_profile_or_denied(request, ProfileType.SELLER)

        if self.decision == "approve":
            payment_request = PaymentRequestService.approve(
                kwargs["pk"],
                profile,
            )
        else:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            payment_request = PaymentRequestService.reject(
                kwargs["pk"],
                profile,
                seller_note=serializer.validated_data.get("seller_note", ""),
            )

        return Response(
            PaymentRequestDetailSerializer(payment_request).data,
            status=status.HTTP_200_OK,
        )
