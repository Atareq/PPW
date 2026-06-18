from django.db import transaction
from django.http import Http404
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from apps.authentication.services import switch_user_profile
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from apps.users.serializers import (
    CustomerProfileAdminSerializer,
    CustomerProfileMobileSerializer,
    SellerProfileAdminSerializer,
    SellerProfileMobileSerializer,
)


class SellerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for seller profile moderation and correction."""

    serializer_class = SellerProfileAdminSerializer
    queryset = SellerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]


class CustomerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for customer profile moderation and correction."""

    serializer_class = CustomerProfileAdminSerializer
    queryset = CustomerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]


class SellerProfileMobileViewSet(viewsets.ModelViewSet):
    """User-facing seller profile flow; creation starts inactive for review."""

    serializer_class = SellerProfileMobileSerializer
    queryset = SellerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        user = request.user
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

        return SellerProfile.objects.filter(
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

    def get_queryset(self):
        return CustomerProfile.objects.filter(
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
