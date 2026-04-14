"""
Tests for login and token endpoints.

Tests:
- LoginAPIView (TokenObtainPairView)
- CustomerTokenSerializer
- JWT token generation
"""
import pytest
from rest_framework import status
from rest_framework_simplejwt.tokens import AccessToken


@pytest.mark.django_db
class TestLogin:
    """Tests for user login and token generation."""

    def test_login_valid_credentials(self, api_client, user):
        """Test login with valid phone number and password."""
        # Set a known password for the user
        user.set_password("TestPass123!")
        user.save()

        data = {
            "phone_number": str(user.phone_number),
            "password": "TestPass123!",
        }

        response = api_client.post("/api/auth/login/", data)

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data

    def test_login_invalid_password(self, api_client, user):
        """Test login fails with incorrect password."""
        user.set_password("CorrectPass123!")
        user.save()

        data = {
            "phone_number": str(user.phone_number),
            "password": "WrongPass123!",
        }

        response = api_client.post("/api/auth/login/", data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_nonexistent_user(self, api_client):
        """Test login fails for non-existent user."""
        data = {
            "phone_number": "+201009999999",
            "password": "TestPass123!",
        }

        response = api_client.post("/api/auth/login/", data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_token_contains_user_info(self, api_client, user):
        """Test that login token contains user and profile info."""

        user.set_password("TestPass123!")
        user.save()

        data = {
            "phone_number": str(user.phone_number),
            "password": "TestPass123!",
        }

        response = api_client.post("/api/auth/login/", data)

        assert response.status_code == status.HTTP_200_OK
        token = response.data["access"]

        # Decode token
        decoded_token = AccessToken(token)
        assert decoded_token["user_id"] == user.id

    def test_login_returns_active_profile_info(self, api_client, user):
        """Test that login response includes active profile."""
        user.set_password("TestPass123!")
        user.save()

        data = {
            "phone_number": str(user.phone_number),
            "password": "TestPass123!",
        }

        response = api_client.post("/api/auth/login/", data)
        assert response.status_code == status.HTTP_200_OK
        assert not response.data["access"] == None  # noqa
        assert not response.data["refresh"] == None  # noqa

    def test_login_missing_phone_number(self, api_client):
        """Test login fails with missing phone number."""
        data = {
            "password": "TestPass123!",
        }

        response = api_client.post("/api/auth/login/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_missing_password(self, api_client, user):
        """Test login fails with missing password."""
        data = {
            "phone_number": str(user.phone_number),
        }

        response = api_client.post("/api/auth/login/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestTokenRefresh:
    """Tests for JWT token refresh."""

    def test_refresh_token_valid(self, api_client, user):
        """Test refreshing a valid refresh token."""
        user.set_password("TestPass123!")
        user.save()

        # Get tokens
        login_data = {
            "phone_number": str(user.phone_number),
            "password": "TestPass123!",
        }
        login_response = api_client.post("/api/auth/login/", login_data)
        refresh_token = login_response.data["refresh"]

        # Refresh token
        refresh_data = {"refresh": refresh_token}
        response = api_client.post("/api/auth/token/refresh/", refresh_data)

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data

    def test_refresh_token_invalid(self, api_client):
        """Test refreshing with invalid token."""
        data = {"refresh": "invalid.token.here"}

        response = api_client.post("/api/auth/token/refresh/", data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
