"""
Tests for user registration endpoints.

Tests:
- CustomerRegisterAPIView
- ManagerRegisterAPIView
"""

import pytest
from rest_framework import status
from django.test import override_settings

from apps.users.constants import ProfileType
from tests.factories import UserAccountFactory


@pytest.mark.django_db
class TestCustomerRegistration:
    """Tests for customer user registration."""

    def test_register_valid_customer(self, api_client):
        """Test registering a new customer with valid data."""
        data = {
            "phone_number": "+201001234567",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        }

        response = api_client.post("/api/auth/register/customer/", data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["message"] == "User created successfully"

    def test_register_duplicate_phone_number(self, api_client, user):
        """Test registration fails with duplicate phone number."""
        data = {
            "phone_number": str(user.phone_number),
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        }

        response = api_client.post("/api/auth/register/customer/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone_number" in response.data

    def test_register_password_mismatch(self, api_client):
        """Test registration fails when passwords don't match."""
        data = {
            "phone_number": "+201001234567",
            "password": "SecurePass123!",
            "password_confirm": "DifferentPass123!",
        }

        response = api_client.post("/api/auth/register/customer/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_missing_required_fields(self, api_client):
        """Test registration fails with missing fields."""
        data = {
            "phone_number": "+201001234567",
            # Missing password and password_confirm
        }

        response = api_client.post("/api/auth/register/customer/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_invalid_phone_format(self, api_client):
        """Test registration fails with invalid phone format."""
        data = {
            "phone_number": "invalid-phone",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        }

        response = api_client.post("/api/auth/register/customer/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_creates_seller_profile(self, api_client):
        """Test that registration creates a SELLER profile by default."""
        from apps.authentication.models import UserAccount

        data = {
            "phone_number": "+201001234567",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        }

        response = api_client.post("/api/auth/register/customer/", data)

        assert response.status_code == status.HTTP_201_CREATED

        user = UserAccount.objects.get(phone_number="+201001234567")
        assert user.active_profile is not None
        assert user.active_profile.type == ProfileType.SELLER


@pytest.mark.django_db
class TestManagerRegistration:
    """Tests for manager user registration."""

    def test_register_valid_manager(self, api_client):
        """Test registering a new manager with valid data."""
        data = {
            "phone_number": "+201001234567",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        }

        response = api_client.post("/api/auth/register/manager/", data)

        assert response.status_code == status.HTTP_201_CREATED

    def test_register_manager_creates_manager_profile(self, api_client):
        """Test that manager registration creates MANAGER profile."""
        from apps.authentication.models import UserAccount

        data = {
            "phone_number": "+201001234567",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        }

        response = api_client.post("/api/auth/register/manager/", data)

        assert response.status_code == status.HTTP_201_CREATED

        user = UserAccount.objects.get(phone_number="+201001234567")
        assert user.active_profile is not None
        assert user.active_profile.type == ProfileType.MANAGER

    def test_register_manager_duplicate_phone(self, api_client, user):
        """Test manager registration fails with duplicate phone."""
        data = {
            "phone_number": str(user.phone_number),
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        }

        response = api_client.post("/api/auth/register/manager/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
