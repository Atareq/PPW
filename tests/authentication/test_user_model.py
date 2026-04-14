"""
Tests for user model and permissions.

Tests:
- UserAccount model
- Permission management
- Profile activation
"""
from tests.factories import CustomerProfileFactory, UserAccountFactory, SellerProfileFactory
import pytest
from apps.authentication.models import UserAccount
from apps.authentication.constants import AppPermissions
from apps.users.constants import ProfileType
from tests.factories import UserAccountFactory
from django.db import IntegrityError

@pytest.mark.django_db
class TestUserModel:
    """Tests for UserAccount model."""

    def test_user_creation(self, user):
        """Test creating a user."""
        assert user.id is not None
        assert user.phone_number is not None
        assert user.active_profile is not None

    def test_user_phone_number_unique(self):
        """Test that phone numbers are unique."""


        user1 = UserAccountFactory(with_profile=ProfileType.SELLER)
        phone = user1.phone_number

        with pytest.raises(IntegrityError):
            UserAccountFactory(phone_number=phone, with_profile=ProfileType.SELLER)

    def test_active_profile_property(self, user):
        """Test active_profile property returns correct profile."""
        from tests.factories import SellerProfileFactory

        profile = user.active_profile
        assert profile is not None
        assert profile.is_active is True

    def test_role_property(self, seller_user):
        """Test role property returns profile type."""

        assert seller_user.role == ProfileType.SELLER

    def test_is_activated_property(self, user):
        """Test is_activated property."""
        profile = user.active_profile
        assert user.is_activated == profile.activated

    def test_multiple_profiles(self):
        """Test user can have multiple profiles."""

        user = UserAccountFactory(with_profile=ProfileType.SELLER)
        second_profile = CustomerProfileFactory(user=user, is_active=False)

        assert user.profiles.count() == 2
        assert user.active_profile.id != second_profile.id
