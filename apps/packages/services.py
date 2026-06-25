from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.packages.models import (
    AssetSellerPackage,
    Package,
    PackageUsageLog,
    PackageUsageType,
    SellerPackage,
    SellerPackageStatus,
)
from apps.packages.validators import SellerPackageValidator
from apps.users.constants import ProfileType


def seller_package_queryset():
    return SellerPackage.objects.select_related(
        "owner",
        "owner__user",
        "package",
    ).order_by("-created")


class PackageService:
    """Consume seller package points for paid seller-side platform actions."""

    @staticmethod
    def validate_can_consume(seller_profile, usage_type, seller_package_id):
        """Preflight package availability without writing a usage log."""

        seller = SellerPackageValidator.get_seller_profile(seller_profile)
        PackageService._validate_usage_type(usage_type)

        expired_error = None
        with transaction.atomic():
            seller_package = PackageService._get_locked_selected_package(
                seller,
                seller_package_id,
            )

            now = timezone.now()
            if seller_package.expires_at and seller_package.expires_at <= now:
                PackageService._mark_exhausted(seller_package, now)
                expired_error = ValidationError(
                    {"package": "Active package has expired."}
                )
            else:
                required_cost = PackageService._required_cost(
                    seller_package,
                    usage_type,
                )
                if seller_package.remaining_points < required_cost:
                    raise ValidationError(
                        {"package": ("Insufficient package points for this action.")}
                    )
                return seller_package

        raise expired_error

    @staticmethod
    def consume_points(
        seller_profile,
        usage_type,
        seller_package_id,
        related_object=None,
    ):
        """Atomically spend package points and record the usage log."""

        seller = SellerPackageValidator.get_seller_profile(seller_profile)
        PackageService._validate_usage_type(usage_type)

        expired_error = None
        with transaction.atomic():
            seller_package = PackageService._get_locked_selected_package(
                seller,
                seller_package_id,
            )

            now = timezone.now()
            if seller_package.expires_at and seller_package.expires_at <= now:
                PackageService._mark_exhausted(seller_package, now)
                expired_error = ValidationError(
                    {"package": "Active package has expired."}
                )
            else:
                return PackageService._consume_locked_package(
                    seller_package,
                    usage_type,
                    related_object,
                    now,
                )

        raise expired_error

    @staticmethod
    def _validate_usage_type(usage_type):
        if usage_type not in {
            PackageUsageType.AUCTION_CREATED,
            PackageUsageType.PRODUCT_CREATED,
        }:
            raise ValidationError({"usage_type": "Unsupported package usage type."})

    @staticmethod
    def _get_locked_selected_package(seller, seller_package_id):
        if not seller_package_id:
            raise ValidationError({"seller_package_id": "This field is required."})

        try:
            seller_package = (
                SellerPackage.objects.select_for_update()
                .select_related("owner", "package")
                .get(pk=seller_package_id)
            )
        except SellerPackage.DoesNotExist as exc:
            raise ValidationError(
                {"seller_package_id": "Seller package was not found."}
            ) from exc

        if seller_package.owner_id != seller.pk:
            raise ValidationError(
                {"seller_package_id": "Seller package does not belong to this seller."}
            )

        if (
            seller_package.status != SellerPackageStatus.ACTIVE
            or not seller_package.activated
        ):
            raise ValidationError(
                {"seller_package_id": "Seller package is not active."}
            )

        return seller_package

    @staticmethod
    def _consume_locked_package(
        seller_package,
        usage_type,
        related_object,
        now,
    ):
        required_cost = PackageService._required_cost(
            seller_package,
            usage_type,
        )
        if seller_package.remaining_points < required_cost:
            raise ValidationError(
                {"package": "Insufficient package points for this action."}
            )

        seller_package.remaining_points -= required_cost
        update_fields = ["remaining_points", "modified"]
        if PackageService._is_unusable(seller_package):
            seller_package.status = SellerPackageStatus.EXHAUSTED
            seller_package.activated = False
            seller_package.invalidated_at = now
            update_fields.extend(["status", "activated", "invalidated_at"])

        seller_package.save(update_fields=update_fields)
        PackageService._create_usage_log(
            seller_package,
            usage_type,
            required_cost,
            related_object,
        )
        return seller_package

    @staticmethod
    def should_consume_for_profile(profile):
        return bool(profile and profile.type == ProfileType.SELLER)

    @staticmethod
    def _required_cost(seller_package, usage_type):
        if usage_type == PackageUsageType.AUCTION_CREATED:
            return seller_package.package.auction_cost
        if usage_type == PackageUsageType.PRODUCT_CREATED:
            return seller_package.package.product_cost
        raise ValidationError({"usage_type": "Unsupported package usage type."})

    @staticmethod
    def _is_unusable(seller_package):
        return seller_package.remaining_points == 0 or (
            seller_package.remaining_points < seller_package.package.auction_cost
            and seller_package.remaining_points < seller_package.package.product_cost
        )

    @staticmethod
    def _mark_exhausted(seller_package, now):
        seller_package.status = SellerPackageStatus.EXHAUSTED
        seller_package.activated = False
        seller_package.invalidated_at = now
        seller_package.save(
            update_fields=[
                "status",
                "activated",
                "invalidated_at",
                "modified",
            ]
        )

    @staticmethod
    def _create_usage_log(
        seller_package,
        usage_type,
        points_used,
        related_object,
    ):
        related_object_type = ""
        related_object_id = None
        if related_object is not None:
            related_object_type = related_object._meta.label
            related_object_id = related_object.pk

        return PackageUsageLog.objects.create(
            seller_package=seller_package,
            owner=seller_package.owner,
            usage_type=usage_type,
            points_used=points_used,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
        )


class SellerPackageService:
    """Seller package request and manager activation lifecycle."""

    @staticmethod
    @transaction.atomic
    def request_package(profile, package_id, payment_proof):
        """Create one pending package request per seller/package pair."""

        seller = SellerPackageValidator.get_seller_profile(profile)

        try:
            package = Package.objects.get(pk=package_id)
        except Package.DoesNotExist as exc:
            raise ValidationError({"package_id": "Package was not found."}) from exc

        if not package.is_available:
            raise ValidationError({"package_id": "Package is not currently available."})

        if SellerPackage.objects.filter(
            owner=seller,
            package=package,
            status=SellerPackageStatus.PENDING,
        ).exists():
            raise ValidationError(
                {"package_id": ("A pending request for this package already exists.")}
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
            payment_proof=payment_proof,
        )

    @staticmethod
    def get_active_package(profile):
        """Return the seller's active package, expiring it first when needed."""

        seller = SellerPackageValidator.get_seller_profile(profile)
        expired_error = None
        with transaction.atomic():
            package = (
                SellerPackage.objects.select_for_update()
                .select_related("package", "owner")
                .filter(
                    owner=seller,
                    status=SellerPackageStatus.ACTIVE,
                    activated=True,
                )
                .first()
            )
            if package is None:
                raise NotFound("Active seller package was not found.")
            now = timezone.now()
            if package.expires_at and package.expires_at <= now:
                PackageService._mark_exhausted(package, now)
                expired_error = NotFound("Active seller package was not found.")
            else:
                return package

        raise expired_error

    @staticmethod
    @transaction.atomic
    def activate_package(seller_package):
        """Activate one seller package without invalidating other active packages."""

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
            raise ValidationError({"activated": "Seller package is already activated."})

        if not locked_package.package.is_available:
            raise ValidationError(
                {"package": "Referenced package is not currently available."}
            )

        now = timezone.now()
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
        """Managers may cancel only pending seller package requests."""

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
