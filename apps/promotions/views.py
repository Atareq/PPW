from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    DiscountOffer,
    OfferTriggerType,
    PromotionCampaign,
    PromotionCampaignEffect,
    PromotionCampaignStatus,
    UserPromotionGrant,
)
from apps.promotions.serializers import (
    CashbackAdjustmentSerializer,
    CashbackBalanceSerializer,
    CashbackOfferSerializer,
    CashbackTransactionSerializer,
    DiscountOfferSerializer,
    OnboardingHighlightBenefitSerializer,
    PromotionCampaignEffectSerializer,
    PromotionCampaignSerializer,
    PublicPromotionCampaignSerializer,
    UserPromotionGrantSerializer,
)
from apps.promotions.services import (
    CashbackOfferService,
    DiscountOfferService,
    OnboardingHighlightBenefitService,
    PromotionCampaignEffectService,
    PromotionCampaignService,
    UserPromotionGrantService,
)
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
                _("Only customer profiles can view cashback transactions.")
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
                    _("Only manager profiles can look up customer cashback balance.")
                )
            try:
                customer = CustomerProfile.objects.get(pk=customer_id)
            except CustomerProfile.DoesNotExist as exc:
                raise NotFound(_("Customer profile was not found.")) from exc
            return self._balance_response(customer)

        if active_profile.type != ProfileType.CUSTOMER:
            raise PermissionDenied(_("Only customer profiles can view cashback balance."))
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



def _get_optional_creator_profile(user):
    return PromotionCampaignService._resolve_creator_profile(user)


def promotion_campaign_queryset():
    return PromotionCampaign.objects.select_related("created_by").order_by(
        "starts_at",
        "id",
    )


class AdminPromotionCampaignViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manager CRUD for scheduled promotion campaigns (BE-PRO-01)."""

    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "PROMOTION_CAMPAIGN_ADMIN"
    manager_permission_map = {
        "list": "promotions.campaigns.list",
        "retrieve": "promotions.campaigns.retrieve",
        "create": "promotions.campaigns.create",
        "partial_update": "promotions.campaigns.update",
        "update": "promotions.campaigns.update",
    }
    serializer_class = PromotionCampaignSerializer
    queryset = PromotionCampaign.objects.all()

    def get_queryset(self):
        queryset = promotion_campaign_queryset()
        campaign_type = self.request.query_params.get("campaign_type")
        if campaign_type:
            queryset = queryset.filter(campaign_type=campaign_type)

        is_enabled_param = self.request.query_params.get("is_enabled")
        if is_enabled_param is not None and is_enabled_param != "":
            lowered = is_enabled_param.strip().lower()
            if lowered in {"true", "1"}:
                queryset = queryset.filter(is_enabled=True)
            elif lowered in {"false", "0"}:
                queryset = queryset.filter(is_enabled=False)

        effective_status = self.request.query_params.get("effective_status")
        if effective_status:
            now = timezone.now()
            if effective_status == PromotionCampaignStatus.DISABLED:
                queryset = queryset.filter(is_enabled=False)
            elif effective_status == PromotionCampaignStatus.SCHEDULED:
                queryset = queryset.filter(is_enabled=True, starts_at__gt=now)
            elif effective_status == PromotionCampaignStatus.ACTIVE:
                queryset = queryset.filter(
                    is_enabled=True,
                    starts_at__lte=now,
                    ends_at__gt=now,
                )
            elif effective_status == PromotionCampaignStatus.ENDED:
                queryset = queryset.filter(is_enabled=True, ends_at__lte=now)
            else:
                raise ValidationError(
                    {"effective_status": [_("Invalid effective status filter.")]}
                )

        return queryset

    def perform_create(self, serializer):
        serializer.instance = PromotionCampaignService.create(
            serializer.validated_data,
            created_by=_get_optional_creator_profile(self.request.user),
        )

    def perform_update(self, serializer):
        serializer.instance = PromotionCampaignService.update(
            serializer.instance,
            serializer.validated_data,
        )


@extend_schema_view(
    create=extend_schema(
        request=None,
        responses={200: PromotionCampaignSerializer},
    )
)
class AdminPromotionCampaignActivationViewSet(viewsets.GenericViewSet):
    """Enable or disable a scheduled promotion campaign (BE-PRO-01)."""

    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "PROMOTION_CAMPAIGN_ADMIN"
    serializer_class = PromotionCampaignSerializer
    queryset = PromotionCampaign.objects.all()
    enable = True

    def get_required_manager_permission(self):
        return (
            "promotions.campaigns.enable"
            if self.enable
            else "promotions.campaigns.disable"
        )

    def get_queryset(self):
        return promotion_campaign_queryset()

    def create(self, request, *args, **kwargs):
        campaign = self.get_object()
        campaign = (
            PromotionCampaignService.enable(campaign)
            if self.enable
            else PromotionCampaignService.disable(campaign)
        )
        serializer = self.get_serializer(campaign)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ActivePromotionCampaignViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """List currently active scheduled promotion campaigns (BE-PRO-01)."""

    permission_classes = [IsAuthenticated]
    serializer_class = PublicPromotionCampaignSerializer
    queryset = PromotionCampaign.objects.none()

    def get_queryset(self):
        campaign_type = self.request.query_params.get("campaign_type")
        return PromotionCampaignService.active_campaigns(
            campaign_type=campaign_type or None
        )



class AdminPromotionCampaignEffectViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Manager CRUD for service-level promotion campaign effects (BE-PRO-02)."""

    permission_classes = [IsAuthenticated, HasAppAccess]
    permission_resource = "PROMOTION_CAMPAIGN_EFFECT_ADMIN"
    manager_permission_map = {
        "list": "promotions.campaign_effects.list",
        "retrieve": "promotions.campaign_effects.retrieve",
        "create": "promotions.campaign_effects.create",
        "partial_update": "promotions.campaign_effects.update",
        "update": "promotions.campaign_effects.update",
        "destroy": "promotions.campaign_effects.delete",
    }
    serializer_class = PromotionCampaignEffectSerializer
    queryset = PromotionCampaignEffect.objects.all()

    def get_queryset(self):
        return PromotionCampaignEffectService.list_for_campaign(
            self.kwargs["campaign_id"]
        )

    def perform_create(self, serializer):
        serializer.instance = PromotionCampaignEffectService.create(
            self.kwargs["campaign_id"],
            serializer.validated_data,
            created_by=_get_optional_creator_profile(self.request.user),
        )

    def perform_update(self, serializer):
        serializer.instance = PromotionCampaignEffectService.update(
            serializer.instance,
            serializer.validated_data,
            campaign=self.kwargs["campaign_id"],
        )

    def perform_destroy(self, instance):
        PromotionCampaignEffectService.delete(
            instance,
            campaign=self.kwargs["campaign_id"],
        )



class OnboardingHighlightBenefitViewSet(viewsets.GenericViewSet):
    """Read-only eligibility status for the calling user's 20% first highlight benefit (BE-PRO-03)."""

    permission_classes = [IsAuthenticated]
    serializer_class = OnboardingHighlightBenefitSerializer
    queryset = UserPromotionGrant.objects.none()

    @extend_schema(
        tags=["Promotions"],
        responses={200: OnboardingHighlightBenefitSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        status_payload = OnboardingHighlightBenefitService.get_benefit_status(
            request.user
        )
        serializer = self.get_serializer(status_payload)
        return Response(serializer.data, status=status.HTTP_200_OK)
