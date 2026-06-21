from django.db import transaction
from django.http import Http404
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from apps.authentication.services import switch_user_profile
from apps.general.permissions import IsSuperUser
from apps.users.constants import ProfileType
from apps.users.manager_permissions import get_manager_permission_codes
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile
from apps.users.serializers import (
    CustomerProfileAdminSerializer,
    CustomerProfileMobileSerializer,
    ManagerAdminCreateSerializer,
    ManagerAdminUpdateSerializer,
    ManagerPermissionUpdateSerializer,
    ManagerProfileAdminDetailSerializer,
    SellerProfileAdminSerializer,
    SellerProfileMobileSerializer,
)


class SellerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for seller profile moderation and correction."""

    serializer_class = SellerProfileAdminSerializer
    queryset = SellerProfile.objects.select_related('user').all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]


class CustomerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for customer profile moderation and correction."""

    serializer_class = CustomerProfileAdminSerializer
    queryset = CustomerProfile.objects.select_related('user').all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]


class ManagerProfileAdminViewSet(viewsets.ModelViewSet):
    """Superuser-only CRUD for manager-only accounts."""

    queryset = ManagerProfile.objects.select_related("user").all().order_by(
        "-created"
    )
    permission_classes = [IsSuperUser]

    def get_serializer_class(self):
        if self.action == "create":
            return ManagerAdminCreateSerializer
        if self.action == "partial_update":
            return ManagerAdminUpdateSerializer
        return ManagerProfileAdminDetailSerializer

    def create(self, request, *args, **kwargs):
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
        permissions_payload = []
        for code in sorted(get_manager_permission_codes()):
            module, level = code.split(".", 1)
            permissions_payload.append(
                {
                    "code": code,
                    "module": module,
                    "level": level,
                }
            )
        return Response(
            {"permissions": permissions_payload},
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


class SellerProfileMobileViewSet(viewsets.ModelViewSet):
    """User-facing seller profile flow; creation starts inactive for review."""

    serializer_class = SellerProfileMobileSerializer
    queryset = SellerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        user = request.user
        if user.profiles.filter(type=ProfileType.MANAGER, deleted__isnull=True).exists():
            return Response(
                {"message": "Manager accounts cannot create seller profiles"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.profiles.filter(type=ProfileType.SELLER).exists():
            return Response(
                {"message": "Profile already exists"},
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
        return Response(
            {"message": "Seller profile created successfully"},
            status=status.HTTP_201_CREATED,
        )

    def get_queryset(self):
        return SellerProfile.objects.select_related('user').filter(
            user=self.request.user, deleted__isnull=True, is_active=True
        ).order_by("-created")

    def get_object(self):
        if self.get_queryset():
            return self.get_queryset().first()
        raise Http404

    def perform_destroy(self, instance):
        """Soft-delete the profile and move the user away from it when active."""

        if instance.is_active:
            other_profile = instance.user.profiles.exclude(
                id=instance.id
            ).first()
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

    def create(self, request, *args, **kwargs):
        if request.user.profiles.filter(
            type=ProfileType.MANAGER,
            deleted__isnull=True,
        ).exists():
            return Response(
                {"message": "Manager accounts cannot create customer profiles"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        return CustomerProfile.objects.select_related('user').filter(
            user=self.request.user, deleted__isnull=True
        ).order_by("-created")

    def perform_destroy(self, instance):
        """Soft-delete the profile and move the user away from it when active."""

        if instance.is_active:
            other_profile = instance.user.profiles.exclude(
                id=instance.id
            ).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
            instance.is_active = False
            instance.is_valid = False
        instance.delete()
