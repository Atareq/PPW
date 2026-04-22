from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.authentication.serializers import (
    CustomerRegisterSerializer,
    CustomerTokenSerializer,
    ManagerRegisterSerializer,
)
from apps.authentication.services import (
    build_token_for_user,
    switch_user_profile,
)


class CustomerRegisterAPIView(APIView):
    permission_classes = []

    def post(self, request):
        with transaction.atomic():

            customer_register_serializer = CustomerRegisterSerializer(
                data=request.data
            )
            customer_register_serializer.is_valid(raise_exception=True)
            customer_register_serializer.save()

            return Response(
                {
                    "message": "User created successfully",
                },
                status=status.HTTP_201_CREATED,
            )


class ManagerRegisterAPIView(APIView):
    permission_classes = []  # Needs to be for admin users only

    def post(self, request):
        with transaction.atomic():

            customer_register_serializer = ManagerRegisterSerializer(
                data=request.data
            )
            customer_register_serializer.is_valid(raise_exception=True)
            customer_register_serializer.save()

        return Response(
            {
                "message": "User created successfully",
            },
            status=status.HTTP_201_CREATED,
        )


class LoginAPIView(TokenObtainPairView):
    permission_classes = []
    serializer_class = CustomerTokenSerializer


class SwitchProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        new_profile = request.data.get("new_profile")

        switched, profile_type = switch_user_profile(request.user, new_profile)

        if not switched:
            return Response(
                {"message": "Failed to switch profile. Profile not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token = build_token_for_user(request.user)

        return Response(
            {
                "profile": profile_type,
                "access": str(token.access_token),
                "refresh": str(token),
            }
        )
