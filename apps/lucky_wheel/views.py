from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.lucky_wheel.models import LuckyWheelPrize, LuckyWheelSpin
from apps.lucky_wheel.serializers import (
    LuckyWheelCurrentSerializer,
    LuckyWheelPrizeSerializer,
    LuckyWheelSegmentSerializer,
    LuckyWheelSpinRequestSerializer,
    LuckyWheelSpinResponseSerializer,
    LuckyWheelSpinSerializer,
    grant_for_spin,
)
from apps.lucky_wheel.services import (
    LuckyWheelGrantService,
    LuckyWheelSpinService,
)
from apps.loyalty.services import PointService
from apps.promotions.serializers import UserPromotionGrantSerializer
from apps.promotions.services import UserPromotionGrantService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile


class LuckyWheelCurrentViewSet(viewsets.GenericViewSet):
    """Read current wheel eligibility and displayable prize segments."""

    permission_classes = [IsAuthenticated]
    serializer_class = LuckyWheelCurrentSerializer
    queryset = LuckyWheelPrize.objects.none()

    @extend_schema(tags=["Lucky Wheel"], responses={200: LuckyWheelCurrentSerializer})
    def list(self, request, *args, **kwargs):
        profile = request.user.active_profile
        grants = list(LuckyWheelGrantService.active_grants(request.user, profile))
        remaining = sum(grant.remaining_attempts for grant in grants)
        segments = LuckyWheelPrize.objects.filter(is_active=True).order_by("id")
        promotion_grants = UserPromotionGrantService.get_active_grants_for_user(
            request.user,
            profile,
        )
        return Response(
            {
                "eligible": remaining > 0,
                "remaining_attempts": remaining,
                "cooldown_until": None,
                "segments": LuckyWheelSegmentSerializer(segments, many=True).data,
                "active_grants": [
                    {
                        "id": grant.pk,
                        "source_type": grant.source_type,
                        "source_id": grant.source_id,
                        "total_attempts": grant.total_attempts,
                        "remaining_attempts": grant.remaining_attempts,
                        "expires_at": grant.expires_at,
                        "is_active": grant.is_active,
                        "metadata": grant.metadata,
                        "created": grant.created,
                    }
                    for grant in grants
                ],
                "user_promotion_grants": UserPromotionGrantSerializer(
                    promotion_grants,
                    many=True,
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class LuckyWheelSpinViewSet(viewsets.GenericViewSet):
    """Spend one available attempt; the backend chooses the prize."""

    permission_classes = [IsAuthenticated]
    serializer_class = LuckyWheelSpinRequestSerializer
    queryset = LuckyWheelSpin.objects.all()

    @extend_schema(
        tags=["Lucky Wheel"],
        request=LuckyWheelSpinRequestSerializer,
        responses={200: LuckyWheelSpinResponseSerializer},
    )
    def create(self, request, *args, **kwargs):
        profile = request.user.active_profile
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        spin, _extra = LuckyWheelSpinService.spin(
            request.user,
            profile,
            serializer.validated_data["idempotency_key"],
        )
        promotion_grant = grant_for_spin(spin)
        balances = {"points": PointService.get_balance(request.user)}
        if profile.type == ProfileType.CUSTOMER:
            customer = CustomerProfile.objects.get(pk=profile.pk)
            balances["cashback"] = str(customer.cashback_balance)
        return Response(
            {
                "spin_id": spin.pk,
                "remaining_attempts": LuckyWheelGrantService.get_remaining_attempts(
                    request.user,
                    profile,
                ),
                "prize": spin.prize_snapshot,
                "awarded": spin.status == "awarded",
                "balances": balances,
                "user_promotion_grant": (
                    UserPromotionGrantSerializer(promotion_grant).data
                    if promotion_grant
                    else None
                ),
            },
            status=status.HTTP_200_OK,
        )


class MyLuckyWheelSpinViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """List the authenticated user's spin history."""

    permission_classes = [IsAuthenticated]
    serializer_class = LuckyWheelSpinSerializer
    queryset = LuckyWheelSpin.objects.all()

    def get_queryset(self):
        return (
            LuckyWheelSpin.objects.select_related("grant", "prize", "profile")
            .filter(user=self.request.user)
            .order_by("-created")
        )


@extend_schema_view(
    list=extend_schema(tags=["Lucky Wheel"]),
    create=extend_schema(tags=["Lucky Wheel"], request=LuckyWheelPrizeSerializer),
    partial_update=extend_schema(
        tags=["Lucky Wheel"],
        request=LuckyWheelPrizeSerializer,
    ),
)
class AdminLuckyWheelPrizeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manager prize configuration for weighted Lucky Wheel results."""

    permission_classes = [HasAppAccess]
    permission_resource = "LUCKY_WHEEL_PRIZE_ADMIN"
    manager_permission_map = {
        "list": "lucky_wheel.prizes.list",
        "create": "lucky_wheel.prizes.create",
        "partial_update": "lucky_wheel.prizes.update",
    }
    serializer_class = LuckyWheelPrizeSerializer
    queryset = LuckyWheelPrize.objects.select_related(
        "discount_offer",
        "cashback_offer",
    ).order_by("label", "id")


class AdminLuckyWheelSpinViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Manager read-only spin audit list."""

    permission_classes = [HasAppAccess]
    permission_resource = "LUCKY_WHEEL_SPIN_ADMIN"
    manager_permission_map = {"list": "lucky_wheel.spins.list"}
    serializer_class = LuckyWheelSpinSerializer
    queryset = LuckyWheelSpin.objects.select_related(
        "user",
        "profile",
        "grant",
        "prize",
    ).order_by("-created")
