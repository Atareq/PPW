from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from apps.auctions.models import AuctionItem, AuctionItemStatus
from apps.authentication.models import UserAccount
from apps.deals.models import CompletedDeal, CompletedDealSourceType, CompletedDealType
from apps.deals.validators import CompletedDealValidator
from apps.orders.models import OrderItem, OrderItemStatus
from apps.payments.models import (
    AuctionPaymentRequest,
    MarketPaymentRequest,
    PaymentRequestStatus,
)
from apps.users.models import Profile, SellerProfile


class CompletedDealService:
    """Record the canonical fact that a buyer/seller deal completed.

    Inputs are business sources already finalized by the manual confirmation
    flow (a seller-approved PaymentRequest). Recording never moves money and
    never changes the source or its confirmation. The unique business source
    makes retries return the existing deal. A newly recorded deal emits
    ``DEAL_COMPLETED`` synchronously, inside the caller's transaction, so its
    rewards roll back with the business finalization.
    """

    @staticmethod
    @transaction.atomic
    def create_from_market_completion(order_item):
        """Record a completed market order item; the item must be ``completed``."""

        order_item = OrderItem.objects.select_related(
            "order__owner__user",
            "seller__user",
            "asset",
        ).get(pk=getattr(order_item, "pk", order_item))
        if order_item.status != OrderItemStatus.COMPLETED:
            raise ValidationError(_("Order item is not completed."))
        confirmation = MarketPaymentRequest.objects.filter(
            order_item_id=order_item.pk,
            status=PaymentRequestStatus.APPROVED,
        ).first()
        if confirmation is None:
            raise ValidationError(_("Deal has no approved confirmation."))

        buyer_profile = order_item.order.owner
        seller_profile = order_item.seller
        return CompletedDealService._record_and_emit(
            deal_type=CompletedDealType.MARKET,
            source_type=CompletedDealSourceType.ORDER_ITEM,
            source_id=order_item.pk,
            buyer_profile=buyer_profile,
            seller_profile=seller_profile,
            asset_id=order_item.asset_id,
            amount=order_item.total_price,
            currency=seller_profile.currency,
            completed_at=confirmation.approved_at,
            confirmation=confirmation,
            metadata={
                "order_id": order_item.order_id,
                "quantity": order_item.quantity,
                "payment_request_id": confirmation.pk,
            },
        )

    @staticmethod
    @transaction.atomic
    def create_from_auction_completion(auction_item):
        """Record a completed auction item; its winner request must be approved."""

        auction_item = AuctionItem.objects.select_related(
            "auction",
            "winner__user",
        ).get(pk=getattr(auction_item, "pk", auction_item))
        if auction_item.status != AuctionItemStatus.SOLD or not auction_item.winner_id:
            raise ValidationError(_("Auction item is not sold to a winner."))
        confirmation = AuctionPaymentRequest.objects.filter(
            auction_item_id=auction_item.pk,
            status=PaymentRequestStatus.APPROVED,
        ).first()
        if confirmation is None:
            raise ValidationError(_("Deal has no approved confirmation."))

        seller_profile = SellerProfile.objects.select_related("user").get(
            pk=auction_item.auction.owner_id
        )
        return CompletedDealService._record_and_emit(
            deal_type=CompletedDealType.AUCTION,
            source_type=CompletedDealSourceType.AUCTION_ITEM,
            source_id=auction_item.pk,
            buyer_profile=auction_item.winner,
            seller_profile=seller_profile,
            asset_id=auction_item.bird_id,
            amount=auction_item.current_price,
            currency=seller_profile.currency,
            completed_at=confirmation.approved_at,
            confirmation=confirmation,
            metadata={
                "auction_id": auction_item.auction_id,
                "paired_bird_id": auction_item.paired_bird_id,
                "payment_request_id": confirmation.pk,
            },
        )

    @staticmethod
    def completed_for_buyer(buyer):
        """Deals bought by a profile, or by any mode of an account."""

        if isinstance(buyer, UserAccount):
            return CompletedDeal.objects.filter(buyer_user=buyer)
        return CompletedDeal.objects.filter(buyer_profile_id=getattr(buyer, "pk", buyer))

    @staticmethod
    def completed_for_seller(seller):
        """Deals sold by a profile, or by any mode of an account."""

        if isinstance(seller, UserAccount):
            return CompletedDeal.objects.filter(seller_user=seller)
        return CompletedDeal.objects.filter(
            seller_profile_id=getattr(seller, "pk", seller)
        )

    @staticmethod
    def _record_and_emit(**fields):
        deal, created = CompletedDealService._record(**fields)
        if created:
            CompletedDealService._emit_deal_completed(deal)
        return deal

    @staticmethod
    def _emit_deal_completed(deal):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.DEAL_COMPLETED,
            actor=deal.seller_profile,
            profile=deal.seller_profile,
            context={"deal": deal},
        )

    @staticmethod
    def _record(
        *,
        deal_type,
        source_type,
        source_id,
        buyer_profile,
        seller_profile,
        asset_id,
        amount,
        currency,
        completed_at,
        confirmation,
        metadata,
    ):
        existing = CompletedDeal.all_objects.filter(
            source_type=source_type,
            source_id=source_id,
        ).first()
        if existing is not None:
            return existing, False

        if (
            confirmation.buyer_id != buyer_profile.pk
            or confirmation.seller_id != seller_profile.pk
        ):
            raise ValidationError(_("Deal confirmation parties do not match the source."))
        if completed_at is None:
            raise ValidationError(_("Deal confirmation has no approval time."))

        buyer_profile = Profile.objects.select_related("user").get(pk=buyer_profile.pk)
        seller_profile = Profile.objects.select_related("user").get(pk=seller_profile.pk)
        CompletedDealValidator.validate_parties(
            buyer_user=buyer_profile.user,
            buyer_profile=buyer_profile,
            seller_user=seller_profile.user,
            seller_profile=seller_profile,
        )
        try:
            with transaction.atomic():
                deal = CompletedDeal.objects.create(
                    deal_type=deal_type,
                    source_type=source_type,
                    source_id=source_id,
                    buyer_user=buyer_profile.user,
                    buyer_profile=buyer_profile,
                    seller_user=seller_profile.user,
                    seller_profile=seller_profile,
                    asset_id=asset_id,
                    amount=amount,
                    currency=currency or "",
                    completed_at=completed_at,
                    metadata=metadata,
                )
        except IntegrityError:
            existing = CompletedDeal.all_objects.get(
                source_type=source_type,
                source_id=source_id,
            )
            return existing, False
        return deal, True
