from rest_framework import serializers

from apps.packages.models import Package, SellerPackage, SellerPackageStatus
from apps.packages.services import SellerPackageService
from apps.packages.validators import PackageValidationRules


class PackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = [
            "id",
            "name",
            "description",
            "points",
            "price",
            "activation_period_days",
            "auction_cost",
            "product_cost",
            "is_available",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "created",
            "modified",
        ]

    def validate(self, attrs):
        values = {}
        for field in [
            "points",
            "price",
            "activation_period_days",
            "auction_cost",
            "product_cost",
        ]:
            if field in attrs:
                values[field] = attrs[field]
            elif self.instance is not None:
                values[field] = getattr(self.instance, field)
            else:
                values[field] = None

        errors = PackageValidationRules.build_errors(values)
        if errors:
            raise serializers.ValidationError(errors)

        return attrs


class PackageSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = [
            "id",
            "name",
            "description",
            "points",
            "price",
            "activation_period_days",
            "auction_cost",
            "product_cost",
            "is_available",
        ]
        read_only_fields = fields


class SellerPackageSerializer(serializers.ModelSerializer):
    package = PackageSummarySerializer(read_only=True)
    package_id = serializers.IntegerField(read_only=True)
    owner_id = serializers.IntegerField(read_only=True)
    owner_nickname = serializers.CharField(
        source="owner.nickname",
        read_only=True,
        allow_blank=True,
    )

    class Meta:
        model = SellerPackage
        fields = [
            "id",
            "owner_id",
            "owner_nickname",
            "package",
            "package_id",
            "remaining_points",
            "activated",
            "activated_at",
            "expires_at",
            "invalidated_at",
            "status",
            "created",
            "modified",
        ]
        read_only_fields = fields


class SellerPackageActiveSerializer(serializers.ModelSerializer):
    package = PackageSummarySerializer(read_only=True)
    auction_cost = serializers.IntegerField(
        source="package.auction_cost",
        read_only=True,
    )
    product_cost = serializers.IntegerField(
        source="package.product_cost",
        read_only=True,
    )

    class Meta:
        model = SellerPackage
        fields = [
            "id",
            "package",
            "remaining_points",
            "expires_at",
            "auction_cost",
            "product_cost",
        ]
        read_only_fields = fields


class SellerPackageRequestSerializer(serializers.Serializer):
    package_id = serializers.IntegerField()

    def create(self, validated_data):
        request = self.context["request"]
        return SellerPackageService.request_package(
            request.user.active_profile,
            validated_data["package_id"],
        )


class SellerPackageStatusTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            SellerPackageStatus.ACTIVE,
            SellerPackageStatus.CANCELLED,
        ]
    )
