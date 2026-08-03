from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.loft_subscriptions.models import (
    LoftSubscription,
    SubscriptionInvoice,
    SubscriptionPaymentRequest,
)
from apps.loft_subscriptions.serializers import (
    CheckoutResponseSerializer,
    LoftSubscriptionSerializer,
    PricingResponseSerializer,
    SubscriptionCheckoutSerializer,
    SubscriptionInvoiceDetailSerializer,
    SubscriptionInvoiceListSerializer,
    SubscriptionPaymentRequestSerializer,
    SubscriptionSelectionSerializer,
    catalog_response_data,
    pricing_response_data,
)
from apps.loft_subscriptions.services import (
    SubscriptionAccessService,
    SubscriptionCheckoutService,
    SubscriptionPaymentConfirmationService,
    SubscriptionPriceChanged,
    subscription_invoice_queryset,
    subscription_payment_request_queryset,
)
from apps.users.constants import ProfileType
from django.utils.translation import gettext_lazy as _


@extend_schema_view(
    list=extend_schema(tags=["Loft Subscriptions"]),
)
class SubscriptionCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "LOFT_SUBSCRIPTION"
    queryset = SubscriptionInvoice.objects.none()
    http_method_names = ["get"]

    def list(self, request, *args, **kwargs):
        return Response(catalog_response_data(), status=status.HTTP_200_OK)


@extend_schema_view(
    create=extend_schema(
        tags=["Loft Subscriptions"],
        request=SubscriptionSelectionSerializer,
        responses={200: PricingResponseSerializer},
    ),
)
class SubscriptionReviseViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "LOFT_SUBSCRIPTION"
    serializer_class = SubscriptionSelectionSerializer
    queryset = SubscriptionInvoice.objects.none()
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = SubscriptionCheckoutService.execute(
            profile=request.user.active_profile,
            validated_selection=serializer.validated_data,
            dry_run=True,
        )
        return Response(pricing_response_data(result), status=status.HTTP_200_OK)


@extend_schema_view(
    create=extend_schema(
        tags=["Loft Subscriptions"],
        request=SubscriptionCheckoutSerializer,
        responses={201: CheckoutResponseSerializer},
    ),
)
class SubscriptionCheckoutViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "LOFT_SUBSCRIPTION"
    serializer_class = SubscriptionCheckoutSerializer
    queryset = SubscriptionInvoice.objects.none()
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = SubscriptionCheckoutService.execute(
                profile=request.user.active_profile,
                validated_selection=serializer.validated_data,
                dry_run=False,
                quote_token=serializer.validated_data["quote_token"],
                idempotency_key=serializer.validated_data["idempotency_key"],
            )
        except SubscriptionPriceChanged as exc:
            dry_run_result = type(
                "Result",
                (),
                {
                    "pricing": exc.pricing,
                    "dry_run": True,
                    "quote_token": None,
                    "quote_expires_at": None,
                },
            )
            data = pricing_response_data(dry_run_result)
            data["code"] = "SUBSCRIPTION_PRICE_CHANGED"
            return Response(data, status=status.HTTP_409_CONFLICT)

        response_data = {
            "invoice": SubscriptionInvoiceDetailSerializer(result.invoice).data,
            "payment_request": (
                SubscriptionPaymentRequestSerializer(result.payment_request).data
                if result.payment_request
                else None
            ),
            "subscription": (
                LoftSubscriptionSerializer(result.subscription).data
                if result.subscription
                else None
            ),
            "pricing": pricing_response_data(result),
        }
        return Response(response_data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(
        tags=["Loft Subscriptions"], responses={200: LoftSubscriptionSerializer}
    ),
)
class CurrentSubscriptionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "LOFT_SUBSCRIPTION"
    queryset = LoftSubscription.objects.none()
    http_method_names = ["get"]

    def list(self, request, *args, **kwargs):
        subscription = SubscriptionAccessService.get_current_subscription(
            request.user.active_profile
        )
        return Response(
            {
                "subscription": (
                    LoftSubscriptionSerializer(subscription).data
                    if subscription
                    else None
                )
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Loft Subscriptions"],
        responses={200: SubscriptionInvoiceListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=["Loft Subscriptions"],
        responses={200: SubscriptionInvoiceDetailSerializer},
    ),
)
class SubscriptionInvoiceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "LOFT_SUBSCRIPTION_INVOICE"
    manager_permission_map = {
        "list": "loft_subscriptions.invoices.list",
        "retrieve": "loft_subscriptions.invoices.retrieve",
    }
    queryset = SubscriptionInvoice.objects.all()
    http_method_names = ["get"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SubscriptionInvoiceDetailSerializer
        return SubscriptionInvoiceListSerializer

    def get_queryset(self):
        queryset = subscription_invoice_queryset()
        active_profile = self.request.user.active_profile
        if active_profile.type == ProfileType.MANAGER:
            return queryset
        return queryset.filter(owner_id=active_profile.pk)


@extend_schema_view(
    create=extend_schema(
        tags=["Loft Subscriptions"],
        responses={200: CheckoutResponseSerializer},
    ),
)
class SubscriptionPaymentConfirmViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "LOFT_SUBSCRIPTION_PAYMENT_REQUEST"
    manager_permission_map = {
        "create": "loft_subscriptions.payment_requests.confirm",
    }
    queryset = SubscriptionPaymentRequest.objects.all()
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        active_profile = request.user.active_profile
        if active_profile.type != ProfileType.MANAGER and not request.user.is_superuser:
            raise PermissionDenied(
                _("Only managers can confirm subscription payments.")
            )

        payment_request, subscription = SubscriptionPaymentConfirmationService.confirm(
            payment_request_id=kwargs["pk"],
            confirmed_by=active_profile,
        )
        return Response(
            {
                "payment_request": SubscriptionPaymentRequestSerializer(
                    payment_request
                ).data,
                "subscription": LoftSubscriptionSerializer(subscription).data,
                "invoice": SubscriptionInvoiceDetailSerializer(
                    payment_request.invoice
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class SubscriptionPaymentRequestViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "LOFT_SUBSCRIPTION_PAYMENT_REQUEST"
    manager_permission_map = {
        "list": "loft_subscriptions.payment_requests.list",
        "retrieve": "loft_subscriptions.payment_requests.retrieve",
    }
    serializer_class = SubscriptionPaymentRequestSerializer
    queryset = SubscriptionPaymentRequest.objects.all()
    http_method_names = ["get"]

    def get_queryset(self):
        queryset = subscription_payment_request_queryset()
        active_profile = self.request.user.active_profile
        if active_profile.type == ProfileType.MANAGER:
            return queryset
        return queryset.filter(owner_id=active_profile.pk)
