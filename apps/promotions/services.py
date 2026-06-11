from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django.db import IntegrityError, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.orders.models import OrderItem
from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    CashbackTransactionType,
    DiscountOffer,
    OfferTargetType,
    OfferValueType,
)
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile

MONEY_QUANT = Decimal("0.01")
ADMIN_ADJUSTMENT_OFFER_TITLE = "Manual Admin Adjustment"


def quantize_money(value):
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def target_priority(target_type):
    return {
        OfferTargetType.ASSET: 2,
        OfferTargetType.CATEGORY: 1,
        OfferTargetType.ALL: 0,
    }[target_type]


class OfferSelectionMixin:
    model = None

    @classmethod
    def get_active_offers(cls):
        return cls.model.objects.select_related("asset", "created_by").filter(
            is_active=True
        )

    @classmethod
    def _eligible_offers(cls, asset, amount):
        offers = cls.get_active_offers()
        return [
            offer
            for offer in offers
            if amount >= offer.minimum_order_price
            and cls._target_matches(offer, asset)
        ]

    @staticmethod
    def _target_matches(offer, asset):
        if offer.target_type == OfferTargetType.ALL:
            return True
        if offer.target_type == OfferTargetType.CATEGORY:
            return offer.category == asset.category
        if offer.target_type == OfferTargetType.ASSET:
            return offer.asset_id == asset.pk
        return False

    @staticmethod
    def _calculated_value(offer, amount):
        amount = Decimal(amount)
        if offer.value_type == OfferValueType.PERCENTAGE:
            return quantize_money(amount * offer.value / Decimal("100.00"))
        return quantize_money(min(offer.value, amount))

    @classmethod
    def _choose_best(cls, asset, amount, calculate):
        eligible_offers = cls._eligible_offers(asset, amount)
        if not eligible_offers:
            return None

        return max(
            eligible_offers,
            key=lambda offer: (
                calculate(offer, amount),
                target_priority(offer.target_type),
                offer.priority,
                offer.created,
                offer.pk,
            ),
        )


class DiscountOfferService(OfferSelectionMixin):
    model = DiscountOffer

    @classmethod
    def choose_best_offer(cls, asset, gross_total):
        return cls._choose_best(asset, gross_total, cls.calculate_discount)

    @staticmethod
    def calculate_discount(offer, gross_total):
        return OfferSelectionMixin._calculated_value(offer, gross_total)

    @staticmethod
    def activate(offer):
        offer = DiscountOffer.objects.get(pk=offer.pk)
        offer.is_active = True
        offer.save(update_fields=["is_active", "modified"])
        return offer

    @staticmethod
    def deactivate(offer):
        offer = DiscountOffer.objects.get(pk=offer.pk)
        offer.is_active = False
        offer.save(update_fields=["is_active", "modified"])
        return offer


class CashbackOfferService(OfferSelectionMixin):
    model = CashbackOffer

    @classmethod
    def choose_best_offer(cls, asset, payable_amount):
        return cls._choose_best(
            asset,
            payable_amount,
            cls.calculate_cashback_earned,
        )

    @staticmethod
    def calculate_cashback_earned(offer, payable_amount):
        return OfferSelectionMixin._calculated_value(offer, payable_amount)

    @staticmethod
    def calculate_redemption_amount(
        offer,
        customer_balance,
        payable_amount,
    ):
        return quantize_money(
            min(
                Decimal(customer_balance),
                offer.allowed_redemption_amount,
                Decimal(payable_amount),
            )
        )

    @staticmethod
    def activate(offer):
        offer = CashbackOffer.objects.get(pk=offer.pk)
        offer.is_active = True
        offer.save(update_fields=["is_active", "modified"])
        return offer

    @staticmethod
    def deactivate(offer):
        offer = CashbackOffer.objects.get(pk=offer.pk)
        offer.is_active = False
        offer.save(update_fields=["is_active", "modified"])
        return offer


