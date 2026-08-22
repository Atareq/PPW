"""Tests for authentication registration endpoints."""

import pytest
from rest_framework import status

from apps.authentication.models import UserAccount
from apps.authentication.services import notify_managers_new_user_registered
from apps.notifications.models import Notification, NotificationType
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, ManagerProfile
from tests.factories import ManagerProfileFactory, UserAccountFactory


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
        assert "message" in response.data

    def test_register_customer_notifies_active_managers(
        self,
        api_client,
        django_capture_on_commit_callbacks,
    ):
        manager = ManagerProfileFactory(is_active=True, activated=True, is_valid=True)
        inactive_manager = ManagerProfileFactory(
            is_active=False,
            activated=True,
            is_valid=True,
        )
        invalid_manager = ManagerProfileFactory(
            is_active=True,
            activated=True,
            is_valid=False,
        )
        deleted_manager = ManagerProfileFactory(
            is_active=True,
            activated=True,
            is_valid=True,
        )
        deleted_manager.delete()
        data = {
            "username": "testcustomer",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "customer_profile": {
                "country": "EG",
                "currency": "EGP",
            },
        }

        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(
                "/api/auth/register/customer/",
                data,
                format="json",
            )

        assert response.status_code == status.HTTP_201_CREATED
        notification = Notification.objects.get(
            notification_type=NotificationType.NEW_USER_REGISTERED,
        )
        user = UserAccount.objects.get(phone_number="+201001234567")
        assert notification.recipient_profile_id == manager.pk
        assert notification.object_id == user.pk
        assert notification.metadata["user_id"] == user.pk
        assert notification.metadata["profile_type"] == ProfileType.CUSTOMER
        skipped_ids = {inactive_manager.pk, invalid_manager.pk, deleted_manager.pk}
        assert not Notification.objects.filter(
            recipient_profile_id__in=skipped_ids,
        ).exists()

    def test_new_user_registration_notification_is_idempotent(
        self,
        django_capture_on_commit_callbacks,
    ):
        manager = ManagerProfileFactory(is_active=True, activated=True, is_valid=True)
        user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        profile = user.active_profile

        with django_capture_on_commit_callbacks(execute=True):
            notify_managers_new_user_registered(user, profile)
            notify_managers_new_user_registered(user, profile)

        assert Notification.objects.filter(
            recipient_profile=manager,
            notification_type=NotificationType.NEW_USER_REGISTERED,
        ).count() == 1

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
    """Manager registration is protected from public use."""

    def test_public_manager_registration_is_blocked(self, api_client):
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

    def test_non_superuser_cannot_use_manager_registration(
        self, authenticated_customer_client
    ):
        data = {
            "username": "testmanager",
            "phone_number": "+201001234567",
            "password": "SecurePass123!@",
            "manager_profile": {},
        }

        response = authenticated_customer_client.post(
            "/api/auth/register/manager/", data, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
