from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.packages.models import (
    AssetSellerPackage,
    Package,
    SellerPackage,
    SellerPackageStatus,
)
from apps.packages.validators import SellerPackageValidator


def seller_package_queryset():
    return SellerPackage.objects.select_related(
        "owner",
        "owner__user",
        "package",
    ).order_by("-created")


class SellerPackageService:
    @staticmethod
    @transaction.atomic
    def request_package(profile, package_id):
        seller = SellerPackageValidator.get_seller_profile(profile)

        try:
            package = Package.objects.get(pk=package_id)
        except Package.DoesNotExist as exc:
            raise ValidationError(
                {"package_id": "Package was not found."}
            ) from exc

        if not package.is_available:
            raise ValidationError(
                {"package_id": "Package is not currently available."}
            )

        if SellerPackage.objects.filter(
            owner=seller,
            package=package,
            status=SellerPackageStatus.PENDING,
        ).exists():
            raise ValidationError(
                {
                    "package_id": (
                        "A pending request for this package already exists."
                    )
                }
            )

        return AssetSellerPackage.objects.create(
            owner=seller,
            package=package,
            remaining_points=package.points,
            status=SellerPackageStatus.PENDING,
            activated=False,
            activated_at=None,
            expires_at=None,
            invalidated_at=None,
        )

    @staticmethod
    def get_active_package(profile):
        seller = SellerPackageValidator.get_seller_profile(profile)
        package = (
            SellerPackage.objects.select_related("package", "owner")
            .filter(
                owner=seller,
                status=SellerPackageStatus.ACTIVE,
                activated=True,
            )
            .first()
        )
        if package is None:
            raise NotFound("Active seller package was not found.")
        return package

    @staticmethod
    @transaction.atomic
    def activate_package(seller_package):
        locked_package = (
            SellerPackage.objects.select_for_update()
            .select_related("package", "owner")
            .get(pk=seller_package.pk)
        )

        if locked_package.status != SellerPackageStatus.PENDING:
            raise ValidationError(
                {"status": "Only pending seller packages can be activated."}
            )

        if locked_package.activated:
            raise ValidationError(
                {"activated": "Seller package is already activated."}
            )

        if not locked_package.package.is_available:
            raise ValidationError(
                {"package": "Referenced package is not currently available."}
            )

        now = timezone.now()
        (
            SellerPackage.objects.select_for_update()
            .filter(
                owner=locked_package.owner,
                status=SellerPackageStatus.ACTIVE,
                activated=True,
            )
            .exclude(pk=locked_package.pk)
            .update(
                activated=False,
                invalidated_at=now,
                modified=now,
            )
        )

        locked_package.status = SellerPackageStatus.ACTIVE
        locked_package.activated = True
        locked_package.activated_at = now
        locked_package.expires_at = now + timedelta(
            days=locked_package.package.activation_period_days
        )

        try:
            locked_package.save(
                update_fields=[
                    "status",
                    "activated",
                    "activated_at",
                    "expires_at",
                    "modified",
                ]
            )
        except IntegrityError as exc:
            raise ValidationError(
                {"owner": "Seller already has an active package."}
            ) from exc

        return seller_package_queryset().get(pk=locked_package.pk)

    @staticmethod
    @transaction.atomic
    def cancel_package(seller_package):
        locked_package = (
            SellerPackage.objects.select_for_update()
            .select_related("package", "owner")
            .get(pk=seller_package.pk)
        )

        if locked_package.status != SellerPackageStatus.PENDING:
            raise ValidationError(
                {"status": "Only pending seller packages can be cancelled."}
            )

        locked_package.status = SellerPackageStatus.CANCELLED
        locked_package.activated = False
        locked_package.invalidated_at = timezone.now()
        locked_package.save(
            update_fields=[
                "status",
                "activated",
                "invalidated_at",
                "modified",
            ]
        )
        return seller_package_queryset().get(pk=locked_package.pk)
