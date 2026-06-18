from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.models import UserAccount
from apps.loyalty.filters import PointTransactionFilter
from apps.loyalty.models import BadgeAward, PointTransaction
from apps.loyalty.serializers import (
    BadgeAwardCreateSerializer,
    BadgeAwardSerializer,
    BadgeDefinitionSerializer,
    PointBalanceSerializer,
    PointTransactionSerializer,
)
from apps.loyalty.services import BadgeService, PointService
from apps.users.constants import ProfileType


class PointBalanceAPIView(APIView):
    """Return the authenticated user's current point balance.

    Managers can pass ?user_id=<id> to view any user's balance.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Loyalty"],
        parameters=[
            OpenApiParameter(
                name="user_id",
                location=OpenApiParameter.QUERY,
                required=False,
                type=int,
                description="Manager-only: return balance for this user.",
            )
        ],
        responses={200: PointBalanceSerializer},
    )
    def get(self, request):
        user_id_param = request.query_params.get("user_id")

        if user_id_param:
            if request.user.role != ProfileType.MANAGER:
                raise PermissionDenied(
                    "Only managers can view another user's balance."
                )
            try:
                target_user = UserAccount.objects.get(pk=user_id_param)
            except UserAccount.DoesNotExist:
                raise NotFound("User not found.")
            balance = PointService.get_balance(target_user)
            serializer = PointBalanceSerializer(
                {
                    "user_id": target_user.pk,
                    "active_profile_id": None,
                    "balance": balance,
                }
            )
        else:
            active_profile = request.user.active_profile
            balance = PointService.get_balance(request.user)
            serializer = PointBalanceSerializer(
                {
                    "user_id": request.user.pk,
                    "active_profile_id": active_profile.pk,
                    "balance": balance,
                }
            )

        return Response(serializer.data, status=status.HTTP_200_OK)


class PointTransactionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Managers see all point transactions; users see their own history."""

    permission_classes = [IsAuthenticated]
    serializer_class = PointTransactionSerializer
    queryset = PointTransaction.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = PointTransactionFilter
    ordering_fields = [
        "created",
        "points",
        "balance_after",
    ]
    ordering = ["-created"]

    def get_queryset(self):
        queryset = PointTransaction.objects.select_related(
            "wallet",
            "user",
            "profile",
        ).order_by("-created")

        if self.request.user.role == ProfileType.MANAGER:
            return queryset

        return queryset.filter(user=self.request.user)


class BadgeViewSet(viewsets.GenericViewSet):
    """Badge catalog, manager awards, and current-profile earned badges."""

    permission_classes = [IsAuthenticated]
    queryset = BadgeAward.objects.all()

    def get_queryset(self):
        return BadgeAward.objects.select_related(
            "profile",
            "profile__user",
        ).order_by("-awarded_at", "-created")

    @extend_schema(
        tags=["Loyalty"],
        parameters=[
            OpenApiParameter(
                name="profile_id",
                location=OpenApiParameter.QUERY,
                required=False,
                type=int,
            )
        ],
        responses={200: BadgeDefinitionSerializer(many=True)},
    )
    def list(self, request):
        profile_id = request.query_params.get("profile_id")
        if profile_id:
            if request.user.role != ProfileType.MANAGER:
                raise PermissionDenied(
                    "Only manager profiles can list badge awards by profile."
                )
            queryset = self.get_queryset().filter(profile_id=profile_id)
            serializer = BadgeAwardSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = BadgeDefinitionSerializer(
            BadgeService.get_badge_catalog(),
            many=True,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Loyalty"],
        request=BadgeAwardCreateSerializer,
        responses={201: BadgeAwardSerializer},
    )
    def create(self, request):
        if request.user.role != ProfileType.MANAGER:
            raise PermissionDenied("Only manager profiles can award badges.")

        serializer = BadgeAwardCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        award = serializer.save()
        response_serializer = BadgeAwardSerializer(award)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Loyalty"],
        responses={200: BadgeAwardSerializer(many=True)},
    )
    def my(self, request):
        queryset = BadgeService.get_earned_badges(request.user.active_profile)
        serializer = BadgeAwardSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
