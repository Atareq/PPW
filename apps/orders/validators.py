from rest_framework.exceptions import ValidationError

from apps.assets.models import Asset, AssetCategory, AssetStatus
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile


class PurchaseRuleValidator:
    """Shared market purchase validation that does not write state."""

    @staticmethod
    def get_customer_profile(profile):
        if not profile:
            raise ValidationError({"customer": "Active profile is required."})

        if profile.type != ProfileType.CUSTOMER:
            raise ValidationError(
                {"customer": "Only customer profiles can checkout orders."}
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

    @staticmethod
    def get_asset(asset_id, *, lock=False):
        queryset = Asset.objects.select_related("owner", "owner__user")
        if lock:
            queryset = queryset.select_for_update()

        try:
            return queryset.get(pk=asset_id)
        except Asset.DoesNotExist as exc:
            raise ValidationError(
                {"asset_id": "Asset was not found."}
            ) from exc

    @staticmethod
    def get_seller_for_asset(asset):
        try:
            return SellerProfile.objects.get(pk=asset.owner_id)
        except SellerProfile.DoesNotExist as exc:
            raise ValidationError(
                {"items": f"Seller for asset #{asset.pk} was not found."}
            ) from exc

    @classmethod
    def validate_purchase_item(cls, customer_profile, asset, quantity):
        cls.validate_asset_not_deleted(asset)
        cls.validate_asset_seller(asset)
        cls.validate_customer_is_not_owner(customer_profile, asset)
        cls.validate_quantity(asset, quantity)
        cls.validate_asset_available(asset)

    @staticmethod
    def validate_asset_not_deleted(asset):
        if getattr(asset, "deleted", None) is not None:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} is no longer available."}
            )

    @staticmethod
    def validate_asset_available(asset):
        if asset.status != AssetStatus.AVAILABLE:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} is not available."}
            )

        if not asset.is_market_listed:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} is not listed for market."}
            )

    @staticmethod
    def validate_asset_seller(asset):
        owner = asset.owner
        seller_is_invalid = (
            owner.type != ProfileType.SELLER
            or not owner.is_valid
            or not owner.activated
            or getattr(owner, "deleted", None) is not None
        )
        if seller_is_invalid:
            raise ValidationError(
                {"items": f"Asset #{asset.pk} is not owned by a valid seller."}
            )

    @staticmethod
    def validate_customer_is_not_owner(customer_profile, asset):
        if asset.owner.user_id == customer_profile.user_id:
            raise ValidationError(
                {"items": "Customers cannot order their own seller assets."}
            )

    @staticmethod
    def validate_quantity(asset, quantity):
        if quantity < 1:
            raise ValidationError(
                {"quantity": "Quantity must be greater than 0."}
            )

        if asset.category == AssetCategory.BIRD and quantity != 1:
            raise ValidationError(
                {"items": f"Bird asset #{asset.pk} quantity must be 1."}
            )

        if quantity > asset.count:
            raise ValidationError({"detail": "Insufficient stock"})


class CartItemValidator:
    """Validate cart add/update requests against current asset state."""

    def __init__(self, customer, asset_id, quantity, existing_quantity=0):
        self.customer = customer
        self.asset_id = asset_id
        self.quantity = quantity
        self.existing_quantity = existing_quantity
        self.customer_profile = None
        self.asset = None
        self.seller = None

    def validate(self):
        self.customer_profile = PurchaseRuleValidator.get_customer_profile(
            self.customer
        )
        self.asset = PurchaseRuleValidator.get_asset(self.asset_id, lock=True)
        PurchaseRuleValidator.validate_purchase_item(
            self.customer_profile,
            self.asset,
            self.quantity + self.existing_quantity,
        )
        self.seller = PurchaseRuleValidator.get_seller_for_asset(self.asset)
        return self


class OrderCreationValidator:
    """Validate direct market order creation requests."""

    def __init__(self, customer, items_payload, *, lock_assets=False):
        self.customer = customer
        self.items_payload = items_payload
        self.lock_assets = lock_assets
        self.customer_profile = None
        self.asset_map = {}
        self.seller_map = {}

    def validate(self):
        self.customer_profile = PurchaseRuleValidator.get_customer_profile(
            self.customer
        )
        self._validate_non_empty()
        self._validate_duplicate_assets()
        self._retrieve_assets()
        self._retrieve_sellers()
        self._validate_items()
        return self

    @staticmethod
    def get_seller_profile(profile):
        return PurchaseRuleValidator.get_seller_profile(profile)

    def _validate_non_empty(self):
        if not self.items_payload:
            raise ValidationError({"items": "Order must include items."})

    def _validate_duplicate_assets(self):
        asset_ids = [item["asset_id"] for item in self.items_payload]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValidationError(
                {"items": "Duplicate asset_id entries are not allowed."}
            )

    def _retrieve_assets(self):
        asset_ids = sorted({item["asset_id"] for item in self.items_payload})
        queryset = Asset.objects.select_related("owner", "owner__user")
        if self.lock_assets:
            queryset = queryset.select_for_update()

        assets = queryset.filter(id__in=asset_ids).order_by("id")
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

            PurchaseRuleValidator.validate_purchase_item(
                self.customer_profile,
                asset,
                quantity,
            )

            if asset.owner_id not in self.seller_map:
                raise ValidationError(
                    {"items": (f"Seller for asset #{asset.pk} was not found.")}
                )


class OrderCheckoutValidator:
    """Validate a locked active cart before converting it to an order."""

    def __init__(self, customer, cart_items, asset_map):
        self.customer = customer
        self.cart_items = cart_items
        self.asset_map = asset_map
        self.customer_profile = None
        self.seller_map = {}

    def validate(self):
        self.customer_profile = PurchaseRuleValidator.get_customer_profile(
            self.customer
        )
        self._validate_non_empty()
        self._retrieve_sellers()
        self._validate_items()
        return self

    def _validate_non_empty(self):
        if not self.cart_items:
            raise ValidationError({"cart": "Cart is empty."})

    def _retrieve_sellers(self):
        seller_ids = {
            asset.owner_id
            for asset in self.asset_map.values()
            if asset.owner.type == ProfileType.SELLER
        }
        self.seller_map = SellerProfile.objects.in_bulk(seller_ids)

    def _validate_items(self):
        for cart_item in self.cart_items:
            asset = self.asset_map.get(cart_item.asset_id)
            if asset is None:
                raise ValidationError(
                    {"items": "One or more cart assets were not found."}
                )

            PurchaseRuleValidator.validate_purchase_item(
                self.customer_profile,
                asset,
                cart_item.quantity,
            )

            if asset.owner_id not in self.seller_map:
                raise ValidationError(
                    {"items": (f"Seller for asset #{asset.pk} was not found.")}
                )
