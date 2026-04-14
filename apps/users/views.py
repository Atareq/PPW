from datetime import timezone

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


# still needs the approve of what the user can update and do
# We need to create API for adding a manager `is_staff = True`
class SellerProfileAdminViewSet(viewsets.ModelViewSet):
    serializer_class = SellerProfileAdminSerializer
    queryset = SellerProfile.objects.all()
    permission_classes = [permissions.IsAdminUser]


class CustomerProfileAdminViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerProfileAdminSerializer
    queryset = CustomerProfile.objects.all()
    permission_classes = [permissions.IsAdminUser]


class SellerProfileMobileViewSet(viewsets.ModelViewSet):
    serializer_class = SellerProfileMobileSerializer
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        user = request.user
        if user.profiles.filter(type=ProfileType.SELLER).exists():
            return Response(
                {"message": "Profile already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data=request.data.copy()
        data['nickname']=user.username
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        serializer.save(user=user, is_active=False, type="Seller")

        return Response(
            {"message": "Seller profile created successfully"},
            status=status.HTTP_201_CREATED,
        )

    def get_queryset(self):

        return SellerProfile.objects.filter(
            user=self.request.user, deleted__isnull=True, is_active=True
        )

    def get_object(self):
        if self.get_queryset():
            return self.get_queryset().first()
        raise Http404

    def perform_destroy(self, instance):

        if instance.is_active:
            instance.deleted = timezone.now()
            instance.save(update_fields=["deleted"])


class CustomerProfileMobileViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerProfileMobileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CustomerProfile.objects.filter(
            user=self.request.user, deleted__isnull=True
        )

    def perform_destroy(self, instance):
        if instance.is_active:
            other_profile = instance.user.profiles.exclude(
                id=instance.id
            ).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
        instance.deleted = True
        instance.save(update_fields=["deleted"])
