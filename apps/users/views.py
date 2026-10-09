from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.authentication.services import switch_user_profile
from apps.general.authorizations import HasAppAccess
from apps.general.permissions import IsNotManagerProfile, IsSuperUser
from apps.users.constants import ProfileType
from apps.users.manager_permissions import get_manager_permission_groups
from apps.users.models import (
    CustomerProfile,
    ManagerProfile,
    ProfileBalanceTransaction,
    SellerProfile,
    SellerRoom,
    SellerRoomFollow,
)
from apps.users.serializers import (
    CustomerProfileAdminSerializer,
    CustomerProfileMobileSerializer,
    ManagerAdminCreateSerializer,
    ManagerAdminUpdateSerializer,
    ManagerPermissionUpdateSerializer,
    ManagerProfileAdminDetailSerializer,
    ProfileBalanceAdjustmentSerializer,
    ProfileBalanceTransactionSerializer,
    SellerProfileAdminSerializer,
    SellerProfileMobileSerializer,
    SellerRoomFollowActionSerializer,
    SellerRoomFollowSerializer,
    SellerRoomPublicSerializer,
    SellerRoomSerializer,
    SellerRoomUnfollowResponseSerializer,
)
from apps.users.services import SellerRoomFollowService, SellerRoomService


class SellerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for seller profile moderation and correction."""

    serializer_class = SellerProfileAdminSerializer
    queryset = SellerProfile.objects.select_related("user").all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated, HasAppAccess]
    manager_permission_map = {
        "list": "users.sellers.list",
        "retrieve": "users.sellers.retrieve",
        "update": "users.sellers.update",
        "partial_update": "users.sellers.update",
    }


class CustomerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for customer profile moderation and correction."""

    serializer_class = CustomerProfileAdminSerializer
    queryset = CustomerProfile.objects.select_related("user").all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated, HasAppAccess]
    manager_permission_map = {
        "list": "users.customers.list",
        "retrieve": "users.customers.retrieve",
        "update": "users.customers.update",
        "partial_update": "users.customers.update",
    }


