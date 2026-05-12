from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, close_old_connections, connection
from django.utils import timezone
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.packages.models import (
    AssetSellerPackage,
    Package,
    PackageUsageLog,
    PackageUsageType,
    SellerPackage,
    SellerPackageStatus,
)
from apps.packages.services import PackageService, SellerPackageService
from apps.users.constants import ProfileType
from apps.users.models import SellerProfile
from tests.factories import (
    ActiveAssetSellerPackageFactory,
    AssetSellerPackageFactory,
    PackageFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def concrete_seller_profile(user):
    return SellerProfile.objects.get(pk=user.active_profile.pk)


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


class TestPackageModel:
    def test_validates_positive_points(self):
        package = PackageFactory.build(points=0)

        with pytest.raises(DjangoValidationError) as exc_info:
            package.full_clean()

        assert "points" in exc_info.value.message_dict

    def test_validates_price_non_negative(self):
        package = PackageFactory.build(price=Decimal("-0.01"))

        with pytest.raises(DjangoValidationError) as exc_info:
            package.full_clean()

        assert "price" in exc_info.value.message_dict

    def test_validates_activation_period_days_positive(self):
        package = PackageFactory.build(activation_period_days=0)

        with pytest.raises(DjangoValidationError) as exc_info:
            package.full_clean()

        assert "activation_period_days" in exc_info.value.message_dict

    def test_validates_auction_and_product_cost_positive(self):
        package = PackageFactory.build(auction_cost=0, product_cost=0)

        with pytest.raises(DjangoValidationError) as exc_info:
            package.full_clean()

        errors = exc_info.value.message_dict
        assert "auction_cost" in errors
        assert "product_cost" in errors

    def test_rejects_auction_cost_above_points(self):
        package = PackageFactory.build(points=10, auction_cost=11)

        with pytest.raises(DjangoValidationError) as exc_info:
            package.full_clean()

        assert "auction_cost" in exc_info.value.message_dict

    def test_rejects_product_cost_above_points(self):
        package = PackageFactory.build(points=10, product_cost=11)

        with pytest.raises(DjangoValidationError) as exc_info:
            package.full_clean()

        assert "product_cost" in exc_info.value.message_dict


class TestSellerPackageModel:
    def test_request_starts_pending_deactivated_and_copies_points(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(points=120)

        seller_package = SellerPackageService.request_package(
            seller_user.active_profile,
            package.pk,
        )

        assert isinstance(seller_package, AssetSellerPackage)
        assert seller_package.status == SellerPackageStatus.PENDING
        assert seller_package.activated is False
        assert seller_package.remaining_points == 120
        assert seller_package.activated_at is None
        assert seller_package.expires_at is None
        assert seller_package.invalidated_at is None

    @pytest.mark.django_db(transaction=True)
    def test_only_one_active_seller_package_is_enforced(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = concrete_seller_profile(seller_user)
        now = timezone.now()
        AssetSellerPackageFactory(
            owner=seller,
            status=SellerPackageStatus.ACTIVE,
            activated=True,
            activated_at=now,
            expires_at=now + timedelta(days=30),
        )

        with pytest.raises(IntegrityError):
            AssetSellerPackageFactory(
                owner=seller,
                status=SellerPackageStatus.ACTIVE,
                activated=True,
                activated_at=now,
                expires_at=now + timedelta(days=30),
            )


class TestSellerPackageEndpoints:
    def test_seller_can_list_available_packages(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        available = PackageFactory(name="Available", is_available=True)
        unavailable = PackageFactory(name="Unavailable", is_available=False)
        client = authenticated_client(seller_user)

        response = client.get("/api/packages/")

        assert response.status_code == 200
        ids = {item["id"] for item in response_results(response)}
        assert available.pk in ids
        assert unavailable.pk not in ids

    def test_seller_can_request_package(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(points=80)
        client = authenticated_client(seller_user)

        response = client.post(
            "/api/packages/my-packages/",
            {"package_id": package.pk},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["package_id"] == package.pk
        assert response.data["status"] == SellerPackageStatus.PENDING
        assert response.data["activated"] is False
        assert response.data["remaining_points"] == 80
        assert AssetSellerPackage.objects.count() == 1

    def test_seller_can_view_own_package_history(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        other_seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = AssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user)
        )
        AssetSellerPackageFactory(
            owner=concrete_seller_profile(other_seller_user)
        )
        client = authenticated_client(seller_user)

        response = client.get("/api/packages/my-packages/?status=pending")

        assert response.status_code == 200
        ids = {item["id"] for item in response_results(response)}
        assert ids == {seller_package.pk}

    def test_seller_can_view_active_package_summary(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(auction_cost=12, product_cost=7)
        now = timezone.now()
        seller_package = AssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            package=package,
            status=SellerPackageStatus.ACTIVE,
            activated=True,
            activated_at=now,
            expires_at=now + timedelta(days=30),
        )
        client = authenticated_client(seller_user)

        response = client.get("/api/packages/my-packages/?active=true")

        assert response.status_code == 200
        assert response.data["id"] == seller_package.pk
        assert response.data["package"]["id"] == package.pk
        assert (
            response.data["remaining_points"]
            == seller_package.remaining_points
        )
        assert response.data["auction_cost"] == 12
        assert response.data["product_cost"] == 7
        assert response.data["expires_at"] is not None

    def test_seller_cannot_activate_package(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = AssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user)
        )
        client = authenticated_client(seller_user)

        response = client.patch(
            f"/api/seller-packages/{seller_package.pk}/",
            {"status": SellerPackageStatus.ACTIVE},
            format="json",
        )

        assert response.status_code == 403

    def test_seller_cannot_see_another_seller_package(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        other_seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        other_package = AssetSellerPackageFactory(
            owner=concrete_seller_profile(other_seller_user)
        )
        client = authenticated_client(seller_user)

        response = client.get("/api/packages/my-packages/")

        assert response.status_code == 200
        ids = {item["id"] for item in response_results(response)}
        assert other_package.pk not in ids

    def test_customer_cannot_request_package(self):
        customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        package = PackageFactory()
        client = authenticated_client(customer_user)

        response = client.post(
            "/api/packages/my-packages/",
            {"package_id": package.pk},
            format="json",
        )

        assert response.status_code == 403


class TestManagerPackageEndpoints:
    def test_manager_can_create_package(self):
        manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
        client = authenticated_client(manager_user)

        response = client.post(
            "/api/packages/",
            {
                "name": "Manager Package",
                "description": "Created by manager",
                "points": 150,
                "price": "99.00",
                "activation_period_days": 60,
                "auction_cost": 15,
                "product_cost": 8,
                "is_available": True,
            },
            format="json",
        )

        assert response.status_code == 201
        assert Package.objects.filter(name="Manager Package").exists()

    def test_manager_can_update_package(self):
        manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
        package = PackageFactory(name="Old", is_available=True)
        client = authenticated_client(manager_user)

        response = client.patch(
            f"/api/packages/manager/packages/{package.pk}/",
            {"name": "Updated", "is_available": False},
            format="json",
        )

        assert response.status_code == 200
        package.refresh_from_db()
        assert package.name == "Updated"
        assert package.is_available is False

    def test_manager_can_list_seller_packages(self):
        manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
        seller_package = AssetSellerPackageFactory()
        client = authenticated_client(manager_user)

        response = client.get("/api/seller-packages/")

        assert response.status_code == 200
        ids = {item["id"] for item in response_results(response)}
        assert seller_package.pk in ids

    def test_manager_can_activate_pending_seller_package(self):
        manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
        seller_package = AssetSellerPackageFactory()
        client = authenticated_client(manager_user)

        response = client.patch(
            f"/api/seller-packages/{seller_package.pk}/",
            {"status": SellerPackageStatus.ACTIVE},
            format="json",
        )

        assert response.status_code == 200
        seller_package = SellerPackage.objects.get(pk=seller_package.pk)
        assert seller_package.status == SellerPackageStatus.ACTIVE
        assert seller_package.activated is True
        assert seller_package.activated_at is not None
        assert seller_package.expires_at is not None

    def test_manager_activation_deactivates_existing_active_package(self):
        manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
        seller = concrete_seller_profile(
            UserAccountFactory(with_profile=ProfileType.SELLER)
        )
        now = timezone.now()
        active_package = AssetSellerPackageFactory(
            owner=seller,
            status=SellerPackageStatus.ACTIVE,
            activated=True,
            activated_at=now,
            expires_at=now + timedelta(days=30),
        )
        pending_package = AssetSellerPackageFactory(owner=seller)
        client = authenticated_client(manager_user)

        response = client.patch(
            f"/api/seller-packages/{pending_package.pk}/",
            {"status": SellerPackageStatus.ACTIVE},
            format="json",
        )

        assert response.status_code == 200
        active_package.refresh_from_db()
        pending_package.refresh_from_db()
        assert active_package.status == SellerPackageStatus.ACTIVE
        assert active_package.activated is False
        assert active_package.invalidated_at is not None
        assert pending_package.status == SellerPackageStatus.ACTIVE
        assert pending_package.activated is True
        assert pending_package.activated_at is not None
        assert (
            SellerPackage.objects.filter(
                owner=seller,
                status=SellerPackageStatus.ACTIVE,
                activated=True,
            ).count()
            == 1
        )

    def test_manager_can_cancel_pending_seller_package(self):
        manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
        seller_package = AssetSellerPackageFactory()
        client = authenticated_client(manager_user)

        response = client.patch(
            f"/api/seller-packages/{seller_package.pk}/",
            {"status": SellerPackageStatus.CANCELLED},
            format="json",
        )

        assert response.status_code == 200
        seller_package.refresh_from_db()
        assert seller_package.status == SellerPackageStatus.CANCELLED
        assert seller_package.activated is False
        assert seller_package.invalidated_at is not None

    def test_manager_cannot_cancel_active_seller_package(self):
        manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)
        now = timezone.now()
        seller_package = AssetSellerPackageFactory(
            status=SellerPackageStatus.ACTIVE,
            activated=True,
            activated_at=now,
            expires_at=now + timedelta(days=30),
        )
        client = authenticated_client(manager_user)

        response = client.patch(
            f"/api/seller-packages/{seller_package.pk}/",
            {"status": SellerPackageStatus.CANCELLED},
            format="json",
        )

        assert response.status_code == 400
        seller_package.refresh_from_db()
        assert seller_package.status == SellerPackageStatus.ACTIVE


class TestPackageConsumptionService:
    def test_active_package_can_consume_auction_cost(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(
            points=100,
            auction_cost=15,
            product_cost=8,
        )
        seller_package = ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            package=package,
            remaining_points=100,
        )

        updated = PackageService.consume_points(
            seller_user.active_profile,
            PackageUsageType.AUCTION_CREATED,
            related_object=seller_package,
        )

        assert updated.remaining_points == 85
        log = PackageUsageLog.objects.get()
        assert log.seller_package_id == seller_package.pk
        assert log.owner_id == seller_user.active_profile.pk
        assert log.usage_type == PackageUsageType.AUCTION_CREATED
        assert log.points_used == 15
        assert log.related_object_type == "packages.AssetSellerPackage"
        assert log.related_object_id == seller_package.pk

    def test_active_package_can_consume_product_cost(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(
            points=100,
            auction_cost=15,
            product_cost=8,
        )
        ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            package=package,
            remaining_points=100,
        )
        product = SupplyFactory(owner=seller_user.active_profile)

        updated = PackageService.consume_points(
            seller_user.active_profile,
            PackageUsageType.PRODUCT_CREATED,
            related_object=product,
        )

        assert updated.remaining_points == 92
        log = PackageUsageLog.objects.get()
        assert log.usage_type == PackageUsageType.PRODUCT_CREATED
        assert log.points_used == 8
        assert log.related_object_type == "assets.Supply"
        assert log.related_object_id == product.pk

    def test_package_becomes_exhausted_when_points_reach_zero(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(points=10, auction_cost=10, product_cost=10)
        ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            package=package,
            remaining_points=10,
        )

        updated = PackageService.consume_points(
            seller_user.active_profile,
            PackageUsageType.AUCTION_CREATED,
        )

        assert updated.remaining_points == 0
        assert updated.status == SellerPackageStatus.EXHAUSTED
        assert updated.activated is False
        assert updated.invalidated_at is not None
        assert PackageUsageLog.objects.count() == 1

    def test_package_becomes_exhausted_when_remaining_points_are_unusable(
        self,
    ):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(points=20, auction_cost=10, product_cost=6)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            package=package,
            remaining_points=11,
        )

        updated = PackageService.consume_points(
            seller_user.active_profile,
            PackageUsageType.PRODUCT_CREATED,
        )

        assert updated.remaining_points == 5
        assert updated.status == SellerPackageStatus.EXHAUSTED
        assert updated.activated is False
        seller_package.refresh_from_db()
        assert seller_package.invalidated_at is not None

    def test_exhausted_package_cannot_be_consumed(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            status=SellerPackageStatus.EXHAUSTED,
            activated=False,
        )

        with pytest.raises(Exception, match="No active package"):
            PackageService.consume_points(
                seller_user.active_profile,
                PackageUsageType.AUCTION_CREATED,
            )

        assert PackageUsageLog.objects.count() == 0

    def test_no_active_package_blocks_consumption(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)

        with pytest.raises(Exception, match="No active package"):
            PackageService.consume_points(
                seller_user.active_profile,
                PackageUsageType.PRODUCT_CREATED,
            )

        assert PackageUsageLog.objects.count() == 0

    def test_expired_package_is_exhausted_and_blocks_consumption(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            expires_at=timezone.now() - timedelta(days=1),
        )

        with pytest.raises(Exception, match="expired"):
            PackageService.consume_points(
                seller_user.active_profile,
                PackageUsageType.PRODUCT_CREATED,
            )

        seller_package.refresh_from_db()
        assert seller_package.status == SellerPackageStatus.EXHAUSTED
        assert seller_package.activated is False
        assert seller_package.invalidated_at is not None
        assert PackageUsageLog.objects.count() == 0

    def test_remaining_points_cannot_go_negative(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(points=100, auction_cost=50, product_cost=20)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            package=package,
            remaining_points=10,
        )

        with pytest.raises(Exception, match="Insufficient package points"):
            PackageService.consume_points(
                seller_user.active_profile,
                PackageUsageType.AUCTION_CREATED,
            )

        seller_package.refresh_from_db()
        assert seller_package.remaining_points == 10
        assert seller_package.status == SellerPackageStatus.ACTIVE
        assert PackageUsageLog.objects.count() == 0

    def test_pedigree_consumption_is_not_supported_yet(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
        )

        with pytest.raises(Exception, match="Unsupported package usage type"):
            PackageService.consume_points(
                seller_user.active_profile,
                PackageUsageType.PEDIGREE_CREATED,
            )

        seller_package.refresh_from_db()
        assert seller_package.status == SellerPackageStatus.ACTIVE
        assert PackageUsageLog.objects.count() == 0

    def test_repeated_final_point_consumption_cannot_go_negative(self):
        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        package = PackageFactory(points=5, auction_cost=5, product_cost=5)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=concrete_seller_profile(seller_user),
            package=package,
            remaining_points=5,
        )

        PackageService.consume_points(
            seller_user.active_profile,
            PackageUsageType.PRODUCT_CREATED,
        )
        with pytest.raises(Exception, match="No active package"):
            PackageService.consume_points(
                seller_user.active_profile,
                PackageUsageType.PRODUCT_CREATED,
            )

        seller_package.refresh_from_db()
        assert seller_package.remaining_points == 0
        assert seller_package.status == SellerPackageStatus.EXHAUSTED
        assert PackageUsageLog.objects.count() == 1

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_consumption_cannot_over_consume_final_points(self):
        if connection.vendor == "sqlite":
            pytest.skip("SQLite does not enforce select_for_update row locks.")

        seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
        seller = concrete_seller_profile(seller_user)
        package = PackageFactory(points=5, auction_cost=5, product_cost=5)
        seller_package = ActiveAssetSellerPackageFactory(
            owner=seller,
            package=package,
            remaining_points=5,
        )

        def consume():
            close_old_connections()
            try:
                return PackageService.consume_points(
                    SellerProfile.objects.get(pk=seller.pk),
                    PackageUsageType.AUCTION_CREATED,
                ).pk
            except Exception as exc:
                return str(exc)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: consume(), range(2)))

        seller_package.refresh_from_db()
        assert seller_package.remaining_points == 0
        assert PackageUsageLog.objects.count() == 1
        assert any(result == seller_package.pk for result in results)
