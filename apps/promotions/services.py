from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from dataclasses import dataclass
from decimal import InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.orders.models import OrderItem
from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    CashbackTransactionType,
    DiscountOffer,
    OfferTargetType,
    OfferTriggerType,
    OfferValueType,
    PromotionCampaign,
    PromotionCampaignEffect,
    PromotionCampaignStatus,
    PromotionCampaignType,
    PromotionEffectType,
    PromotionTargetType,
    SellerPackagePromotionSnapshot,
    UserPromotionGrant,
    UserPromotionGrantStatus,
    UserPromotionGrantType,
)
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, Profile

MONEY_QUANT = Decimal("0.01")
ADMIN_ADJUSTMENT_OFFER_TITLE = "Manual Admin Adjustment"
POINTS_PURCHASE_OFFER_TITLE = "Cashback Points Purchase"
ONBOARDING_HIGHLIGHT_OFFER_TITLE = "Onboarding First Auction Highlight 20% Discount"
ONBOARDING_HIGHLIGHT_BENEFIT_CODE = "ONBOARDING_FIRST_HIGHLIGHT"
ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT = Decimal("20.00")


def quantize_money(value):
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def target_priority(target_type):
    return {
        OfferTargetType.ASSET: 2,
        OfferTargetType.CATEGORY: 1,
        OfferTargetType.ALL: 0,
    }[target_type]


