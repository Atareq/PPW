"""
Tests for profile switching.

Tests:
- SwitchProfileAPIView
- Multiple profiles per user
- Active profile management
"""

import pytest
from rest_framework import status


@pytest.mark.django_db
class TestProfileSwitching:
    """Tests for profile switching functionality."""

    def test_switch_to_valid_profile(self, authenticated_client, user):
        """Test switching to a valid profile the user owns."""
        from tests.factories import SellerProfileFactory

        # Create a second profile for the user
        second_profile = SellerProfileFactory(user=user, type="seller")

        data = {"new_profile": second_profile.id}

        response = authenticated_client.post("/api/auth/switch-profile/", data)

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
        from tests.factories import UserAccountFactory, SellerProfileFactory

        other_user = UserAccountFactory(with_profile="seller")
        other_profile = other_user.active_profile

        data = {"new_profile": other_profile.id}

        response = authenticated_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_profile_updates_active_profile(self, authenticated_client, user):
        """Test that switching profile updates the active_profile."""
        from tests.factories import SellerProfileFactory

        second_profile = SellerProfileFactory(user=user, type="seller")

        # Verify original profile is active
        user.refresh_from_db()
        assert user.active_profile.id != second_profile.id

        # Switch to second profile
        data = {"new_profile": second_profile.id}
        response = authenticated_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_200_OK

        # Verify second profile is now active
        user.refresh_from_db()
        assert user.active_profile.id == second_profile.id

    def test_switch_profile_requires_authentication(self, api_client, user):
        """Test that profile switching requires authentication."""
        data = {"new_profile": 1}

        response = api_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_switch_profile_missing_new_profile_param(self, authenticated_client):
        """Test switching fails with missing new_profile parameter."""
        data = {}

        response = authenticated_client.post("/api/auth/switch-profile/", data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
