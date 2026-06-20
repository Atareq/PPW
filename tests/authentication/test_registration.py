"""Tests for authentication registration endpoints."""

import pytest
from rest_framework import status

from apps.authentication.models import UserAccount
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, ManagerProfile


@pytest.mark.django_db
class TestCustomerRegistration:
    """Tests for customer user registration endpoint."""

    def test_register_valid_customer(self, api_client):
        """Test successful customer registration."""
        data = {
            "username": "testcustomer",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["message"] == "User created successfully"

    def test_register_customer_creates_customer_profile(self, api_client):
        """Test that customer registration creates CustomerProfile."""
        data = {
            "username": "testcustomer",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        user = UserAccount.objects.get(phone_number="+201001234567")
        assert user.active_profile.type == ProfileType.CUSTOMER
        assert user.active_profile.is_active is True

        profile = CustomerProfile.objects.get(
            user__phone_number="+201001234567"
        )
        assert profile.type == ProfileType.CUSTOMER
        assert profile.country == "EG"
        assert profile.currency == "EGP"
        assert profile.balance == 0.0

    def test_register_customer_duplicate_phone_number(
        self, api_client, customer_user
    ):
        """Test registration fails with duplicate phone number."""
        data = {
            "username": "another_customer",
            "phone_number": str(customer_user.phone_number),
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone_number" in response.data

    def test_register_customer_weak_password(self, api_client):
        """Test registration validates password strength."""
        data = {
            "username": "testcustomer",
            "phone_number": "+201001234567",
            "password": "weak",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data

    def test_register_customer_missing_username(self, api_client):
        """Test registration fails with missing username."""
        data = {
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_customer_missing_phone_number(self, api_client):
        """Test registration fails with missing phone number."""
        data = {
            "username": "testcustomer",
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_customer_missing_password(self, api_client):
        """Test registration fails with missing password."""
        data = {
            "username": "testcustomer",
            "phone_number": "+201001234567",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_customer_missing_profile_data(self, api_client):
        """Test registration fails when customer profile data is missing."""
        data = {
            "username": "testcustomer",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "customer_profile" in response.data

    def test_register_customer_invalid_phone_format(self, api_client):
        """Test registration fails with invalid phone number format."""
        data = {
            "username": "testcustomer",
            "phone_number": "invalid-phone",
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_customer_balance_not_writable(self, api_client):
        """Test that balance cannot be set during registration (read-only)."""
        data = {
            "username": "testcustomer",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
                "balance": "999.99",
            },
        }

        response = api_client.post(
            "/api/auth/register/customer/", data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        profile = CustomerProfile.objects.get(
            user__phone_number="+201001234567"
        )
        assert profile.balance == 0.0


@pytest.mark.django_db
class TestManagerRegistration:
    """Tests for manager user registration endpoint (Super Admin only)."""

    def test_register_valid_manager(self, super_admin_client):
        """Test successful manager registration by Super Admin."""
        data = {
            "username": "testmanager",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["message"] == "User created successfully"

    def test_register_manager_requires_super_admin(self, api_client):
        """Unauthenticated requests are rejected."""
        data = {
            "username": "testmanager",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = api_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_register_manager_creates_manager_profile(self, super_admin_client):
        """Test that manager registration creates ManagerProfile."""
        data = {
            "username": "testmanager",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        user = UserAccount.objects.get(phone_number="+201001234567")
        assert user.active_profile.type == ProfileType.MANAGER
        assert user.active_profile.is_active is True
        assert user.active_profile.activated is True
        assert user.is_staff is True

        profile = ManagerProfile.objects.get(
            user__phone_number="+201001234567"
        )
        assert profile.type == ProfileType.MANAGER
        assert profile.activated is True

    def test_register_manager_duplicate_phone(self, super_admin_client, customer_user):
        """Test manager registration fails with duplicate phone number."""
        data = {
            "username": "another_manager",
            "phone_number": str(customer_user.phone_number),
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone_number" in response.data

    def test_register_manager_weak_password(self, super_admin_client):
        """Test manager registration validates password strength."""
        data = {
            "username": "testmanager",
            "phone_number": "+201001234567",
            "password": "weak",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data

    def test_register_manager_missing_username(self, super_admin_client):
        """Test manager registration fails with missing username."""
        data = {
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_manager_missing_phone_number(self, super_admin_client):
        """Test manager registration fails with missing phone number."""
        data = {
            "username": "testmanager",
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_manager_missing_password(self, super_admin_client):
        """Test manager registration fails with missing password."""
        data = {
            "username": "testmanager",
            "phone_number": "+201001234567",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_manager_invalid_phone_format(self, super_admin_client):
        """Test manager registration fails with invalid phone number."""
        data = {
            "username": "testmanager",
            "phone_number": "not-a-phone",
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = super_admin_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