class OfferSelectionMixin:
    """Shared offer selection that defaults to normal marketplace global offers."""

    model = None

    @classmethod
    def get_active_offers(cls, *, trigger_type=OfferTriggerType.GLOBAL):
        """Only global offers participate in automatic selection by default."""

        return cls.model.objects.select_related("asset", "created_by").filter(
            is_active=True,
            trigger_type=trigger_type,
        )

    @classmethod
    def _eligible_offers(cls, asset, amount, *, trigger_type=OfferTriggerType.GLOBAL):
        offers = cls.get_active_offers(trigger_type=trigger_type)
        return [
            offer
            for offer in offers
            if amount >= offer.minimum_order_price and cls._target_matches(offer, asset)
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
    def _choose_best(
        cls, asset, amount, calculate, *, trigger_type=OfferTriggerType.GLOBAL
    ):
        eligible_offers = cls._eligible_offers(
            asset,
            amount,
            trigger_type=trigger_type,
        )
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
    def choose_best_offer(
        cls, asset, gross_total, *, trigger_type=OfferTriggerType.GLOBAL
    ):
        return cls._choose_best(
            asset,
            gross_total,
            cls.calculate_discount,
            trigger_type=trigger_type,
        )

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
    def choose_best_offer(
        cls, asset, payable_amount, *, trigger_type=OfferTriggerType.GLOBAL
    ):
        return cls._choose_best(
            asset,
            payable_amount,
            cls.calculate_cashback_earned,
            trigger_type=trigger_type,
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
        if CashbackService._existing_transaction(idempotency_key):
            return CashbackService._get_order_item(order_item.pk)
        payable_amount = quantize_money(item.gross_total - item.discount_amount)
        offer = CashbackOfferService.choose_best_offer(item.asset, payable_amount)
        if offer is None:
            return item

        customer = CustomerProfile.objects.select_for_update().get(
            pk=item.order.owner_id
        )
        if CashbackService._existing_transaction(idempotency_key):
            return CashbackService._get_order_item(order_item.pk)
        cashback_earned_amount = CashbackOfferService.calculate_cashback_earned(
            offer,
            payable_amount,
        )
        cashback_redeemed_amount = CashbackOfferService.calculate_redemption_amount(
            offer,
            customer.cashback_balance,
            payable_amount,
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
        existing = CashbackService._existing_transaction(idempotency_key)
        if existing is not None:
            return existing

        item = CashbackService._get_locked_order_item(order_item.pk)
        if item.cashback_offer_id is None or item.cashback_earned_amount <= 0:
            return None

        customer = CustomerProfile.objects.select_for_update().get(
            pk=item.order.owner_id
        )
        existing = CashbackService._existing_transaction(idempotency_key)
        if existing is not None:
            return existing
        amount = item.cashback_earned_amount
        customer.cashback_balance = quantize_money(customer.cashback_balance + amount)
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
        existing = CashbackService._existing_transaction(idempotency_key)
        if existing is not None:
            return existing

        item = CashbackService._get_locked_order_item(order_item.pk)
        if item.cashback_offer_id is None or item.cashback_redeemed_amount <= 0:
            return None

        customer = CustomerProfile.objects.select_for_update().get(
            pk=item.order.owner_id
        )
        existing = CashbackService._existing_transaction(idempotency_key)
        if existing is not None:
            return existing
        amount = item.cashback_redeemed_amount
        customer.cashback_balance = quantize_money(customer.cashback_balance + amount)
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
                _("Only manager profiles can adjust cashback balances.")
            )

        amount = quantize_money(amount)
        if amount == 0:
            raise ValidationError({"amount": _("Adjustment amount cannot be zero.")})

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
        next_balance = quantize_money(customer.cashback_balance + amount)
        if next_balance < 0:
            raise ValidationError(
                {"amount": _("Adjustment would make cashback balance negative.")}
            )

        customer.cashback_balance = next_balance
        customer.save(update_fields=["cashback_balance", "modified"])
        offer = CashbackService._get_or_create_admin_adjustment_offer(admin_profile)
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
    @transaction.atomic
    def buy_points_with_cashback(customer_profile, cashback_amount, idempotency_key):
        """Convert customer cashback to PP Coins with paired idempotent ledgers."""

        from apps.loyalty.constants import RewardSourceType
        from apps.loyalty.services import PointService

        amount = quantize_money(cashback_amount)
        if amount <= 0:
            raise ValidationError(
                {"cashback_amount": _("Cashback amount must be greater than zero.")}
            )
        points = int(amount * Decimal("20"))
        if points <= 0:
            raise ValidationError(
                {
                    "cashback_amount": _(
                        "Cashback amount must convert to at least 1 PP Coin."
                    )
                }
            )
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise ValidationError({"idempotency_key": _("This field is required.")})

        cashback_key = (
            f"cashback_points_purchase:{customer_profile.pk}:{idempotency_key}"
        )
        note = "Cashback converted into PP Coins."
        customer = CustomerProfile.objects.select_for_update().get(
            pk=customer_profile.pk
        )
        existing = CashbackService._existing_transaction(cashback_key)
        if existing is not None:
            CashbackService._validate_idempotent_transaction(
                existing,
                customer=customer_profile,
                transaction_type=CashbackTransactionType.POINTS_PURCHASE,
                amount=amount,
                note=note,
            )
            point_transaction = PointService.earn_points(
                user=customer.user,
                profile=customer,
                points=int(existing.amount * Decimal("20")),
                reason="Purchased PP Coins with cashback",
                source_type=RewardSourceType.CASHBACK_POINTS_PURCHASE,
                source_id=str(existing.pk),
                idempotency_key=f"points:cashback_purchase:{existing.pk}",
                metadata={"cashback_transaction_id": existing.pk},
            )
            return existing, point_transaction

        if customer.cashback_balance < amount:
            raise ValidationError({"cashback_amount": _("Insufficient cashback balance.")})

        customer.cashback_balance = quantize_money(customer.cashback_balance - amount)
        customer.save(update_fields=["cashback_balance", "modified"])
        offer = CashbackService._get_or_create_points_purchase_offer()
        cashback_transaction = CashbackService._create_transaction(
            customer=customer,
            cashback_offer=offer,
            order_item=None,
            transaction_type=CashbackTransactionType.POINTS_PURCHASE,
            amount=amount,
            balance_after=customer.cashback_balance,
            idempotency_key=cashback_key,
            note=note,
        )
        point_transaction = PointService.earn_points(
            user=customer.user,
            profile=customer,
            points=points,
            reason="Purchased PP Coins with cashback",
            source_type=RewardSourceType.CASHBACK_POINTS_PURCHASE,
            source_id=str(cashback_transaction.pk),
            idempotency_key=f"points:cashback_purchase:{cashback_transaction.pk}",
            metadata={"cashback_transaction_id": cashback_transaction.pk},
        )
        return cashback_transaction, point_transaction

    @staticmethod
    def _get_locked_order_item(order_item_id):
        return (
            OrderItem.objects.select_for_update(of=("self",))
            .select_related("order", "order__owner", "asset", "cashback_offer")
            .get(pk=order_item_id)
        )

    @staticmethod
    def _get_order_item(order_item_id):
        return OrderItem.objects.select_related(
            "order",
            "order__owner",
            "asset",
            "cashback_offer",
        ).get(pk=order_item_id)

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
            with transaction.atomic():
                return CashbackTransaction.objects.create(**kwargs)
        except IntegrityError:
            existing = CashbackService._existing_transaction(kwargs["idempotency_key"])
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
                        _("Idempotency key was already used for a different "
                        "cashback transaction.")
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

    @staticmethod
    def _get_or_create_points_purchase_offer():
        offer, _ = CashbackOffer.objects.get_or_create(
            title=POINTS_PURCHASE_OFFER_TITLE,
            defaults={
                "description": "System offer used for cashback-to-points purchases.",
                "value_type": OfferValueType.FIXED_AMOUNT,
                "value": Decimal("0.01"),
                "target_type": OfferTargetType.ALL,
                "minimum_order_price": Decimal("0.00"),
                "allowed_redemption_amount": Decimal("0.00"),
                "is_active": False,
                "priority": -1000000,
            },
        )
        return offer


class UserPromotionGrantService:
    """Idempotent user-owned grants for non-global promotion prizes."""

    @staticmethod
    @transaction.atomic
    def grant_discount(
        *,
        user,
        profile,
        discount_offer,
        source_type,
        source_id="",
        expires_at=None,
        idempotency_key,
        metadata=None,
    ):
        offer = DiscountOffer.objects.get(pk=discount_offer.pk)
        return UserPromotionGrantService._grant(
            user=user,
            profile=profile,
            grant_type=UserPromotionGrantType.DISCOUNT,
            discount_offer=offer,
            cashback_offer=None,
            source_type=source_type,
            source_id=source_id,
            expires_at=expires_at,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    @staticmethod
    @transaction.atomic
    def grant_cashback(
        *,
        user,
        profile,
        cashback_offer,
        source_type,
        source_id="",
        expires_at=None,
        idempotency_key,
        metadata=None,
    ):
        offer = CashbackOffer.objects.get(pk=cashback_offer.pk)
        return UserPromotionGrantService._grant(
            user=user,
            profile=profile,
            grant_type=UserPromotionGrantType.CASHBACK,
            discount_offer=None,
            cashback_offer=offer,
            source_type=source_type,
            source_id=source_id,
            expires_at=expires_at,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    @staticmethod
    def get_active_grants_for_user(user, profile=None):
        """Return unredeemed grants that are still valid for the user/profile."""

        now = timezone.now()
        queryset = (
            UserPromotionGrant.objects.select_related(
                "user",
                "profile",
                "discount_offer",
                "cashback_offer",
            )
            .filter(
                user=user,
                status=UserPromotionGrantStatus.ACTIVE,
            )
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .order_by("expires_at", "-created")
        )
        if profile is not None:
            queryset = queryset.filter(
                models.Q(profile__isnull=True) | models.Q(profile=profile)
            )
        return queryset

    @staticmethod
    def _grant(
        *,
        user,
        profile,
        grant_type,
        discount_offer,
        cashback_offer,
        source_type,
        source_id,
        expires_at,
        idempotency_key,
        metadata,
    ):
        """Persist a single user grant for an offer prize source."""

        idempotency_key = str(idempotency_key).strip()
        if not idempotency_key:
            raise ValidationError({"idempotency_key": _("Idempotency key is required.")})

        existing = UserPromotionGrant.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing

        try:
            with transaction.atomic():
                return UserPromotionGrant.objects.create(
                    user=user,
                    profile=profile,
                    grant_type=grant_type,
                    discount_offer=discount_offer,
                    cashback_offer=cashback_offer,
                    source_type=source_type or "",
                    source_id=str(source_id or ""),
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    metadata=metadata or {},
                )
        except IntegrityError:
            return UserPromotionGrant.objects.get(idempotency_key=idempotency_key)

    @staticmethod
    def ensure_onboarding_highlight_benefit(user, profile=None):
        """Ensure the one-time 20% first auction-highlight benefit for a UserAccount."""

        return OnboardingHighlightBenefitService.ensure_for_onboarding(
            user=user,
            profile=profile,
        )


class PromotionCampaignService:
    """Manage scheduled promotion campaigns and canonical active-window queries (BE-PRO-01).

    BE-PRO-01 owns campaign classification (MONTHLY, SEASONAL, FLASH), half-open
    scheduling [starts_at, ends_at), enable/disable state, and active campaign
    lookup. Service-level economic application (package/highlight/subscription
    discounts, PP multipliers, conflict resolution) belongs to BE-PRO-02.
    """

    EDITABLE_FIELDS_BEFORE_START = {
        "name",
        "campaign_type",
        "starts_at",
        "ends_at",
        "is_enabled",
        "description",
        "metadata",
    }
    LOCKED_FIELDS_AFTER_START = {
        "campaign_type",
        "starts_at",
        "ends_at",
    }

    @staticmethod
    def _resolve_creator_profile(actor):
        if actor is None:
            return None
        if isinstance(actor, Profile):
            return actor
        if hasattr(actor, "active_profile"):
            try:
                return actor.active_profile
            except PermissionDenied:
                return None
        return None

    @staticmethod
    def _validate_aware_datetime(value, *, field_name: str):
        if value is None:
            raise ValidationError({field_name: [_("This field is required.")]})
        if not hasattr(value, "tzinfo") or timezone.is_naive(value):
            raise ValidationError(
                {field_name: [_("Datetime value must be timezone-aware.")]}
            )
        return value

    @staticmethod
    def _validate_campaign_type(campaign_type):
        if campaign_type not in PromotionCampaignType.values:
            raise ValidationError(
                {"campaign_type": [_("Invalid promotion campaign type.")]}
            )
        return campaign_type

    @staticmethod
    def _validate_metadata(metadata):
        if metadata is None:
            return {}
        if not isinstance(metadata, dict):
            raise ValidationError(
                {"metadata": [_("Campaign metadata must be a JSON object.")]}
            )
        return metadata

    @staticmethod
    def _validate_name(name):
        if not name or not str(name).strip():
            raise ValidationError({"name": [_("Campaign name is required.")]})
        return str(name).strip()

    @staticmethod
    def _get_locked_campaign(campaign):
        campaign_id = getattr(campaign, "pk", campaign)
        try:
            return PromotionCampaign.objects.select_for_update().get(
                pk=campaign_id,
                deleted__isnull=True,
            )
        except PromotionCampaign.DoesNotExist as exc:
            raise NotFound(_("Promotion campaign was not found.")) from exc

    @classmethod
    def create(cls, campaign_data=None, *, created_by=None, **kwargs):
        data = dict(campaign_data or {})
        data.update(kwargs)

        creator = created_by if created_by is not None else data.pop("created_by", None)
        creator_profile = cls._resolve_creator_profile(creator)

        name = cls._validate_name(data.get("name"))
        campaign_type = cls._validate_campaign_type(data.get("campaign_type"))
        starts_at = cls._validate_aware_datetime(
            data.get("starts_at"), field_name="starts_at"
        )
        ends_at = cls._validate_aware_datetime(
            data.get("ends_at"), field_name="ends_at"
        )
        if starts_at >= ends_at:
            raise ValidationError(
                {"ends_at": [_("Campaign start time must be earlier than end time.")]}
            )
        metadata = cls._validate_metadata(data.get("metadata", {}))
        description = data.get("description") or ""
        is_enabled = bool(data.get("is_enabled", True))

        with transaction.atomic():
            return PromotionCampaign.objects.create(
                name=name,
                campaign_type=campaign_type,
                starts_at=starts_at,
                ends_at=ends_at,
                is_enabled=is_enabled,
                description=description,
                metadata=metadata,
                created_by=creator_profile,
            )

    @classmethod
    def update(cls, campaign, data=None, *, now=None, **kwargs):
        changes = dict(data or {})
        changes.update(kwargs)
        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")

        with transaction.atomic():
            locked = cls._get_locked_campaign(campaign)
            locked_fields = cls.LOCKED_FIELDS_AFTER_START & set(changes.keys())
            if locked_fields and now >= locked.starts_at:
                message = (
                    _("Ended promotion campaigns cannot modify schedule or type.")
                    if now >= locked.ends_at
                    else _(
                        "Campaign schedule and type cannot be modified after the campaign starts."
                    )
                )
                raise ValidationError(
                    {field: [message] for field in sorted(locked_fields)}
                )

            if "name" in changes:
                locked.name = cls._validate_name(changes["name"])
            if "campaign_type" in changes:
                locked.campaign_type = cls._validate_campaign_type(
                    changes["campaign_type"]
                )
            if "starts_at" in changes:
                locked.starts_at = cls._validate_aware_datetime(
                    changes["starts_at"], field_name="starts_at"
                )
            if "ends_at" in changes:
                locked.ends_at = cls._validate_aware_datetime(
                    changes["ends_at"], field_name="ends_at"
                )
            if locked.starts_at >= locked.ends_at:
                raise ValidationError(
                    {
                        "ends_at": [
                            _("Campaign start time must be earlier than end time.")
                        ]
                    }
                )
            if "is_enabled" in changes:
                locked.is_enabled = bool(changes["is_enabled"])
            if "description" in changes:
                locked.description = changes["description"] or ""
            if "metadata" in changes:
                locked.metadata = cls._validate_metadata(changes["metadata"])

            locked.save()
            return locked

    @classmethod
    def enable(cls, campaign):
        with transaction.atomic():
            locked = cls._get_locked_campaign(campaign)
            if not locked.is_enabled:
                locked.is_enabled = True
                locked.save(update_fields=["is_enabled", "modified"])
            return locked

    @classmethod
    def disable(cls, campaign):
        with transaction.atomic():
            locked = cls._get_locked_campaign(campaign)
            if locked.is_enabled:
                locked.is_enabled = False
                locked.save(update_fields=["is_enabled", "modified"])
            return locked

    @classmethod
    def effective_status(cls, campaign, now=None) -> str:
        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        return campaign.effective_status(now=now)

    @classmethod
    def active_campaigns(cls, now=None, campaign_type=None):
        now = cls._validate_aware_datetime(now or timezone.now(), field_name="now")
        queryset = PromotionCampaign.objects.filter(
            deleted__isnull=True,
            is_enabled=True,
            starts_at__lte=now,
            ends_at__gt=now,
        )
        if campaign_type is not None:
            normalized_type = cls._validate_campaign_type(campaign_type)
            queryset = queryset.filter(campaign_type=normalized_type)
        return queryset.order_by("starts_at", "id")



@dataclass(frozen=True)
class ServiceDiscountResolution:
    """Immutable result of resolving active service-level promotion discounts (BE-PRO-02)."""

    target_type: str
    base_price: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    final_price: Decimal
    campaign: PromotionCampaign | None = None
    effect: PromotionCampaignEffect | None = None

    @property
    def campaign_id(self) -> int | None:
        return self.campaign.pk if self.campaign is not None else None

    @property
    def effect_id(self) -> int | None:
        return self.effect.pk if self.effect is not None else None

    @property
    def promotion_campaign_id(self) -> int | None:
        return self.campaign_id

    @property
    def promotion_effect_id(self) -> int | None:
        return self.effect_id

    @property
    def has_discount(self) -> bool:
        return self.effect is not None and self.discount_percent > Decimal("0.00")

    def as_dict(self) -> dict:
        return {
            "promotion_campaign_id": self.campaign_id,
            "promotion_effect_id": self.effect_id,
            "base_price": self.base_price,
            "discount_percent": self.discount_percent,
            "discount_amount": self.discount_amount,
            "final_price": self.final_price,
        }

    def __getitem__(self, key):
        mapping = {
            "target_type": self.target_type,
            "base_price": self.base_price,
            "discount_percent": self.discount_percent,
            "discount_amount": self.discount_amount,
            "final_price": self.final_price,
            "campaign": self.campaign,
            "effect": self.effect,
            "campaign_id": self.campaign_id,
            "effect_id": self.effect_id,
            "promotion_campaign_id": self.campaign_id,
            "promotion_effect_id": self.effect_id,
            "has_discount": self.has_discount,
        }
        if key in mapping:
            return mapping[key]
        raise KeyError(key)


class PromotionCampaignEffectService:
    """Manage service-level percentage discount effects on PromotionCampaigns (BE-PRO-02).

    Effects are editable only before `campaign.starts_at`. Once `now >= campaign.starts_at`,
    creating, updating, or deleting effects is blocked.
    """

    @staticmethod
    def _convert_django_validation_error(exc: DjangoValidationError) -> ValidationError:
        if hasattr(exc, "message_dict"):
            return ValidationError(exc.message_dict)
        return ValidationError({"non_field_errors": exc.messages})

    @staticmethod
    def _assert_campaign_mutable(campaign: PromotionCampaign, *, now):
        if now >= campaign.starts_at:
            raise ValidationError(
                {
                    "campaign": [
                        _(
                            "Campaign effects cannot be modified after the campaign starts."
                        )
                    ]
                }
            )

    @staticmethod
    def _validate_effect_type(effect_type):
        if effect_type is None or effect_type == "":
            return PromotionEffectType.PERCENT_DISCOUNT
        if effect_type not in PromotionEffectType.values:
            raise ValidationError(
                {"effect_type": [_("Invalid promotion effect type.")]}
            )
        return effect_type

    @staticmethod
    def _validate_target_type(target_type):
        if target_type not in PromotionTargetType.values:
            raise ValidationError(
                {"target_type": [_("Invalid promotion target type.")]}
            )
        return target_type

    @staticmethod
    def _validate_discount_percent(value) -> Decimal:
        if isinstance(value, (float, bool)):
            raise ValidationError(
                {
                    "discount_percent": [
                        _("Discount percentage must use Decimal precision, not float.")
                    ]
                }
            )
        if value is None or value == "":
            raise ValidationError(
                {"discount_percent": [_("Discount percentage is required.")]}
            )
        try:
            pct = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationError(
                {"discount_percent": [_("Invalid discount percentage.")]}
            ) from exc

        pct = quantize_money(pct)
        if pct <= Decimal("0.00") or pct > Decimal("100.00"):
            raise ValidationError(
                {
                    "discount_percent": [
                        _("Discount percentage must be greater than 0 and at most 100.")
                    ]
                }
            )
        return pct

    @staticmethod
    def _resolve_target_package(target_package):
        if target_package is None or target_package == "":
            return None
        from apps.packages.models import Package

        if isinstance(target_package, Package):
            if target_package.deleted is not None:
                raise ValidationError(
                    {"target_package": [_("Target seller package was not found.")]}
                )
            return target_package
        try:
            return Package.objects.get(pk=int(target_package), deleted__isnull=True)
        except (Package.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(
                {"target_package": [_("Target seller package was not found.")]}
            ) from exc

    @staticmethod
    def _resolve_target_subscription_duration(target_subscription_duration):
        if target_subscription_duration is None or target_subscription_duration == "":
            return None
        from apps.loft_subscriptions.models import SubscriptionDurationOption

        if isinstance(target_subscription_duration, SubscriptionDurationOption):
            if target_subscription_duration.deleted is not None:
                raise ValidationError(
                    {
                        "target_subscription_duration": [
                            _("Target subscription duration option was not found.")
                        ]
                    }
                )
            return target_subscription_duration
        try:
            return SubscriptionDurationOption.objects.get(
                pk=int(target_subscription_duration),
                deleted__isnull=True,
            )
        except (SubscriptionDurationOption.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(
                {
                    "target_subscription_duration": [
                        _("Target subscription duration option was not found.")
                    ]
                }
            ) from exc

    @staticmethod
    def _get_locked_effect(effect, *, campaign=None):
        effect_id = getattr(effect, "pk", effect)
        queryset = PromotionCampaignEffect.objects.select_for_update(
            of=("self",)
        ).select_related(
            "campaign",
            "target_package",
            "target_subscription_duration",
            "created_by",
        ).filter(pk=effect_id, deleted__isnull=True)
        if campaign is not None:
            campaign_id = getattr(campaign, "pk", campaign)
            queryset = queryset.filter(campaign_id=campaign_id)
        locked = queryset.first()
        if locked is None:
            raise NotFound(_("Promotion campaign effect was not found."))
        return locked

    @classmethod
    def list_for_campaign(cls, campaign):
        campaign_id = getattr(campaign, "pk", campaign)
        exists = PromotionCampaign.objects.filter(
            pk=campaign_id,
            deleted__isnull=True,
        ).exists()
        if not exists:
            raise NotFound(_("Promotion campaign was not found."))
        return (
            PromotionCampaignEffect.objects.select_related(
                "campaign",
                "target_package",
                "target_subscription_duration",
                "created_by",
            )
            .filter(campaign_id=campaign_id, deleted__isnull=True)
            .order_by("-discount_percent", "id")
        )

    @classmethod
    def create(cls, campaign, effect_data=None, *, created_by=None, now=None, **kwargs):
        data = dict(effect_data or {})
        data.update(kwargs)
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        creator = created_by if created_by is not None else data.pop("created_by", None)
        creator_profile = PromotionCampaignService._resolve_creator_profile(creator)

        with transaction.atomic():
            locked_campaign = PromotionCampaignService._get_locked_campaign(campaign)
            cls._assert_campaign_mutable(locked_campaign, now=now)

            effect_type = cls._validate_effect_type(
                data.get("effect_type", PromotionEffectType.PERCENT_DISCOUNT)
            )
            target_type = cls._validate_target_type(data.get("target_type"))
            discount_percent = cls._validate_discount_percent(
                data.get("discount_percent")
            )
            target_package = cls._resolve_target_package(data.get("target_package"))
            target_subscription_duration = cls._resolve_target_subscription_duration(
                data.get("target_subscription_duration")
            )
            target_code = str(data.get("target_code") or "").strip()
            description = data.get("description") or ""
            metadata = PromotionCampaignService._validate_metadata(
                data.get("metadata", {})
            )

            effect = PromotionCampaignEffect(
                campaign=locked_campaign,
                effect_type=effect_type,
                target_type=target_type,
                target_package=target_package,
                target_subscription_duration=target_subscription_duration,
                target_code=target_code,
                discount_percent=discount_percent,
                description=description,
                metadata=metadata,
                created_by=creator_profile,
            )
            try:
                effect.save(now=now)
            except DjangoValidationError as exc:
                raise cls._convert_django_validation_error(exc) from exc
            return effect

    @classmethod
    def update(cls, effect, data=None, *, campaign=None, now=None, **kwargs):
        changes = dict(data or {})
        changes.update(kwargs)
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )

        with transaction.atomic():
            locked_effect = cls._get_locked_effect(effect, campaign=campaign)
            locked_campaign = PromotionCampaignService._get_locked_campaign(
                locked_effect.campaign_id
            )
            cls._assert_campaign_mutable(locked_campaign, now=now)

            if "effect_type" in changes:
                locked_effect.effect_type = cls._validate_effect_type(
                    changes["effect_type"]
                )
            if "target_type" in changes:
                locked_effect.target_type = cls._validate_target_type(
                    changes["target_type"]
                )
            if "discount_percent" in changes:
                locked_effect.discount_percent = cls._validate_discount_percent(
                    changes["discount_percent"]
                )
            if "target_package" in changes:
                locked_effect.target_package = cls._resolve_target_package(
                    changes["target_package"]
                )
            if "target_subscription_duration" in changes:
                locked_effect.target_subscription_duration = (
                    cls._resolve_target_subscription_duration(
                        changes["target_subscription_duration"]
                    )
                )
            if "target_code" in changes:
                locked_effect.target_code = str(changes["target_code"] or "").strip()
            if "description" in changes:
                locked_effect.description = changes["description"] or ""
            if "metadata" in changes:
                locked_effect.metadata = PromotionCampaignService._validate_metadata(
                    changes["metadata"]
                )

            try:
                locked_effect.save(now=now)
            except DjangoValidationError as exc:
                raise cls._convert_django_validation_error(exc) from exc
            return locked_effect

    @classmethod
    def delete(cls, effect, *, campaign=None, now=None):
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        with transaction.atomic():
            locked_effect = cls._get_locked_effect(effect, campaign=campaign)
            locked_campaign = PromotionCampaignService._get_locked_campaign(
                locked_effect.campaign_id
            )
            cls._assert_campaign_mutable(locked_campaign, now=now)
            try:
                locked_effect.delete(now=now)
            except DjangoValidationError as exc:
                raise cls._convert_django_validation_error(exc) from exc
            return locked_effect


class PromotionPricingService:
    """Canonical service-level promotion discount resolver (BE-PRO-02).

    Evaluates only effects attached to campaigns returned by
    `PromotionCampaignService.active_campaigns(now=now)`.
    Enforces non-stacking highest-discount resolution with deterministic tie-breaking:
      1. highest `discount_percent`
      2. earlier `campaign.starts_at`
      3. lower `campaign.id`
      4. lower `effect.id`
    """

    @staticmethod
    def _validate_base_price(base_price) -> Decimal:
        if isinstance(base_price, (float, bool)) or base_price is None:
            raise ValidationError(
                {"base_price": [_("Base price must use Decimal precision, not float.")]}
            )
        try:
            amount = Decimal(str(base_price))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationError({"base_price": [_("Invalid base price.")]}) from exc

        amount = quantize_money(amount)
        if amount < Decimal("0.00"):
            raise ValidationError(
                {"base_price": [_("Base price cannot be negative.")]}
            )
        return amount

    @staticmethod
    def _extract_seller_package_id(*, target_id=None, target_package=None):
        candidate = target_package if target_package is not None else target_id
        if candidate is None or candidate == "":
            return None
        pk = getattr(candidate, "pk", candidate)
        try:
            return int(pk)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                {"target_id": [_("Invalid seller package target identifier.")]}
            ) from exc

    @staticmethod
    def _extract_loft_subscription_target(
        *,
        target_id=None,
        target_code=None,
        target_subscription_duration=None,
    ):
        duration_id = None
        code = str(target_code or "").strip()

        if target_subscription_duration is not None:
            duration_id = getattr(
                target_subscription_duration, "pk", target_subscription_duration
            )
            if not code:
                code = str(
                    getattr(target_subscription_duration, "code", "") or ""
                ).strip()
        elif target_id is not None and target_id != "":
            if hasattr(target_id, "pk"):
                duration_id = target_id.pk
                if not code:
                    code = str(getattr(target_id, "code", "") or "").strip()
            else:
                try:
                    duration_id = int(target_id)
                except (ValueError, TypeError):
                    if not code:
                        code = str(target_id).strip()

        if duration_id is not None:
            try:
                duration_id = int(duration_id)
            except (ValueError, TypeError) as exc:
                raise ValidationError(
                    {"target_id": [_("Invalid subscription duration target identifier.")]}
                ) from exc

        return duration_id, code

    @classmethod
    def _effect_matches_target(
        cls,
        effect: PromotionCampaignEffect,
        *,
        target_type: str,
        package_id: int | None,
        duration_id: int | None,
        duration_code: str,
        highlight_code: str,
    ) -> bool:
        if target_type == PromotionTargetType.SELLER_PACKAGE:
            if effect.target_package_id is None:
                return True
            return package_id is not None and effect.target_package_id == package_id

        if target_type == PromotionTargetType.LOFT_SUBSCRIPTION:
            if effect.target_subscription_duration_id is None and not effect.target_code:
                return True
            if (
                duration_id is not None
                and effect.target_subscription_duration_id == duration_id
            ):
                return True
            if duration_code and effect.target_code == duration_code:
                return True
            return False

        if target_type == PromotionTargetType.AUCTION_HIGHLIGHT:
            if not effect.target_code:
                return True
            return bool(highlight_code) and effect.target_code == highlight_code

        return False

    @classmethod
    def resolve_service_discount(
        cls,
        *,
        target_type: str,
        base_price,
        target_id=None,
        target_code: str | None = None,
        target_package=None,
        target_subscription_duration=None,
        now=None,
    ) -> ServiceDiscountResolution:
        validated_target_type = PromotionCampaignEffectService._validate_target_type(
            target_type
        )
        normalized_base_price = cls._validate_base_price(base_price)
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )

        package_id = None
        duration_id = None
        duration_code = ""
        highlight_code = ""

        if validated_target_type == PromotionTargetType.SELLER_PACKAGE:
            package_id = cls._extract_seller_package_id(
                target_id=target_id,
                target_package=target_package,
            )
        elif validated_target_type == PromotionTargetType.LOFT_SUBSCRIPTION:
            duration_id, duration_code = cls._extract_loft_subscription_target(
                target_id=target_id,
                target_code=target_code,
                target_subscription_duration=target_subscription_duration,
            )
        elif validated_target_type == PromotionTargetType.AUCTION_HIGHLIGHT:
            highlight_code = str(target_code or target_id or "").strip()

        active_campaigns = PromotionCampaignService.active_campaigns(now=now)
        candidate_effects = list(
            PromotionCampaignEffect.objects.select_related(
                "campaign",
                "target_package",
                "target_subscription_duration",
            ).filter(
                deleted__isnull=True,
                campaign__in=active_campaigns,
                effect_type=PromotionEffectType.PERCENT_DISCOUNT,
                target_type=validated_target_type,
            )
        )

        matching_effects = [
            effect
            for effect in candidate_effects
            if cls._effect_matches_target(
                effect,
                target_type=validated_target_type,
                package_id=package_id,
                duration_id=duration_id,
                duration_code=duration_code,
                highlight_code=highlight_code,
            )
        ]

        if not matching_effects:
            return ServiceDiscountResolution(
                target_type=validated_target_type,
                base_price=normalized_base_price,
                discount_percent=Decimal("0.00"),
                discount_amount=Decimal("0.00"),
                final_price=normalized_base_price,
                campaign=None,
                effect=None,
            )

        winning_effect = min(
            matching_effects,
            key=lambda eff: (
                -eff.discount_percent,
                eff.campaign.starts_at,
                eff.campaign_id,
                eff.id,
            ),
        )
        discount_percent = quantize_money(winning_effect.discount_percent)
        raw_discount = (
            normalized_base_price * discount_percent / Decimal("100")
        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        discount_amount = min(raw_discount, normalized_base_price)
        final_price = max(
            Decimal("0.00"),
            quantize_money(normalized_base_price - discount_amount),
        )

        return ServiceDiscountResolution(
            target_type=validated_target_type,
            base_price=normalized_base_price,
            discount_percent=discount_percent,
            discount_amount=discount_amount,
            final_price=final_price,
            campaign=winning_effect.campaign,
            effect=winning_effect,
        )


PromotionEffectService = PromotionPricingService



class OnboardingHighlightDiscountSource(models.TextChoices):
    ONBOARDING_FIRST_HIGHLIGHT = (
        "ONBOARDING_FIRST_HIGHLIGHT",
        _("Onboarding First Highlight"),
    )
    CAMPAIGN = "CAMPAIGN", _("Promotion Campaign")
    NONE = "NONE", _("No Discount")


@dataclass(frozen=True)
class OnboardingHighlightQuoteResult:
    """Immutable quote/resolution result for auction-highlight pricing (BE-PRO-03)."""

    base_amount: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    final_amount: Decimal
    discount_source: str
    campaign_id: int | None = None
    effect_id: int | None = None
    onboarding_benefit_id: int | None = None
    onboarding_available: bool = False
    should_consume_on_commit: bool = False
    campaign: PromotionCampaign | None = None
    effect: PromotionCampaignEffect | None = None
    onboarding_grant: UserPromotionGrant | None = None

    @property
    def base_price(self) -> Decimal:
        return self.base_amount

    @property
    def final_price(self) -> Decimal:
        return self.final_amount

    @property
    def promotion_campaign_id(self) -> int | None:
        return self.campaign_id

    @property
    def promotion_effect_id(self) -> int | None:
        return self.effect_id

    @property
    def onboarding_grant_id(self) -> int | None:
        return self.onboarding_benefit_id

    @property
    def has_discount(self) -> bool:
        return (
            self.discount_percent > Decimal("0.00")
            and self.discount_source != OnboardingHighlightDiscountSource.NONE
        )

    def as_dict(self) -> dict:
        return {
            "base_amount": self.base_amount,
            "discount_percent": self.discount_percent,
            "discount_amount": self.discount_amount,
            "final_amount": self.final_amount,
            "discount_source": self.discount_source,
            "campaign_id": self.campaign_id,
            "effect_id": self.effect_id,
            "onboarding_benefit_id": self.onboarding_benefit_id,
            "onboarding_available": self.onboarding_available,
            "should_consume_on_commit": self.should_consume_on_commit,
        }

    def __getitem__(self, key):
        mapping = {
            "base_amount": self.base_amount,
            "base_price": self.base_amount,
            "discount_percent": self.discount_percent,
            "discount_amount": self.discount_amount,
            "final_amount": self.final_amount,
            "final_price": self.final_amount,
            "discount_source": self.discount_source,
            "campaign_id": self.campaign_id,
            "effect_id": self.effect_id,
            "promotion_campaign_id": self.campaign_id,
            "promotion_effect_id": self.effect_id,
            "onboarding_benefit_id": self.onboarding_benefit_id,
            "onboarding_grant_id": self.onboarding_benefit_id,
            "onboarding_available": self.onboarding_available,
            "should_consume_on_commit": self.should_consume_on_commit,
            "campaign": self.campaign,
            "effect": self.effect,
            "onboarding_grant": self.onboarding_grant,
            "has_discount": self.has_discount,
        }
        if key in mapping:
            return mapping[key]
        raise KeyError(key)


class OnboardingHighlightBenefitService:
    """Account-level 20% first paid auction-highlight discount service (BE-PRO-03).

    Invariants:
    - Owned by `UserAccount` (never per `CustomerProfile` or `SellerProfile`).
    - Fixed at `Decimal("20.00")`; non-configurable.
    - Created once at account onboarding (`promotion:onboarding_highlight:user:{user_id}`);
      no retroactive backfill for pre-existing accounts.
    - Quotes never consume the entitlement.
    - Qualifies only when `base_amount > Decimal("0.00")`.
    - Compares against active `AUCTION_HIGHLIGHT` campaign effects using the
      single-best-discount non-stacking rule; equal-value ties (20% vs 20%)
      select `ONBOARDING_FIRST_HIGHLIGHT`.
    - Consumed atomically under `select_for_update()` only when a paid highlight
      commits with `ONBOARDING_FIRST_HIGHLIGHT` as the winning discount.
    - Creates 0 `PointTransaction` rows and 0 notifications.
    """

    DISCOUNT_PERCENT = ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT

    @staticmethod
    def idempotency_key(user) -> str:
        user_id = getattr(user, "pk", user)
        return f"promotion:onboarding_highlight:user:{user_id}"

    @staticmethod
    def _resolve_user(user):
        from apps.authentication.models import UserAccount

        if user is None or isinstance(user, bool):
            raise ValidationError({"user": [_("User account is required.")]})
        if isinstance(user, UserAccount):
            if user.pk is None:
                raise ValidationError({"user": [_("User account must be saved.")]})
            return user
        try:
            return UserAccount.objects.get(pk=int(user))
        except (UserAccount.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(
                {"user": [_("User account was not found.")]}
            ) from exc

    @staticmethod
    def _validate_profile_for_user(user, profile):
        if profile is None:
            return None
        if not isinstance(profile, Profile):
            try:
                profile = Profile.objects.get(pk=int(getattr(profile, "pk", profile)))
            except (Profile.DoesNotExist, ValueError, TypeError) as exc:
                raise ValidationError(
                    {"profile": [_("Profile was not found.")]}
                ) from exc
        if profile.user_id != user.pk:
            raise ValidationError(
                {
                    "profile": [
                        _("Profile does not belong to the specified user account.")
                    ]
                }
            )
        if profile.type == ProfileType.MANAGER:
            raise ValidationError(
                {
                    "profile": [
                        _(
                            "Manager profiles cannot receive or use onboarding highlight benefits."
                        )
                    ]
                }
            )
        return profile

    @staticmethod
    def _get_or_create_onboarding_highlight_offer() -> DiscountOffer:
        offer, _ = DiscountOffer.objects.get_or_create(
            title=ONBOARDING_HIGHLIGHT_OFFER_TITLE,
            defaults={
                "description": (
                    "System offer for one-time 20% onboarding first auction-highlight discount."
                ),
                "value_type": OfferValueType.PERCENTAGE,
                "value": ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT,
                "target_type": OfferTargetType.ALL,
                "minimum_order_price": Decimal("0.00"),
                "trigger_type": OfferTriggerType.ONBOARDING,
                "is_active": False,
                "priority": -1000000,
            },
        )
        return offer

    @classmethod
    def ensure_for_onboarding(cls, user, profile=None) -> UserPromotionGrant:
        """Idempotently ensure the one-time 20% highlight benefit for an onboarded account."""

        from apps.authentication.models import UserAccount

        resolved_user = cls._resolve_user(user)
        resolved_profile = cls._validate_profile_for_user(resolved_user, profile)
        if resolved_user.profiles.filter(
            type=ProfileType.MANAGER,
            deleted__isnull=True,
        ).exists():
            raise ValidationError(
                {
                    "user": [
                        _(
                            "Manager accounts cannot receive onboarding highlight benefits."
                        )
                    ]
                }
            )

        key = cls.idempotency_key(resolved_user)
        with transaction.atomic():
            locked_user = UserAccount.objects.select_for_update().get(
                pk=resolved_user.pk
            )
            existing = (
                UserPromotionGrant.objects.select_for_update(of=("self",))
                .select_related("discount_offer", "user", "profile")
                .filter(user=locked_user, idempotency_key=key)
                .first()
            )
            if existing is not None:
                return existing

            offer = cls._get_or_create_onboarding_highlight_offer()
            metadata = {
                "benefit_code": ONBOARDING_HIGHLIGHT_BENEFIT_CODE,
                "target_type": PromotionTargetType.AUCTION_HIGHLIGHT,
                "discount_percent": str(ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT),
                "onboarding_profile_id": (
                    resolved_profile.pk if resolved_profile is not None else None
                ),
                "onboarding_profile_type": (
                    resolved_profile.type if resolved_profile is not None else None
                ),
            }
            try:
                with transaction.atomic():
                    return UserPromotionGrant.objects.create(
                        user=locked_user,
                        profile=None,
                        grant_type=UserPromotionGrantType.DISCOUNT,
                        discount_offer=offer,
                        cashback_offer=None,
                        source_type=OfferTriggerType.ONBOARDING,
                        source_id=str(locked_user.pk),
                        status=UserPromotionGrantStatus.ACTIVE,
                        expires_at=None,
                        redeemed_at=None,
                        idempotency_key=key,
                        metadata=metadata,
                    )
            except IntegrityError:
                return UserPromotionGrant.objects.select_related(
                    "discount_offer",
                    "user",
                    "profile",
                ).get(idempotency_key=key)

    ensure_benefit = ensure_for_onboarding
    grant_for_onboarding = ensure_for_onboarding

    @classmethod
    def has_prior_onboarding_profile_history(
        cls,
        user,
        *,
        current_profile=None,
    ) -> bool:
        """Return True unless `user` has at most one historical Customer/Seller profile (live, non-deleted)."""

        from apps.users.services import AccountOnboardingService

        resolved_user = cls._resolve_user(user)
        resolved_profile = cls._validate_profile_for_user(
            resolved_user, current_profile
        )
        return AccountOnboardingService.has_prior_customer_seller_profile_history(
            resolved_user,
            current_profile=resolved_profile,
        )

    @classmethod
    def ensure_for_first_profile_onboarding(
        cls,
        user,
        profile=None,
    ) -> UserPromotionGrant | None:
        """Automatic profile-creation hook: grant only on the account's first-ever Customer/Seller profile.

        Uses `Profile.all_objects` (including soft-deleted history) so that:
        - Fresh customer or seller-first onboarding creates the benefit once.
        - Creating a second mode later on an already-onboarded account (including
          historical pre-BE-PRO-03 accounts or accounts with soft-deleted prior
          profiles) never retroactively grants or resets the benefit.
        """

        from apps.authentication.models import UserAccount

        resolved_user = cls._resolve_user(user)
        resolved_profile = cls._validate_profile_for_user(resolved_user, profile)
        if Profile.all_objects.filter(
            user_id=resolved_user.pk,
            type=ProfileType.MANAGER,
            deleted__isnull=True,
        ).exists():
            raise ValidationError(
                {
                    "user": [
                        _(
                            "Manager accounts cannot receive onboarding highlight benefits."
                        )
                    ]
                }
            )

        with transaction.atomic():
            locked_user = UserAccount.objects.select_for_update().get(
                pk=resolved_user.pk
            )
            if cls.has_prior_onboarding_profile_history(
                locked_user,
                current_profile=resolved_profile,
            ):
                return None
            return cls.ensure_for_onboarding(
                user=locked_user,
                profile=resolved_profile,
            )

    @classmethod
    def get_benefit(cls, user) -> UserPromotionGrant | None:
        """Return the account's onboarding highlight grant without creating/backfilling."""

        resolved_user = cls._resolve_user(user)
        key = cls.idempotency_key(resolved_user)
        return (
            UserPromotionGrant.objects.select_related(
                "discount_offer",
                "user",
                "profile",
            )
            .filter(user=resolved_user, idempotency_key=key)
            .first()
        )

    @staticmethod
    def _is_grant_available(grant: UserPromotionGrant | None, *, now) -> bool:
        if grant is None:
            return False
        if grant.status != UserPromotionGrantStatus.ACTIVE:
            return False
        if grant.expires_at is not None and grant.expires_at <= now:
            return False
        return True

    @classmethod
    def is_available(cls, user, *, now=None) -> bool:
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        grant = cls.get_benefit(user)
        return cls._is_grant_available(grant, now=now)

    @classmethod
    def get_benefit_status(cls, user, *, now=None) -> dict:
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        grant = cls.get_benefit(user)
        available = cls._is_grant_available(grant, now=now)
        consumed_at = (
            grant.redeemed_at
            if grant is not None and grant.status == UserPromotionGrantStatus.REDEEMED
            else None
        )
        return {
            "available": available,
            "discount_percent": ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT,
            "consumed_at": consumed_at,
        }

    @classmethod
    def _evaluate_quote(
        cls,
        *,
        grant: UserPromotionGrant | None,
        normalized_base_amount: Decimal,
        target_code: str | None,
        now,
    ) -> OnboardingHighlightQuoteResult:
        onboarding_available = cls._is_grant_available(grant, now=now)
        onboarding_benefit_id = grant.pk if grant is not None else None

        campaign_resolution = PromotionPricingService.resolve_service_discount(
            target_type=PromotionTargetType.AUCTION_HIGHLIGHT,
            base_price=normalized_base_amount,
            target_code=target_code,
            now=now,
        )

        # Free / zero-price highlights do not qualify for paid discount application or consumption.
        if normalized_base_amount <= Decimal("0.00"):
            return OnboardingHighlightQuoteResult(
                base_amount=Decimal("0.00"),
                discount_percent=Decimal("0.00"),
                discount_amount=Decimal("0.00"),
                final_amount=Decimal("0.00"),
                discount_source=OnboardingHighlightDiscountSource.NONE,
                campaign_id=None,
                effect_id=None,
                onboarding_benefit_id=onboarding_benefit_id,
                onboarding_available=onboarding_available,
                should_consume_on_commit=False,
                campaign=None,
                effect=None,
                onboarding_grant=grant,
            )

        onboarding_pct = (
            ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT
            if onboarding_available
            else Decimal("0.00")
        )
        campaign_pct = (
            campaign_resolution.discount_percent
            if campaign_resolution.has_discount
            else Decimal("0.00")
        )

        # Highest percentage wins; equal-value tie (20% vs 20%) goes to ONBOARDING_FIRST_HIGHLIGHT.
        if onboarding_available and onboarding_pct >= campaign_pct:
            discount_percent = ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT
            raw_discount = (
                normalized_base_amount * discount_percent / Decimal("100")
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
            discount_amount = min(raw_discount, normalized_base_amount)
            final_amount = max(
                Decimal("0.00"),
                quantize_money(normalized_base_amount - discount_amount),
            )
            return OnboardingHighlightQuoteResult(
                base_amount=normalized_base_amount,
                discount_percent=discount_percent,
                discount_amount=discount_amount,
                final_amount=final_amount,
                discount_source=(
                    OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
                ),
                campaign_id=None,
                effect_id=None,
                onboarding_benefit_id=onboarding_benefit_id,
                onboarding_available=True,
                should_consume_on_commit=True,
                campaign=None,
                effect=None,
                onboarding_grant=grant,
            )

        if campaign_pct > Decimal("0.00"):
            return OnboardingHighlightQuoteResult(
                base_amount=normalized_base_amount,
                discount_percent=campaign_resolution.discount_percent,
                discount_amount=campaign_resolution.discount_amount,
                final_amount=campaign_resolution.final_price,
                discount_source=OnboardingHighlightDiscountSource.CAMPAIGN,
                campaign_id=campaign_resolution.campaign_id,
                effect_id=campaign_resolution.effect_id,
                onboarding_benefit_id=onboarding_benefit_id,
                onboarding_available=onboarding_available,
                should_consume_on_commit=False,
                campaign=campaign_resolution.campaign,
                effect=campaign_resolution.effect,
                onboarding_grant=grant,
            )

        return OnboardingHighlightQuoteResult(
            base_amount=normalized_base_amount,
            discount_percent=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            final_amount=normalized_base_amount,
            discount_source=OnboardingHighlightDiscountSource.NONE,
            campaign_id=None,
            effect_id=None,
            onboarding_benefit_id=onboarding_benefit_id,
            onboarding_available=onboarding_available,
            should_consume_on_commit=False,
            campaign=None,
            effect=None,
            onboarding_grant=grant,
        )

    @classmethod
    def quote(
        cls,
        *,
        user,
        base_amount,
        target_code: str | None = None,
        now=None,
    ) -> OnboardingHighlightQuoteResult:
        """Resolve best non-stacking auction-highlight discount without consuming."""

        resolved_user = cls._resolve_user(user)
        normalized_base_amount = PromotionPricingService._validate_base_price(
            base_amount
        )
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        grant = cls.get_benefit(resolved_user)
        return cls._evaluate_quote(
            grant=grant,
            normalized_base_amount=normalized_base_amount,
            target_code=target_code,
            now=now,
        )

    @classmethod
    def consume_if_selected(
        cls,
        *,
        user,
        base_amount,
        target_code: str | None = None,
        profile=None,
        source_id: str | None = None,
        now=None,
        require_onboarding_discount: bool = False,
        on_commit_callback=None,
    ) -> OnboardingHighlightQuoteResult:
        """Atomically lock, re-evaluate, and consume the onboarding benefit if selected."""

        from apps.authentication.models import UserAccount

        resolved_user = cls._resolve_user(user)
        resolved_profile = cls._validate_profile_for_user(resolved_user, profile)
        normalized_base_amount = PromotionPricingService._validate_base_price(
            base_amount
        )
        now = PromotionCampaignService._validate_aware_datetime(
            now or timezone.now(), field_name="now"
        )
        key = cls.idempotency_key(resolved_user)

        with transaction.atomic():
            locked_user = UserAccount.objects.select_for_update().get(
                pk=resolved_user.pk
            )
            locked_grant = (
                UserPromotionGrant.objects.select_for_update(of=("self",))
                .select_related("discount_offer", "user", "profile")
                .filter(user=locked_user, idempotency_key=key)
                .first()
            )

            if require_onboarding_discount and not cls._is_grant_available(
                locked_grant, now=now
            ):
                raise ValidationError(
                    {
                        "onboarding_benefit": [
                            _("Onboarding highlight discount is no longer available.")
                        ]
                    }
                )

            quote_result = cls._evaluate_quote(
                grant=locked_grant,
                normalized_base_amount=normalized_base_amount,
                target_code=target_code,
                now=now,
            )

            if (
                quote_result.should_consume_on_commit
                and locked_grant is not None
                and quote_result.discount_source
                == OnboardingHighlightDiscountSource.ONBOARDING_FIRST_HIGHLIGHT
            ):
                metadata = dict(locked_grant.metadata or {})
                metadata.update(
                    {
                        "consumed_at": now.isoformat(),
                        "consumed_by_profile_id": (
                            resolved_profile.pk
                            if resolved_profile is not None
                            else None
                        ),
                        "consumed_by_profile_type": (
                            resolved_profile.type
                            if resolved_profile is not None
                            else None
                        ),
                        "consumed_source_id": str(source_id or ""),
                        "consumed_target_code": str(target_code or "").strip(),
                        "base_amount": str(quote_result.base_amount),
                        "discount_percent": str(quote_result.discount_percent),
                        "discount_amount": str(quote_result.discount_amount),
                        "final_amount": str(quote_result.final_amount),
                    }
                )
                locked_grant.status = UserPromotionGrantStatus.REDEEMED
                locked_grant.redeemed_at = now
                locked_grant.metadata = metadata
                update_fields = ["status", "redeemed_at", "metadata", "modified"]
                if resolved_profile is not None:
                    locked_grant.profile = resolved_profile
                    update_fields.append("profile")
                locked_grant.save(update_fields=update_fields)

            if on_commit_callback is not None:
                on_commit_callback(quote_result)

            return quote_result

    commit_highlight_benefit = consume_if_selected
