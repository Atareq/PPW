"""
Tests for profile switching.

Tests:
- SwitchProfileAPIView
- Multiple profiles per user
- Active profile management
"""

import pytest
from rest_framework import status

from apps.users.constants import ProfileType
from tests.factories import SellerProfileFactory, UserAccountFactory


@pytest.mark.django_db
class TestProfileSwitching:
    """Tests for profile switching functionality."""

    def test_switch_to_valid_profile(
        self, authenticated_customer_client, customer_user
    ):
        """Test switching to a valid profile the user owns."""

        # Create a second profile for the user
        SellerProfileFactory(user=customer_user)

        data = {"new_profile": ProfileType.SELLER}
        response = authenticated_customer_client.post(
            "/api/auth/switch-profile/", data
        )

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data

    def test_switch_to_nonexistent_profile(self, authenticated_client, user):
        """Test switching to a profile that doesn't exist."""
        data = {"new_profile": 99999}

        response = authenticated_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_to_other_user_profile(self, authenticated_client, user):
        """Test user cannot switch to another user's profile."""

        other_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        other_profile = other_user.active_profile
        data = {"new_profile": other_profile.id}

        response = authenticated_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_profile_updates_active_profile(
        self, authenticated_customer_client, customer_user
    ):
        """Test that switching profile updates the active_profile."""

        first_profile = customer_user  # User with customer active profile
        second_profile = SellerProfileFactory(user=first_profile)
        first_profile.refresh_from_db()
        assert first_profile.active_profile != second_profile.type
        # Switch to second profile
        data = {"new_profile": ProfileType.SELLER}
        response = authenticated_customer_client.post(
            "/api/auth/switch-profile/", data
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify second profile is now active
        first_profile.refresh_from_db()
        assert first_profile.active_profile.id == second_profile.id

    def test_switch_profile_requires_authentication(self, api_client, user):
        """Test that profile switching requires authentication."""
        data = {"new_profile": 1}

        response = api_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_switch_profile_missing_new_profile_param(
        self, authenticated_client
    ):
        """Test switching fails with missing new_profile parameter."""
        data = {}

        response = authenticated_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