class CashbackService:
    """Ledger-backed customer cashback balance changes."""

    @staticmethod
    @transaction.atomic
    def redeem_for_order_item(order_item):
        """Apply automatic cashback redemption at seller item approval time."""

        idempotency_key = f"redeemed:order_item:{order_item.pk}"
        if CashbackService._existing_transaction(idempotency_key):
            return CashbackService._get_order_item(order_item.pk)

        item = CashbackService._get_locked_order_item(order_item.pk)
        payable_amount = quantize_money(item.gross_total - item.discount_amount)
        offer = CashbackOfferService.choose_best_offer(item.asset, payable_amount)
        if offer is None:
            return item

        customer = CustomerProfile.objects.select_for_update().get(
            pk=item.order.owner_id
        )
        cashback_earned_amount = (
            CashbackOfferService.calculate_cashback_earned(
                offer,
                payable_amount,
            )
        )
        cashback_redeemed_amount = (
            CashbackOfferService.calculate_redemption_amount(
                offer,
                customer.cashback_balance,
                payable_amount,
            )
        )

        if cashback_redeemed_amount > 0:
            customer.cashback_balance = quantize_money(
                customer.cashback_balance - cashback_redeemed_amount
            )
            customer.save(update_fields=["cashback_balance", "modified"])
            CashbackService._create_transaction(
                customer=customer,
                cashback_offer=offer,
                order_item=item,
                transaction_type=CashbackTransactionType.REDEEMED,
                amount=cashback_redeemed_amount,
                balance_after=customer.cashback_balance,
                idempotency_key=idempotency_key,
                note="Cashback redeemed for order item approval.",
            )

        item.cashback_offer = offer
        item.cashback_redeemed_amount = cashback_redeemed_amount
        item.cashback_earned_amount = cashback_earned_amount
        item.save(
            update_fields=[
                "cashback_offer",
                "cashback_redeemed_amount",
                "cashback_earned_amount",
                "gross_total",
                "total_price",
                "modified",
            ]
        )
        return CashbackService._get_order_item(item.pk)

    @staticmethod
    @transaction.atomic
    def earn_for_completed_order_item(order_item):
        """Credit earned cashback once after marketplace sale completion."""

        idempotency_key = f"earned:order_item:{order_item.pk}"
        if CashbackService._existing_transaction(idempotency_key):
            return CashbackService._existing_transaction(idempotency_key)

        item = CashbackService._get_locked_order_item(order_item.pk)
        if item.cashback_offer_id is None or item.cashback_earned_amount <= 0:
            return None

        customer = CustomerProfile.objects.select_for_update().get(
            pk=item.order.owner_id
        )
        amount = item.cashback_earned_amount
        customer.cashback_balance = quantize_money(
            customer.cashback_balance + amount
        )
        customer.save(update_fields=["cashback_balance", "modified"])
        return CashbackService._create_transaction(
            customer=customer,
            cashback_offer=item.cashback_offer,
            order_item=item,
            transaction_type=CashbackTransactionType.EARNED,
            amount=amount,
            balance_after=customer.cashback_balance,
            idempotency_key=idempotency_key,
            note="Cashback earned for completed order item.",
        )

    @staticmethod
    @transaction.atomic
    def refund_redeemed_for_order_item(order_item):
        """Return redeemed cashback if an approved item is later cancelled."""

        idempotency_key = f"refunded:order_item:{order_item.pk}"
        if CashbackService._existing_transaction(idempotency_key):
            return CashbackService._existing_transaction(idempotency_key)

        item = CashbackService._get_locked_order_item(order_item.pk)
        if item.cashback_offer_id is None or item.cashback_redeemed_amount <= 0:
            return None

        customer = CustomerProfile.objects.select_for_update().get(
            pk=item.order.owner_id
        )
        amount = item.cashback_redeemed_amount
        customer.cashback_balance = quantize_money(
            customer.cashback_balance + amount
        )
        customer.save(update_fields=["cashback_balance", "modified"])
        return CashbackService._create_transaction(
            customer=customer,
            cashback_offer=item.cashback_offer,
            order_item=item,
            transaction_type=CashbackTransactionType.REFUNDED,
            amount=amount,
            balance_after=customer.cashback_balance,
            idempotency_key=idempotency_key,
            note="Cashback redemption refunded for order item.",
        )

    @staticmethod
    @transaction.atomic
    def admin_adjust(
        customer_profile,
        amount,
        admin_profile,
        note="",
        idempotency_key=None,
    ):
        """Manager-only signed cashback balance adjustment with audit trail."""

        if admin_profile.type != ProfileType.MANAGER:
            raise PermissionDenied(
                "Only manager profiles can adjust cashback balances."
            )

        amount = quantize_money(amount)
        if amount == 0:
            raise ValidationError({"amount": "Adjustment amount cannot be zero."})

        idempotency_key = (
            str(idempotency_key).strip()
            if idempotency_key
            else f"admin_adjustment:{uuid4()}"
        )
        existing = CashbackService._existing_transaction(idempotency_key)
        if existing is not None:
            CashbackService._validate_idempotent_transaction(
                existing,
                customer=customer_profile,
                transaction_type=CashbackTransactionType.ADMIN_ADJUSTMENT,
                amount=amount,
                adjusted_by=admin_profile,
                note=note or "",
            )
            return existing

        customer = CustomerProfile.objects.select_for_update().get(
            pk=customer_profile.pk
        )
        next_balance = quantize_money(customer.cashback_balance + amount)
        if next_balance < 0:
            raise ValidationError(
                {"amount": "Adjustment would make cashback balance negative."}
            )

        customer.cashback_balance = next_balance
        customer.save(update_fields=["cashback_balance", "modified"])
        offer = CashbackService._get_or_create_admin_adjustment_offer(
            admin_profile
        )
        return CashbackService._create_transaction(
            customer=customer,
            cashback_offer=offer,
            order_item=None,
            transaction_type=CashbackTransactionType.ADMIN_ADJUSTMENT,
            amount=amount,
            balance_after=next_balance,
            adjusted_by=admin_profile,
            idempotency_key=idempotency_key,
            note=note or "",
        )

    @staticmethod
    def _get_locked_order_item(order_item_id):
        return (
            OrderItem.objects.select_for_update()
            .select_related("order", "order__owner", "asset", "cashback_offer")
            .get(pk=order_item_id)
        )

    @staticmethod
    def _get_order_item(order_item_id):
        return (
            OrderItem.objects.select_related(
                "order",
                "order__owner",
                "asset",
                "cashback_offer",
            )
            .get(pk=order_item_id)
        )

    @staticmethod
    def _existing_transaction(idempotency_key):
        if not idempotency_key:
            return None
        return (
            CashbackTransaction.objects.select_related(
                "customer",
                "cashback_offer",
                "order_item",
                "adjusted_by",
            )
            .filter(idempotency_key=idempotency_key)
            .first()
        )

    @staticmethod
    def _create_transaction(**kwargs):
        try:
            return CashbackTransaction.objects.create(**kwargs)
        except IntegrityError:
            existing = CashbackService._existing_transaction(
                kwargs["idempotency_key"]
            )
            if existing is not None:
                return existing
            raise

    @staticmethod
    def _validate_idempotent_transaction(
        transaction,
        *,
        customer,
        transaction_type,
        amount,
        adjusted_by=None,
        note="",
    ):
        adjusted_by_id = adjusted_by.pk if adjusted_by else None
        matches = (
            transaction.customer_id == customer.pk
            and transaction.transaction_type == transaction_type
            and transaction.amount == amount
            and transaction.adjusted_by_id == adjusted_by_id
            and transaction.note == note
        )
        if not matches:
            raise ValidationError(
                {
                    "idempotency_key": (
                        "Idempotency key was already used for a different "
                        "cashback transaction."
                    )
                }
            )

    @staticmethod
    def _get_or_create_admin_adjustment_offer(admin_profile):
        offer, _ = CashbackOffer.objects.get_or_create(
            title=ADMIN_ADJUSTMENT_OFFER_TITLE,
            defaults={
                "description": "System offer used for manual cashback adjustments.",
                "value_type": OfferValueType.FIXED_AMOUNT,
                "value": Decimal("0.01"),
                "target_type": OfferTargetType.ALL,
                "minimum_order_price": Decimal("0.00"),
                "allowed_redemption_amount": Decimal("0.00"),
                "is_active": False,
                "priority": -1000000,
                "created_by": admin_profile,
            },
        )
        return offer