class ManagerProfileAdminViewSet(viewsets.ModelViewSet):
    """Manager account reads with superuser-only mutation."""

    queryset = ManagerProfile.objects.select_related("user").all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated, HasAppAccess]
    manager_permission_map = {
        "list": "managers.list",
        "retrieve": "managers.retrieve",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return ManagerAdminCreateSerializer
        if self.action == "partial_update":
            return ManagerAdminUpdateSerializer
        return ManagerProfileAdminDetailSerializer

    def create(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(_("Only superusers can create managers."))
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        manager_profile = serializer.save()
        response_serializer = ManagerProfileAdminDetailSerializer(
            manager_profile,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(_("Only superusers can update managers."))
        manager_profile = self.get_object()
        serializer = self.get_serializer(
            manager_profile,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        response_serializer = ManagerProfileAdminDetailSerializer(
            manager_profile,
            context=self.get_serializer_context(),
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def perform_destroy(self, instance):
        if not self.request.user.is_superuser:
            raise PermissionDenied(_("Only superusers can delete managers."))
        instance.is_active = False
        instance.is_valid = False
        instance.save(update_fields=["is_active", "is_valid"])
        instance.delete()
        instance.user.is_active = False
        instance.user.is_staff = False
        instance.user.save(update_fields=["is_active", "is_staff"])


class ManagerPermissionRegistryViewSet(viewsets.GenericViewSet):
    permission_classes = [IsSuperUser]
    queryset = ManagerProfile.objects.none()

    def list(self, request, *args, **kwargs):
        return Response(
            {"groups": get_manager_permission_groups()},
            status=status.HTTP_200_OK,
        )


class ManagerPermissionUpdateViewSet(viewsets.GenericViewSet):
    permission_classes = [IsSuperUser]
    queryset = ManagerProfile.objects.select_related("user").all()
    serializer_class = ManagerPermissionUpdateSerializer

    def partial_update(self, request, *args, **kwargs):
        manager_profile = self.get_object()
        serializer = self.get_serializer(
            manager_profile,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            ManagerProfileAdminDetailSerializer(
                manager_profile,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )


class ProfileBalanceAdjustmentViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated, HasAppAccess]
    manager_permission_map = {"create": "profile_balances.adjust"}
    serializer_class = ProfileBalanceAdjustmentSerializer
    queryset = ProfileBalanceTransaction.objects.all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        balance_transaction = serializer.save()
        return Response(
            ProfileBalanceTransactionSerializer(balance_transaction).data,
            status=status.HTTP_201_CREATED,
        )


class SellerProfileMobileViewSet(viewsets.ModelViewSet):
    """User-facing seller profile flow; creation starts inactive for review."""

    serializer_class = SellerProfileMobileSerializer
    queryset = SellerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        user = request.user
        if user.profiles.filter(
            type=ProfileType.MANAGER, deleted__isnull=True
        ).exists():
            return Response(
                {"message": _("Manager accounts cannot create seller profiles")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.profiles.filter(type=ProfileType.SELLER).exists():
            return Response(
                {"message": _("Profile already exists")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = request.data.copy()
        data["nickname"] = user.username
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        seller_profile = serializer.save(
            user=user,
            is_active=False,
            type=ProfileType.SELLER,
        )
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.SELLER_PROFILE_CREATED,
            actor=seller_profile,
            profile=seller_profile,
        )
        from apps.promotions.services import OnboardingHighlightBenefitService

        OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
            user=user,
            profile=seller_profile,
        )
        return Response(
            {"message": _("Seller profile created successfully")},
            status=status.HTTP_201_CREATED,
        )

    def get_queryset(self):
        return (
            SellerProfile.objects.select_related("user")
            .filter(user=self.request.user, deleted__isnull=True, is_active=True)
            .order_by("-created")
        )

    def get_object(self):
        if self.get_queryset():
            return self.get_queryset().first()
        raise Http404

    def perform_destroy(self, instance):
        """Soft-delete the profile and move the user away from it when active."""

        if instance.is_active:
            other_profile = instance.user.profiles.exclude(id=instance.id).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
            instance.is_active = False
            instance.is_valid = False
        instance.delete()


class CustomerProfileMobileViewSet(viewsets.ModelViewSet):
    """User-facing customer profile flow scoped to the authenticated account."""

    serializer_class = CustomerProfileMobileSerializer
    queryset = CustomerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        if request.user.profiles.filter(
            type=ProfileType.MANAGER,
            deleted__isnull=True,
        ).exists():
            return Response(
                {"message": _("Manager accounts cannot create customer profiles")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.user.profiles.filter(type=ProfileType.CUSTOMER).exists():
            return Response(
                {"message": _("Profile already exists")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        response = super().create(request, *args, **kwargs)
        customer_profile = self.get_queryset().first()
        if customer_profile is not None:
            from apps.loyalty.constants import RewardEventType
            from apps.loyalty.reward_engine import RewardEngine
            from apps.promotions.services import OnboardingHighlightBenefitService

            RewardEngine.handle_event(
                RewardEventType.CUSTOMER_PROFILE_CREATED,
                actor=customer_profile,
                profile=customer_profile,
            )
            OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding(
                user=request.user,
                profile=customer_profile,
            )
        return response

    def perform_create(self, serializer):
        has_active_profile = self.request.user.profiles.filter(
            is_active=True,
            deleted__isnull=True,
        ).exists()
        serializer.save(
            user=self.request.user,
            is_active=not has_active_profile,
            type=ProfileType.CUSTOMER,
        )

    def get_queryset(self):
        return (
            CustomerProfile.objects.select_related("user")
            .filter(user=self.request.user, deleted__isnull=True)
            .order_by("-created")
        )

    def perform_destroy(self, instance):
        """Soft-delete the profile and move the user away from it when active."""

        if instance.is_active:
            other_profile = instance.user.profiles.exclude(id=instance.id).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
            instance.is_active = False
            instance.is_valid = False
        instance.delete()


class SellerRoomViewSet(viewsets.ModelViewSet):
    """Seller-owned room CRUD and public room retrieval (BE-ROOM-01)."""

    permission_classes = [permissions.IsAuthenticated, HasAppAccess]
    permission_resource = "SELLER_ROOM"
    permission_owner_field = "seller_id"
    allow_scoped_read_access = True
    serializer_class = SellerRoomSerializer
    queryset = (
        SellerRoom.objects.select_related("seller")
        .filter(deleted__isnull=True)
        .order_by("-created", "-id")
    )

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return SellerRoomPublicSerializer
        return SellerRoomSerializer

    def get_queryset(self):
        base_qs = (
            SellerRoom.objects.select_related("seller")
            .filter(deleted__isnull=True)
            .order_by("-created", "-id")
        )
        user = self.request.user
        role = getattr(user, "role", None)

        if self.action == "list":
            seller_param = self.request.query_params.get(
                "seller"
            ) or self.request.query_params.get("seller_id")
            if seller_param is not None:
                try:
                    seller_id = int(seller_param)
                    if seller_id <= 0:
                        raise ValueError
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        {"seller": [_("Invalid seller id.")]}
                    ) from exc
                if (
                    role == ProfileType.SELLER
                    and user.active_profile.pk == seller_id
                ):
                    return SellerRoomService.rooms_for_owner(seller_id)
                return SellerRoomService.public_rooms_for_seller(seller_id)

            if role == ProfileType.SELLER:
                return SellerRoomService.rooms_for_owner(user.active_profile)

            return base_qs.filter(
                seller__deleted__isnull=True,
                seller__is_active=True,
                seller__activated=True,
                seller__is_valid=True,
            )

        if self.action == "retrieve":
            if role == ProfileType.SELLER:
                return base_qs.filter(
                    Q(seller=user.active_profile)
                    | Q(
                        seller__deleted__isnull=True,
                        seller__is_active=True,
                        seller__activated=True,
                        seller__is_valid=True,
                    )
                )
            return base_qs.filter(
                seller__deleted__isnull=True,
                seller__is_active=True,
                seller__activated=True,
                seller__is_valid=True,
            )

        return base_qs

    def perform_create(self, serializer):
        serializer.instance = SellerRoomService.create_room(
            user=self.request.user,
            validated_data=serializer.validated_data,
        )

    def perform_update(self, serializer):
        serializer.instance = SellerRoomService.update_room(
            room=serializer.instance,
            user=self.request.user,
            validated_data=serializer.validated_data,
        )

    def perform_destroy(self, instance):
        SellerRoomService.delete_room(
            room=instance,
            user=self.request.user,
        )


class SellerRoomFollowViewSet(viewsets.GenericViewSet):
    """Idempotently follow a public seller room at the UserAccount level (BE-ROOM-01)."""

    permission_classes = [permissions.IsAuthenticated, IsNotManagerProfile]
    queryset = SellerRoomFollow.objects.all()
    serializer_class = SellerRoomFollowSerializer
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        action_serializer = SellerRoomFollowActionSerializer(data=request.data)
        action_serializer.is_valid(raise_exception=True)
        follow = SellerRoomFollowService.follow(
            user=request.user,
            room_id=kwargs["pk"],
        )
        return Response(
            SellerRoomFollowSerializer(
                follow,
                context={"request": request},
            ).data,
            status=status.HTTP_200_OK,
        )


class SellerRoomUnfollowViewSet(viewsets.GenericViewSet):
    """Idempotently unfollow a seller room at the UserAccount level (BE-ROOM-01)."""

    permission_classes = [permissions.IsAuthenticated, IsNotManagerProfile]
    queryset = SellerRoomFollow.objects.all()
    serializer_class = SellerRoomUnfollowResponseSerializer
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        action_serializer = SellerRoomFollowActionSerializer(data=request.data)
        action_serializer.is_valid(raise_exception=True)
        room = SellerRoomFollowService.unfollow(
            user=request.user,
            room_id=kwargs["pk"],
        )
        return Response(
            {
                "room_id": room.pk,
                "is_followed": False,
            },
            status=status.HTTP_200_OK,
        )

