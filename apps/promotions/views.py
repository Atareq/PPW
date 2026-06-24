from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import NotFound
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    DiscountOffer,
    OfferTriggerType,
    UserPromotionGrant,
)
from apps.promotions.serializers import (
    CashbackAdjustmentSerializer,
    CashbackBalanceSerializer,
    CashbackOfferSerializer,
    CashbackTransactionSerializer,
    DiscountOfferSerializer,
    UserPromotionGrantSerializer,
)
from apps.promotions.services import CashbackOfferService, DiscountOfferService, UserPromotionGrantService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile


def discount_offer_queryset():
    return DiscountOffer.objects.select_related("asset", "created_by").order_by(
        "-created"
    )


def cashback_offer_queryset():
    return CashbackOffer.objects.select_related("asset", "created_by").order_by(
        "-created"
    )


def cashback_transaction_queryset():
    return CashbackTransaction.objects.select_related(
        "customer",
        "cashback_offer",
        "order_item",
        "adjusted_by",
    ).order_by("-created")


class CurrentDiscountOfferViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "DISCOUNT_OFFER"
    serializer_class = DiscountOfferSerializer
    queryset = DiscountOffer.objects.all()

    def get_queryset(self):
        return discount_offer_queryset().filter(
            is_active=True,
            trigger_type=OfferTriggerType.GLOBAL,
        )


class CurrentCashbackOfferViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "CASHBACK_OFFER"
    serializer_class = CashbackOfferSerializer
    queryset = CashbackOffer.objects.all()

    def get_queryset(self):
        return cashback_offer_queryset().filter(
            is_active=True,
            trigger_type=OfferTriggerType.GLOBAL,
        )


@extend_schema_view(
    list=extend_schema(tags=["Promotions"]),
    create=extend_schema(
        tags=["Promotions"],
        request=DiscountOfferSerializer,
        responses={201: DiscountOfferSerializer},
    ),
)
class AdminDiscountOfferViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "DISCOUNT_OFFER_ADMIN"
    manager_permission_map = {
        "list": "promotions.discount_offers.list",
        "create": "promotions.discount_offers.create",
    }
    serializer_class = DiscountOfferSerializer
    queryset = DiscountOffer.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = [
        "is_active",
        "trigger_type",
        "target_type",
        "category",
        "asset",
    ]
    ordering_fields = ["created", "modified", "priority", "value"]
    ordering = ["-created"]

    def get_queryset(self):
        return discount_offer_queryset()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.active_profile)


@extend_schema_view(
    list=extend_schema(tags=["Promotions"]),
    create=extend_schema(
        tags=["Promotions"],
        request=CashbackOfferSerializer,
        responses={201: CashbackOfferSerializer},
    ),
)
class AdminCashbackOfferViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "CASHBACK_OFFER_ADMIN"
    manager_permission_map = {
        "list": "promotions.cashback_offers.list",
        "create": "promotions.cashback_offers.create",
    }
    serializer_class = CashbackOfferSerializer
    queryset = CashbackOffer.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = [
        "is_active",
        "trigger_type",
        "target_type",
        "category",
        "asset",
    ]
    ordering_fields = ["created", "modified", "priority", "value"]
    ordering = ["-created"]

    def get_queryset(self):
        return cashback_offer_queryset()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.active_profile)


class DiscountOfferActivationViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "DISCOUNT_OFFER_ADMIN"
    serializer_class = DiscountOfferSerializer
    queryset = DiscountOffer.objects.all()
    activate = None

    def get_required_manager_permission(self):
        if self.activate:
            return "promotions.discount_offers.activate"
        return "promotions.discount_offers.deactivate"

    @extend_schema(tags=["Promotions"], request=None)
    def create(self, request, *args, **kwargs):
        offer = self.get_object()
        if self.activate:
            offer = DiscountOfferService.activate(offer)
        else:
            offer = DiscountOfferService.deactivate(offer)
        return Response(
            DiscountOfferSerializer(offer).data,
            status=status.HTTP_200_OK,
        )


class CashbackOfferActivationViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "CASHBACK_OFFER_ADMIN"
    serializer_class = CashbackOfferSerializer
    queryset = CashbackOffer.objects.all()
    activate = None

    def get_required_manager_permission(self):
        if self.activate:
            return "promotions.cashback_offers.activate"
        return "promotions.cashback_offers.deactivate"

    @extend_schema(tags=["Promotions"], request=None)
    def create(self, request, *args, **kwargs):
        offer = self.get_object()
        if self.activate:
            offer = CashbackOfferService.activate(offer)
        else:
            offer = CashbackOfferService.deactivate(offer)
        return Response(
            CashbackOfferSerializer(offer).data,
            status=status.HTTP_200_OK,
        )


class MyCashbackTransactionViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "CASHBACK"
    serializer_class = CashbackTransactionSerializer
    queryset = CashbackTransaction.objects.all()

    def get_queryset(self):
        active_profile = self.request.user.active_profile
        if active_profile.type != ProfileType.CUSTOMER:
            raise PermissionDenied(
                "Only customer profiles can view cashback transactions."
            )
        return cashback_transaction_queryset().filter(customer_id=active_profile.pk)


class CashbackBalanceViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "CASHBACK"
    manager_permission_map = {
        "retrieve": "cashback.balance.retrieve",
    }
    serializer_class = CashbackBalanceSerializer
    queryset = CashbackTransaction.objects.all()

    @extend_schema(tags=["Promotions"], responses={200: CashbackBalanceSerializer})
    def retrieve(self, request):
        customer_id = request.query_params.get("customer")
        active_profile = request.user.active_profile
        if customer_id:
            if active_profile.type != ProfileType.MANAGER:
                raise PermissionDenied(
                    "Only manager profiles can look up customer cashback balance."
                )
            try:
                customer = CustomerProfile.objects.get(pk=customer_id)
            except CustomerProfile.DoesNotExist as exc:
                raise NotFound("Customer profile was not found.") from exc
            return self._balance_response(customer)

        if active_profile.type != ProfileType.CUSTOMER:
            raise PermissionDenied("Only customer profiles can view cashback balance.")
        customer = CustomerProfile.objects.get(pk=active_profile.pk)
        return self._balance_response(customer)

    def _balance_response(self, customer):
        return Response(
            CashbackBalanceSerializer(
                {
                    "customer_id": customer.pk,
                    "cashback_balance": customer.cashback_balance,
                }
            ).data,
            status=status.HTTP_200_OK,
        )


class AdminCashbackTransactionViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "CASHBACK_TRANSACTION_ADMIN"
    manager_permission_map = {
        "list": "cashback.transactions.list",
    }
    serializer_class = CashbackTransactionSerializer
    queryset = CashbackTransaction.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = [
        "customer",
        "cashback_offer",
        "order_item",
        "transaction_type",
    ]
    ordering_fields = ["created", "amount", "balance_after"]
    ordering = ["-created"]

    def get_queryset(self):
        return cashback_transaction_queryset()


class CashbackAdjustmentViewSet(
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "CASHBACK_ADJUSTMENT_ADMIN"
    manager_permission_map = {
        "create": "cashback.adjustments.create",
    }
    serializer_class = CashbackAdjustmentSerializer
    queryset = CashbackTransaction.objects.all()

    @extend_schema(
        tags=["Promotions"],
        request=CashbackAdjustmentSerializer,
        responses={201: CashbackTransactionSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transaction = serializer.save()
        return Response(
            CashbackTransactionSerializer(transaction).data,
            status=status.HTTP_201_CREATED,
        )


class MyPromotionGrantViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Lists the calling user's active, non-expired promotion grants."""

    permission_classes = [HasAppAccess]
    # Temporary customer promotions scope until dedicated promotion grants permission exists.
    permission_resource = "CASHBACK"
    serializer_class = UserPromotionGrantSerializer
    queryset = UserPromotionGrant.objects.all()

    def get_queryset(self):
        return UserPromotionGrantService.get_active_grants_for_user(
            user=self.request.user,
            profile=self.request.user.active_profile,
        )
