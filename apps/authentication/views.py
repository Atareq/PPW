from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.authentication.services import switch_user_profile
from apps.authentication.services import build_token_for_user

from apps.authentication.serializers import (
    CustomTokenSerializer,
    CustomerRegisterSerializer,
)

from apps.authentication.serializers import CustomerRegisterSerializer
from django.db import transaction


class CustomerRegisterAPIView(APIView):
    permission_classes = []
    with transaction.atomic():

        def post(self, request):

            customer_register_serializer = CustomerRegisterSerializer(
                data=request.data
            )
            customer_register_serializer.is_valid(raise_exception=True)
            user = customer_register_serializer.save()

            return Response(
                {
                    "message": "User created successfully",
                },
                status=status.HTTP_201_CREATED,
            )


class LoginAPIView(TokenObtainPairView):
    permission_classes = []
    serializer_class = CustomTokenSerializer


class SwitchProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        new_profile = request.data.get("new_profile")

        switched = switch_user_profile(request.user, new_profile)

        if not switched:
            return Response(
                {"message": "Failed to switch profile. Profile not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token = build_token_for_user(request.user)

        return Response(
            {
                "access": str(token.access_token),
                "refresh": str(token),
            }
        )
