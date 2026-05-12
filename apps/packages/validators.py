from rest_framework.exceptions import ValidationError

from apps.users.constants import ProfileType
from apps.users.models import SellerProfile


class PackageValidationRules:
    @staticmethod
    def build_errors(values):
        errors = {}

        positive_fields = [
            "points",
            "activation_period_days",
            "auction_cost",
            "product_cost",
        ]
        for field in positive_fields:
            value = values.get(field)
            if value is not None and value <= 0:
                errors[field] = "Must be greater than 0."

        price = values.get("price")
        if price is not None and price < 0:
            errors["price"] = "Must be greater than or equal to 0."

        points = values.get("points")
        auction_cost = values.get("auction_cost")
        if points is not None and auction_cost is not None:
            if auction_cost > points:
                errors["auction_cost"] = "Auction cost cannot exceed points."

        product_cost = values.get("product_cost")
        if points is not None and product_cost is not None:
            if product_cost > points:
                errors["product_cost"] = "Product cost cannot exceed points."

        return errors


class SellerPackageValidator:
    @staticmethod
    def get_seller_profile(profile):
        if not profile:
            raise ValidationError({"seller": "Active profile is required."})

        if profile.type != ProfileType.SELLER:
            raise ValidationError(
                {"seller": "Only seller profiles can request packages."}
            )

        try:
            return SellerProfile.objects.get(pk=profile.pk)
        except SellerProfile.DoesNotExist as exc:
            raise ValidationError(
                {"seller": "Seller profile was not found."}
            ) from exc
