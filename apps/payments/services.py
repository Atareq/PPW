from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.assets.models import Asset, AssetCategory, AssetStatus
from apps.auctions.models import AuctionItem, AuctionItemStatus
from apps.feed.services import BlockService
from apps.orders.models import OrderItem, OrderItemStatus
from apps.orders.services import OrderService
from apps.payments.models import (
    AuctionPaymentRequest,
    MarketPaymentRequest,
    PaymentRequest,
    PaymentRequestEvent,
    PaymentRequestEventType,
    PaymentRequestStatus,
    PaymentRequestType,
)
from apps.payments.validators import PaymentRequestValidator
from apps.promotions.services import CashbackService
from apps.users.constants import ProfileType


def payment_request_queryset():
    """Optimized queryset for list/detail responses across both submodels."""

    return (
        PaymentRequest.objects.select_related(
            "buyer",
            "seller",
            "asset",
            "approved_by",
            "rejected_by",
            "auctionpaymentrequest",
            "auctionpaymentrequest__auction_item",
            "auctionpaymentrequest__auction_item__auction",
            "marketpaymentrequest",
            "marketpaymentrequest__order_item",
            "marketpaymentrequest__order_item__order",
        )
        .prefetch_related("events")
        .order_by("-created")
    )


class PaymentRequestService:
    """Manual sale-completion workflow shared by auction and market sales."""

    NON_FINAL_STATUSES = [
        PaymentRequestStatus.PENDING,
        PaymentRequestStatus.REJECTED,
    ]

    @staticmethod
    @transaction.atomic
    def create_for_auction_item(
        auction_item_id,
        buyer_profile,
        buyer_note=None,
        payment_proof=None,
    ):
        """Create the winner's pending request for a sold auction item."""

        PaymentRequestValidator.ensure_customer(buyer_profile)
        item = (
            AuctionItem.objects.select_for_update()
            .select_related(
                "auction",
                "auction__owner",
                "bird",
                "paired_bird",
                "winner",
            )
            .get(pk=auction_item_id)
        )

        if item.status != AuctionItemStatus.SOLD:
            raise ValidationError("Auction item must be sold.")

        if item.winner_id != buyer_profile.pk:
            raise ValidationError("Only the winning buyer can create this request.")

        seller = item.auction.owner
        if seller.type != ProfileType.SELLER:
            raise ValidationError("Auction must be owned by a seller.")
        PaymentRequestService._validate_not_blocked(buyer_profile, seller)

        asset = item.bird
        PaymentRequestService._validate_no_active_duplicate(
            buyer_profile,
            asset,
        )

        payment_request = AuctionPaymentRequest.objects.create(
            buyer=buyer_profile,
            seller=seller,
            asset=asset,
            amount=item.current_price,
            status=PaymentRequestStatus.PENDING,
            buyer_note=buyer_note or "",
            payment_proof=payment_proof,
            auction_item=item,
        )
        PaymentRequestService._log_event(
            payment_request,
            PaymentRequestEventType.CREATED,
            actor=buyer_profile,
        )
        PaymentRequestService._schedule_notification(
            payment_request=payment_request,
            recipient_profile=seller,
            actor_profile=buyer_profile,
            notification_type="payment_request_created",
            title="New payment request",
            body="A buyer created a payment request for your auction item.",
            idempotency_key=f"payment_request_created:{payment_request.pk}",
        )
        return PaymentRequestService.get_request(payment_request.pk)

    @staticmethod
    @transaction.atomic
    def create_for_order_item(
        order_item_id,
        buyer_profile,
        buyer_note=None,
        payment_proof=None,
    ):
        """Create the order owner's pending request for an approved order item."""

        PaymentRequestValidator.ensure_customer(buyer_profile)
        item = (
            OrderItem.objects.select_for_update()
            .select_related("order", "order__owner", "asset", "seller")
            .get(pk=order_item_id)
        )

        if item.order.owner_id != buyer_profile.pk:
            raise ValidationError("Only the order owner can create this request.")

        if item.status != OrderItemStatus.APPROVED:
            raise ValidationError("Order item must be approved.")
        PaymentRequestService._validate_not_blocked(buyer_profile, item.seller)

        PaymentRequestService._validate_no_active_duplicate(
            buyer_profile,
            item.asset,
        )

        payment_request = MarketPaymentRequest.objects.create(
            buyer=buyer_profile,
            seller=item.seller,
            asset=item.asset,
            amount=item.total_price,
            status=PaymentRequestStatus.PENDING,
            buyer_note=buyer_note or "",
            payment_proof=payment_proof,
            order_item=item,
        )
        PaymentRequestService._log_event(
            payment_request,
            PaymentRequestEventType.CREATED,
            actor=buyer_profile,
        )
        PaymentRequestService._schedule_notification(
            payment_request=payment_request,
            recipient_profile=item.seller,
            actor_profile=buyer_profile,
            notification_type="payment_request_created",
            title="New payment request",
            body="A buyer created a payment request for your order item.",
            idempotency_key=f"payment_request_created:{payment_request.pk}",
        )
        return PaymentRequestService.get_request(payment_request.pk)

    @staticmethod
    @transaction.atomic
    def update_by_buyer(payment_request_id, buyer_profile, buyer_note=None, payment_proof=None):
        """Update buyer note and/or payment proof; rejected requests are resubmitted as pending."""

        PaymentRequestValidator.ensure_customer(buyer_profile)
        payment_request = PaymentRequestService._get_locked_request(payment_request_id)

        if payment_request.buyer_id != buyer_profile.pk:
            raise NotFound("Payment request was not found.")

        if payment_request.status not in PaymentRequestService.NON_FINAL_STATUSES:
            raise ValidationError("Only pending or rejected requests can be updated.")

        update_fields = ["modified"]
        if buyer_note is not None:
            payment_request.buyer_note = buyer_note
            update_fields.append("buyer_note")
        if payment_proof is not None:
            payment_request.payment_proof = payment_proof
            update_fields.append("payment_proof")

        if payment_request.status == PaymentRequestStatus.REJECTED:
            payment_request.status = PaymentRequestStatus.PENDING
            payment_request.rejected_at = None
            payment_request.rejected_by = None
            payment_request.seller_note = ""
            update_fields.extend(
                [
                    "status",
                    "rejected_at",
                    "rejected_by",
                    "seller_note",
                ]
            )

        payment_request.save(update_fields=update_fields)
        PaymentRequestService._log_event(
            payment_request,
            PaymentRequestEventType.UPDATED,
            actor=buyer_profile,
        )
        return PaymentRequestService.get_request(payment_request.pk)

    @staticmethod
    @transaction.atomic
    def approve(payment_request_id, seller_profile):
        """Finalize a pending request; market and auction sources differ."""

        PaymentRequestValidator.ensure_seller(seller_profile)
        payment_request = PaymentRequestService._get_locked_request(payment_request_id)

        if payment_request.seller_id != seller_profile.pk:
            raise NotFound("Payment request was not found.")

        if payment_request.status != PaymentRequestStatus.PENDING:
            raise ValidationError("Only pending requests can be approved.")

        if payment_request.request_type == PaymentRequestType.MARKET:
            PaymentRequestService._approve_market_request(payment_request)
        elif payment_request.request_type == PaymentRequestType.AUCTION:
            PaymentRequestService._approve_auction_request(payment_request)
        else:
            raise ValidationError("Unsupported payment request type.")

        payment_request.status = PaymentRequestStatus.APPROVED
        payment_request.approved_by = seller_profile
        payment_request.approved_at = timezone.now()
        payment_request.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "modified",
            ]
        )
        PaymentRequestService._log_event(
            payment_request,
            PaymentRequestEventType.APPROVED,
            actor=seller_profile,
        )
        PaymentRequestService._schedule_notification(
            payment_request=payment_request,
            recipient_profile=payment_request.buyer,
            actor_profile=seller_profile,
            notification_type="payment_request_approved",
            title="Payment request approved",
            body="The seller approved your payment request.",
            idempotency_key=f"payment_request_approved:{payment_request.pk}",
        )
        PaymentRequestService._handle_approved_rewards(payment_request)
        return PaymentRequestService.get_request(payment_request.pk)

    @staticmethod
    @transaction.atomic
    def reject(payment_request_id, seller_profile, seller_note=None):
        """Reject a pending request without mutating stock, ownership, or order state."""

        PaymentRequestValidator.ensure_seller(seller_profile)
        payment_request = PaymentRequestService._get_locked_request(payment_request_id)

        if payment_request.seller_id != seller_profile.pk:
            raise NotFound("Payment request was not found.")

        if payment_request.status != PaymentRequestStatus.PENDING:
            raise ValidationError("Only pending requests can be rejected.")

        payment_request.status = PaymentRequestStatus.REJECTED
        payment_request.rejected_by = seller_profile
        payment_request.rejected_at = timezone.now()
        if seller_note is not None:
            payment_request.seller_note = seller_note

        payment_request.save(
            update_fields=[
                "status",
                "rejected_by",
                "rejected_at",
                "seller_note",
                "modified",
            ]
        )
        PaymentRequestService._log_event(
            payment_request,
            PaymentRequestEventType.REJECTED,
            actor=seller_profile,
        )
        PaymentRequestService._schedule_notification(
            payment_request=payment_request,
            recipient_profile=payment_request.buyer,
            actor_profile=seller_profile,
            notification_type="payment_request_rejected",
            title="Payment request rejected",
            body="The seller rejected your payment request.",
            idempotency_key=f"payment_request_rejected:{payment_request.pk}",
        )
        return PaymentRequestService.get_request(payment_request.pk)

    @staticmethod
    def get_request(payment_request_id):
        return payment_request_queryset().get(pk=payment_request_id)

    @staticmethod
    def _get_locked_request(payment_request_id):
        return (
            PaymentRequest.objects.select_for_update()
            .select_related("buyer", "seller", "asset")
            .get(pk=payment_request_id)
        )

    @staticmethod
    def _validate_no_active_duplicate(buyer_profile, asset):
        if PaymentRequest.objects.filter(
            buyer=buyer_profile,
            asset=asset,
            status__in=PaymentRequestService.NON_FINAL_STATUSES,
        ).exists():
            raise ValidationError(
                "An active payment request already exists for this asset."
            )

    @staticmethod
    def _validate_not_blocked(buyer_profile, seller_profile):
        if BlockService.is_blocked_between(buyer_profile, seller_profile):
            raise ValidationError(
                "Payment requests are not allowed while a block is active."
            )

    @staticmethod
    def _approve_market_request(payment_request):
        """Reduce stock and complete the order item after seller approval."""

        market_request = (
            MarketPaymentRequest.objects.select_for_update()
            .select_related("order_item", "order_item__order", "order_item__asset")
            .get(pk=payment_request.pk)
        )
        order_item = (
            OrderItem.objects.select_for_update()
            .select_related("order", "asset", "seller")
            .get(pk=market_request.order_item_id)
        )

        if order_item.status != OrderItemStatus.APPROVED:
            raise ValidationError("Order item must still be approved.")

        if order_item.order.owner_id != payment_request.buyer_id:
            raise ValidationError("Payment request buyer does not match the order.")

        if order_item.seller_id != payment_request.seller_id:
            raise ValidationError("Payment request seller does not match the item.")

        if order_item.asset_id != payment_request.asset_id:
            raise ValidationError("Payment request asset does not match the item.")

        asset = PaymentRequestService._get_locked_asset(payment_request.asset_id)
        PaymentRequestService._reduce_market_stock(asset, order_item.quantity)

        if asset.category == AssetCategory.BIRD:
            PaymentRequestService._transfer_asset_owner(
                asset,
                from_profile_id=payment_request.seller_id,
                to_profile_id=payment_request.buyer_id,
            )

        order_item.status = OrderItemStatus.COMPLETED
        order_item.save(update_fields=["status", "modified"])
        CashbackService.earn_for_completed_order_item(order_item)
        OrderService.refresh_order_status(order_item.order_id)

    @staticmethod
    def _approve_auction_request(payment_request):
        """Transfer the sold auction bird pair, if any, after seller approval."""

        auction_request = (
            AuctionPaymentRequest.objects.select_for_update()
            .select_related(
                "auction_item",
                "auction_item__auction",
                "auction_item__bird",
                "auction_item__paired_bird",
            )
            .get(pk=payment_request.pk)
        )
        auction_item = (
            AuctionItem.objects.select_for_update()
            .select_related("auction", "bird", "paired_bird", "winner")
            .get(pk=auction_request.auction_item_id)
        )

        if auction_item.status != AuctionItemStatus.SOLD:
            raise ValidationError("Auction item must still be sold.")

        if auction_item.winner_id != payment_request.buyer_id:
            raise ValidationError("Payment request buyer does not match the winner.")

        if auction_item.auction.owner_id != payment_request.seller_id:
            raise ValidationError("Payment request seller does not match the auction.")

        if auction_item.bird_id != payment_request.asset_id:
            raise ValidationError("Payment request asset does not match the item.")

        assets = [auction_item.bird_id]
        if auction_item.paired_bird_id:
            assets.append(auction_item.paired_bird_id)

        locked_assets = {
            asset.pk: asset
            for asset in Asset.all_objects.select_for_update().filter(pk__in=assets)
        }
        for asset_id in assets:
            PaymentRequestService._transfer_asset_owner(
                locked_assets[asset_id],
                from_profile_id=payment_request.seller_id,
                to_profile_id=payment_request.buyer_id,
            )

    @staticmethod
    def _get_locked_asset(asset_id):
        try:
            return Asset.all_objects.select_for_update().get(pk=asset_id)
        except Asset.DoesNotExist as exc:
            raise ValidationError({"asset": "Asset is no longer available."}) from exc

    @staticmethod
    def _reduce_market_stock(asset, quantity):
        """Apply market stock mutation at payment approval time only."""

        if asset.count < quantity:
            raise ValidationError({"detail": "Insufficient stock"})

        asset.count -= quantity
        update_fields = ["count", "modified"]
        if asset.count == 0:
            asset.status = AssetStatus.SOLD
            asset.is_market_listed = False
            update_fields.extend(["status", "is_market_listed"])

        asset.save(update_fields=update_fields)

    @staticmethod
    def _transfer_asset_owner(asset, *, from_profile_id, to_profile_id):
        """Move bird ownership, treating an already-transferred asset as idempotent."""

        if asset.owner_id == to_profile_id:
            return

        if asset.owner_id != from_profile_id:
            raise ValidationError("Seller does not currently own this asset.")

        asset.owner_id = to_profile_id
        asset.save(update_fields=["owner", "modified"])

    @staticmethod
    def _log_event(payment_request, event_type, *, actor=None, metadata=None):
        PaymentRequestEvent.objects.create(
            payment_request=payment_request,
            event_type=event_type,
            actor=actor,
            metadata=metadata or {},
        )

    @staticmethod
    def _handle_approved_rewards(payment_request):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        event_type = {
            PaymentRequestType.AUCTION: (
                RewardEventType.AUCTION_PAYMENT_REQUEST_APPROVED
            ),
            PaymentRequestType.MARKET: (
                RewardEventType.MARKET_PAYMENT_REQUEST_APPROVED
            ),
        }.get(payment_request.request_type)
        if event_type is None:
            return

        RewardEngine.handle_event(
            event_type,
            actor=payment_request.seller,
            profile=payment_request.seller,
            context={"payment_request": payment_request},
        )

    @staticmethod
    def _schedule_notification(
        *,
        payment_request,
        recipient_profile,
        actor_profile,
        notification_type,
        title,
        body,
        idempotency_key,
    ):
        from apps.notifications.services import NotificationService

        transaction.on_commit(
            lambda: NotificationService.safe_create_notification(
                recipient_profile=recipient_profile,
                actor_profile=actor_profile,
                notification_type=notification_type,
                title=title,
                body=body,
                object_type="payment_request",
                object_id=payment_request.pk,
                metadata={
                    "payment_request_id": payment_request.pk,
                    "asset_id": payment_request.asset_id,
                },
                idempotency_key=idempotency_key,
            )
        )
