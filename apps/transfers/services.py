from rest_framework.exceptions import ValidationError

from apps.assets.models import Asset, AssetCategory
from apps.transfers.models import OwnershipTransfer, TransferType
from apps.users.constants import ProfileType


class OwnershipTransferService:

    @staticmethod
    def create_direct(asset_id, from_profile, to_profile_id, note=""):
        """Validate and execute a direct seller-to-seller ownership transfer."""

        try:
            asset = Asset.objects.select_for_update().get(pk=asset_id)
        except Asset.DoesNotExist:
            raise ValidationError({"asset_id": "الطير غير موجود."})

        if asset.category != AssetCategory.BIRD:
            raise ValidationError({"asset_id": "نقل الملكية متاح للطيور فقط."})

        if asset.owner_id != from_profile.id:
            raise ValidationError({"asset_id": "لا تملك هذا الطير."})

        if to_profile_id == from_profile.id:
            raise ValidationError({"to_profile_id": "لا يمكن نقل الملكية إلى نفسك."})

        from apps.users.models import Profile
        try:
            to_profile = Profile.objects.get(pk=to_profile_id)
        except Profile.DoesNotExist:
            raise ValidationError({"to_profile_id": "البائع المستلم غير موجود."})

        if to_profile.type != ProfileType.SELLER:
            raise ValidationError({"to_profile_id": "يمكن نقل الملكية إلى بائع فقط."})

        # Block any transfer while a non-final payment request exists for this asset
        from apps.payments.models import PaymentRequest, PaymentRequestStatus
        active_statuses = [PaymentRequestStatus.PENDING]
        if PaymentRequest.objects.filter(
            asset_id=asset_id,
            status__in=active_statuses,
        ).exists():
            raise ValidationError({"asset_id": "لا يمكن نقل الملكية أثناء وجود طلب دفع معلق."})

        # Block transfer if the bird is in an active (non-sold, non-cancelled) auction
        from apps.auctions.models import AuctionItem, AuctionItemStatus
        active_auction_statuses = [AuctionItemStatus.ACTIVE, AuctionItemStatus.PENDING]
        if AuctionItem.objects.filter(
            bird_id=asset_id,
            status__in=active_auction_statuses,
        ).exists():
            raise ValidationError({"asset_id": "لا يمكن نقل ملكية طير مشارك في مزاد نشط."})

        # Execute transfer
        asset.owner_id = to_profile_id
        asset.save(update_fields=["owner", "modified"])

        return OwnershipTransfer.objects.create(
            asset_id=asset_id,
            from_profile=from_profile,
            to_profile=to_profile,
            transfer_type=TransferType.DIRECT,
            note=note,
        )

    @staticmethod
    def record_transfer(asset, from_profile_id, to_profile_id, transfer_type):
        """Create an audit record for a transfer already executed by the payments service."""
        OwnershipTransfer.objects.create(
            asset=asset,
            from_profile_id=from_profile_id,
            to_profile_id=to_profile_id,
            transfer_type=transfer_type,
        )
