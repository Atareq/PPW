from rest_framework.response import Response
from rest_framework import viewsets, permissions, status
from apps.users.models import SellerProfile, CustomerProfile
from apps.users.serializers import (
    SellerProfileAdminSerializer,
    CustomerProfileAdminSerializer,
)


from rest_framework import viewsets, permissions
from apps.users.serializers import (
    SellerProfileMobileSerializer,
    CustomerProfileMobileSerializer,
)
from apps.authentication.services import switch_user_profile
from django.db import transaction


from apps.users.constants import ProfileType


# still needs the approve of what the user can update and do
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

        if user.profiles.filter(type="Seller").exists():
            return Response(
                {"message": "Profile already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        serializer.save(user=user, is_active=False, type="Seller")
        switch_user_profile(user, ProfileType.SELLER)

        return Response(
            {"message": "Seller profile created successfully"},
            status=status.HTTP_201_CREATED,
        )

    def get_queryset(self):
        return SellerProfile.objects.filter(
            user=self.request.user, deleted=False
        )

    def get_object(self):
        return self.get_queryset().first()

    def perform_destroy(self, instance):
        if instance.is_active:
            other_profile = instance.user.profiles.exclude(
                id=instance.id
            ).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
        instance.deleted = True
        instance.save(update_fields=["deleted"])


class CustomerProfileMobileViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerProfileMobileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CustomerProfile.objects.filter(
            user=self.request.user, deleted=False
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
