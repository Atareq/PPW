from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from apps.authentication.services import switch_user_profile
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from apps.users.serializers import (
    CustomerProfileAdminSerializer,
    CustomerProfileMobileSerializer,
    SellerDiscoverSerializer,
    SellerProfileAdminSerializer,
    SellerProfileMobileSerializer,
)


# still needs the approve of what the user can update and do
# We need to create API for adding a manager `is_staff = True`
class SellerProfileAdminViewSet(viewsets.ModelViewSet):
    serializer_class = SellerProfileAdminSerializer
    queryset = SellerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]


class CustomerProfileAdminViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerProfileAdminSerializer
    queryset = CustomerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAdminUser]


class SellerProfileMobileViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return SellerDiscoverSerializer
        return SellerProfileMobileSerializer

    def get_queryset(self):
        if self.action == "list":
            return (
                SellerProfile.objects.filter(
                    deleted__isnull=True,
                    is_valid=True,
                    activated=True,
                )
                .annotate(
                    active_auctions_count=Count(
                        "owned_auctions",
                        filter=Q(
                            owned_auctions__status="active",
                            owned_auctions__deleted__isnull=True,
                        ),
                    )
                )
                .order_by("-avg_rating", "-created")
            )
        return SellerProfile.objects.filter(
            user=self.request.user, deleted__isnull=True, is_active=True
        ).order_by("-created")

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

    def get_object(self):
        qs = SellerProfile.objects.filter(
            user=self.request.user, deleted__isnull=True, is_active=True
        ).order_by("-created")
        if qs.exists():
            return qs.first()
        raise Http404

    def perform_destroy(self, instance):
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
    serializer_class = CustomerProfileMobileSerializer
    queryset = CustomerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CustomerProfile.objects.filter(
            user=self.request.user, deleted__isnull=True
        ).order_by("-created")

    def perform_destroy(self, instance):
        if instance.is_active:
            other_profile = instance.user.profiles.exclude(
                id=instance.id
            ).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
            instance.is_active = False
            instance.is_valid = False
        instance.delete()
