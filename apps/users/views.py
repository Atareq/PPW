from django.db import transaction
from django.http import Http404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
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
    queryset = SellerProfile.objects.select_related('user').all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]

    @action(detail=False, methods=["post"], url_path="bulk-suspend")
    def bulk_suspend(self, request):
        ids = request.data.get("ids", [])
        updated = SellerProfile.objects.filter(pk__in=ids).update(is_valid=False)
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-restore")
    def bulk_restore(self, request):
        ids = request.data.get("ids", [])
        updated = SellerProfile.objects.filter(pk__in=ids).update(is_valid=True)
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-activate")
    def bulk_activate(self, request):
        ids = request.data.get("ids", [])
        updated = SellerProfile.objects.filter(pk__in=ids).update(activated=True)
        return Response({"updated": updated}, status=status.HTTP_200_OK)


class CustomerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for customer profile moderation and correction."""

    serializer_class = CustomerProfileAdminSerializer
    queryset = CustomerProfile.objects.select_related('user').all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]

    @action(detail=False, methods=["post"], url_path="bulk-suspend")
    def bulk_suspend(self, request):
        ids = request.data.get("ids", [])
        updated = CustomerProfile.objects.filter(pk__in=ids).update(is_valid=False)
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-restore")
    def bulk_restore(self, request):
        ids = request.data.get("ids", [])
        updated = CustomerProfile.objects.filter(pk__in=ids).update(is_valid=True)
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-activate")
    def bulk_activate(self, request):
        ids = request.data.get("ids", [])
        updated = CustomerProfile.objects.filter(pk__in=ids).update(activated=True)
        return Response({"updated": updated}, status=status.HTTP_200_OK)


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

        serializer.save(user=user, is_active=False, type=ProfileType.SELLER)

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
