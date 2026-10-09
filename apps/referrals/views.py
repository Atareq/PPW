from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.referrals.models import (
    LinkSharingCode,
    Referral,
    ReferralChallenge,
    ReferralChallengePrize,
    ReferralChallengeWinner,
)
from apps.referrals.serializers import (
    AdminReferralChallengeDetailSerializer,
    AdminReferralChallengeSerializer,
    AdminReferralChallengeWriteSerializer,
    AdminReferralSerializer,
    CurrentReferralChallengeSerializer,
    LinkSharingCodeSerializer,
    ReferralRedeemResponseSerializer,
    ReferralRedeemSerializer,
    ReferralRejectSerializer,
    ReferralStatusSerializer,
)
from apps.referrals.services import ReferralChallengeService, ReferralService


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
    """Redeem a code for a new account; the referral starts ``pending``.

    No PP is paid here. The inviter is rewarded only after a manager approves.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ReferralRedeemSerializer
    queryset = LinkSharingCode.objects.all()

    @extend_schema(
        tags=["Referrals"],
        request=ReferralRedeemSerializer,
        responses={200: ReferralRedeemResponseSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        referral = ReferralService.redeem_code(
            request.user.active_profile,
            serializer.validated_data["code"],
        )
        return Response(
            ReferralRedeemResponseSerializer(referral.code).data,
            status=status.HTTP_200_OK,
        )


class MyReferralViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Referrals the current account invited, with their review status."""

    permission_classes = [IsAuthenticated]
    serializer_class = ReferralStatusSerializer
    queryset = Referral.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status"]

    def get_queryset(self):
        return Referral.objects.filter(inviter_user=self.request.user).order_by(
            "-redeemed_at",
            "-id",
        )

    @extend_schema(tags=["Referrals"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class AdminReferralViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Manager referral review queue."""

    permission_classes = [HasAppAccess]
    permission_resource = "REFERRAL_ADMIN"
    manager_permission_map = {"list": "referrals.list"}
    serializer_class = AdminReferralSerializer
    queryset = Referral.objects.select_related(
        "code",
        "inviter_user",
        "referred_user",
    ).order_by("-redeemed_at", "-id")
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "inviter_user", "referred_user"]

    @extend_schema(tags=["Referrals"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class AdminReferralApproveViewSet(viewsets.GenericViewSet):
    """Approve a pending referral; the inviter reward follows the approval."""

    permission_classes = [HasAppAccess]
    permission_resource = "REFERRAL_ADMIN"
    manager_permission_map = {"create": "referrals.approve"}
    serializer_class = AdminReferralSerializer
    queryset = Referral.objects.all()

    @extend_schema(
        tags=["Referrals"],
        request=None,
        responses={200: AdminReferralSerializer},
    )
    def create(self, request, pk=None, *args, **kwargs):
        referral = ReferralService.approve(pk, request.user)
        return Response(AdminReferralSerializer(referral).data)


class AdminReferralRejectViewSet(viewsets.GenericViewSet):
    """Reject a pending referral; it never becomes reward-eligible."""

    permission_classes = [HasAppAccess]
    permission_resource = "REFERRAL_ADMIN"
    manager_permission_map = {"create": "referrals.reject"}
    serializer_class = ReferralRejectSerializer
    queryset = Referral.objects.all()

    @extend_schema(
        tags=["Referrals"],
        request=ReferralRejectSerializer,
        responses={200: AdminReferralSerializer},
    )
    def create(self, request, pk=None, *args, **kwargs):
        serializer = ReferralRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        referral = ReferralService.reject(
            pk,
            request.user,
            serializer.validated_data["reason"],
        )
        return Response(AdminReferralSerializer(referral).data)


class CurrentReferralChallengeViewSet(viewsets.GenericViewSet):
    """Current referral challenge for the caller's account.

    The active challenge, or the nearest upcoming one; 404 when neither
    exists. Scores are approved referrals of the account, not PP.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = CurrentReferralChallengeSerializer
    queryset = ReferralChallenge.objects.all()

    @extend_schema(
        tags=["Referrals"],
        responses={200: CurrentReferralChallengeSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        overview = ReferralChallengeService.current_overview(request.user)
        return Response(CurrentReferralChallengeSerializer(overview).data)


class AdminReferralChallengeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manager challenge configuration; dates and prizes freeze at the start."""

    permission_classes = [HasAppAccess]
    permission_resource = "REFERRAL_CHALLENGE_ADMIN"
    manager_permission_map = {
        "list": "referral_challenges.list",
        "retrieve": "referral_challenges.retrieve",
        "create": "referral_challenges.create",
        "partial_update": "referral_challenges.update",
    }
    queryset = ReferralChallenge.objects.prefetch_related(
        Prefetch(
            "prizes",
            queryset=ReferralChallengePrize.objects.order_by("rank", "id"),
        ),
        Prefetch(
            "winners",
            queryset=ReferralChallengeWinner.objects.select_related("user"),
        ),
    ).order_by("-starts_at", "-id")

    def get_serializer_class(self):
        if self.action == "list":
            return AdminReferralChallengeSerializer
        if self.action == "retrieve":
            return AdminReferralChallengeDetailSerializer
        return AdminReferralChallengeWriteSerializer

    def perform_create(self, serializer):
        serializer.instance = ReferralChallengeService.create_challenge(
            created_by=self.request.user,
            **serializer.validated_data,
        )

    def perform_update(self, serializer):
        serializer.instance = ReferralChallengeService.update_challenge(
            serializer.instance.pk,
            serializer.validated_data,
        )

    @extend_schema(tags=["Referrals"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        tags=["Referrals"],
        request=AdminReferralChallengeWriteSerializer,
        responses={201: AdminReferralChallengeDetailSerializer},
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(tags=["Referrals"])
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        tags=["Referrals"],
        request=AdminReferralChallengeWriteSerializer,
        responses={200: AdminReferralChallengeDetailSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)


class AdminReferralChallengeFinalizeViewSet(viewsets.GenericViewSet):
    """Freeze the Top 3 winners of an ended challenge (idempotent).

    Refused while referrals redeemed in the window are still pending.
    """

    permission_classes = [HasAppAccess]
    permission_resource = "REFERRAL_CHALLENGE_ADMIN"
    manager_permission_map = {"create": "referral_challenges.finalize"}
    serializer_class = AdminReferralChallengeDetailSerializer
    queryset = ReferralChallenge.objects.all()

    @extend_schema(
        tags=["Referrals"],
        request=None,
        responses={200: AdminReferralChallengeDetailSerializer},
    )
    def create(self, request, pk=None, *args, **kwargs):
        challenge = ReferralChallengeService.finalize(pk, request.user)
        return Response(AdminReferralChallengeDetailSerializer(challenge).data)
