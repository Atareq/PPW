from rest_framework.exceptions import ValidationError

from apps.assets.models import Asset, AssetCategory, AssetStatus
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile


class OrderCreationValidator:
    """Validate market order creation business rules without writes."""

    def __init__(self, customer, items_payload):
        self.customer = customer
        self.items_payload = items_payload
        self.customer_profile = None
        self.asset_map = {}
        self.seller_map = {}

    def validate(self):
        self.customer_profile = self._get_customer_profile(self.customer)
        self._validate_duplicate_assets()
        self._retrieve_assets()
        self._retrieve_sellers()
        self._validate_items()
        return self

    @staticmethod
    def _get_customer_profile(profile):
        if not profile:
            raise ValidationError({"customer": "Active profile is required."})

        if profile.type != ProfileType.CUSTOMER:
            raise ValidationError(
                {"customer": "Only customer profiles can create orders."}
            )

        try:
            return CustomerProfile.objects.get(pk=profile.pk)
        except CustomerProfile.DoesNotExist as exc:
            raise ValidationError(
                {"customer": "Customer profile was not found."}
            ) from exc

    @staticmethod
    def get_seller_profile(profile):
        if not profile:
            raise ValidationError({"seller": "Active profile is required."})

        if profile.type != ProfileType.SELLER:
            raise ValidationError(
                {"seller": "Only seller profiles can manage order items."}
            )

        try:
            return SellerProfile.objects.get(pk=profile.pk)
        except SellerProfile.DoesNotExist as exc:
            raise ValidationError(
                {"seller": "Seller profile was not found."}
            ) from exc

    def _validate_duplicate_assets(self):
        asset_ids = [item["asset_id"] for item in self.items_payload]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValidationError(
                {"items": "Duplicate asset_id entries are not allowed."}
            )

    def _retrieve_assets(self):
        asset_ids = [item["asset_id"] for item in self.items_payload]
        assets = Asset.objects.select_related("owner", "owner__user").filter(
            id__in=asset_ids
        )
        self.asset_map = {asset.id: asset for asset in assets}

        missing_ids = set(asset_ids) - set(self.asset_map)
        if missing_ids:
            raise ValidationError(
                {"items": "One or more assets were not found."}
            )

    def _retrieve_sellers(self):
        seller_ids = {
            asset.owner_id
            for asset in self.asset_map.values()
            if asset.owner.type == ProfileType.SELLER
        }
        self.seller_map = SellerProfile.objects.in_bulk(seller_ids)

    def _validate_items(self):
        for item in self.items_payload:
            asset = self.asset_map[item["asset_id"]]
            quantity = item["quantity"]

            self._validate_asset_available(asset)
            self._validate_asset_seller(asset)
            self._validate_customer_is_not_owner(asset)
            self._validate_quantity(asset, quantity)

    def _validate_asset_available(self, asset):
        if asset.status != AssetStatus.AVAILABLE:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} is not available."}
            )

        if not asset.is_market_listed:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} is not listed for market."}
            )

    def _validate_asset_seller(self, asset):
        if asset.owner.type != ProfileType.SELLER or not asset.owner.is_valid:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} is not owned by a seller."}
            )

        if asset.owner_id not in self.seller_map:
            raise ValidationError(
                {"items": f"Seller for asset #{asset.pk} was not found."}
            )

    def _validate_customer_is_not_owner(self, asset):
        if asset.owner.user_id == self.customer_profile.user_id:
            raise ValidationError(
                {"items": "Customers cannot order their own seller assets."}
            )

    @staticmethod
    def _validate_quantity(asset, quantity):
        if asset.category == AssetCategory.BIRD:
            if quantity != 1:
                raise ValidationError(
                    {"items": f"Bird asset #{asset.pk} quantity must be 1."}
                )
            if asset.count < 1:
                raise ValidationError(
                    {"items": f"Bird asset #{asset.pk} is out of stock."}
                )
            return

        if quantity > asset.count:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} does not have enough stock."}
            )
