"""
Tests for user model and permissions.

Tests:
- UserAccount model
- Permission management
- Profile activation
"""

import pytest
from apps.authentication.models import UserAccount
from apps.authentication.constants import AppPermissions


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
        from tests.factories import UserAccountFactory
        from django.db import IntegrityError

        user1 = UserAccountFactory(with_profile="seller")
        phone = user1.phone_number

        with pytest.raises(IntegrityError):
            UserAccountFactory(phone_number=phone, with_profile="seller")

    def test_active_profile_property(self, user):
        """Test active_profile property returns correct profile."""
        from tests.factories import SellerProfileFactory

        profile = user.active_profile
        assert profile is not None
        assert profile.is_active is True

    def test_role_property(self, user):
        """Test role property returns profile type."""
        from apps.users.constants import ProfileType

        assert user.role == ProfileType.SELLER

    def test_is_activated_property(self, user):
        """Test is_activated property."""
        profile = user.active_profile
        assert user.is_activated == profile.activated

    def test_multiple_profiles(self):
        """Test user can have multiple profiles."""
        from tests.factories import UserAccountFactory, SellerProfileFactory

        user = UserAccountFactory(with_profile="seller")
        second_profile = SellerProfileFactory(user=user, type="manager", is_active=False)

        assert user.profiles.count() == 2
        assert user.active_profile.id != second_profile.id


@pytest.mark.django_db
class TestPermissions:
    """Tests for permission management."""

    def test_add_permission(self, user):
        """Test adding a permission to user."""
        user.add_permission(AppPermissions.CREATE_AUCTION)

        assert user.has_app_permission(AppPermissions.CREATE_AUCTION)

    def test_add_multiple_permissions(self, user):
        """Test adding multiple permissions."""
        perms = [AppPermissions.CREATE_AUCTION, AppPermissions.VIEW_AUCTION]

        for perm in perms:
            user.add_permission(perm)

        for perm in perms:
            assert user.has_app_permission(perm)

    def test_remove_permission(self, user):
        """Test removing a permission."""
        user.add_permission(AppPermissions.CREATE_AUCTION)
        assert user.has_app_permission(AppPermissions.CREATE_AUCTION)

        user.remove_permission(AppPermissions.CREATE_AUCTION)
        assert not user.has_app_permission(AppPermissions.CREATE_AUCTION)

    def test_set_permissions(self, user):
        """Test setting permissions (overwrites existing)."""
        user.add_permission(AppPermissions.CREATE_AUCTION)
        perms = [AppPermissions.VIEW_AUCTION, AppPermissions.EDIT_AUCTION]

        user.set_permissions(perms)

        assert not user.has_app_permission(AppPermissions.CREATE_AUCTION)
        assert user.has_app_permission(AppPermissions.VIEW_AUCTION)
        assert user.has_app_permission(AppPermissions.EDIT_AUCTION)

    def test_invalid_permission_raises_error(self, user):
        """Test that invalid permission raises ValueError."""
        with pytest.raises(ValueError):
            user.add_permission("INVALID_PERMISSION")

    def test_permissions_no_duplicates(self, user):
        """Test that duplicate permissions aren't added."""
        user.add_permission(AppPermissions.CREATE_AUCTION)
        user.add_permission(AppPermissions.CREATE_AUCTION)

        perms = user.get_manager_permissions()
        assert perms.count(AppPermissions.CREATE_AUCTION) == 1

    def test_has_app_permission_false_by_default(self, user):
        """Test that user has no permissions by default."""
        assert not user.has_app_permission(AppPermissions.CREATE_AUCTION)
