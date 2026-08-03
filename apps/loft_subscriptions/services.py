from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.authentication.models import UserAccount
from apps.loft_subscriptions.constants import (
    INCLUDED_AI_OPERATIONS_PER_MONTH,
    LOFT_SUBSCRIPTION_CURRENCY,
    SUBSCRIPTION_ACCESS_MESSAGES,
    SUBSCRIPTION_QUOTE_MAX_AGE_SECONDS,
    SUBSCRIPTION_QUOTE_SALT,
    SubscriptionErrorCode,
    TRIAL_AI_OPERATIONS_TOTAL,
)
from apps.loft_subscriptions.models import (
    AIQuotaPackage,
    LoftSubscription,
    LoftSubscriptionStatus,
    LoftSubscriptionTrialUsage,
    PedigreeTemplatePackage,
    SubscriptionAddon,
    SubscriptionAIUsage,
    SubscriptionDurationOption,
    SubscriptionInvoice,
    SubscriptionInvoiceLine,
    SubscriptionInvoiceStatus,
    SubscriptionPaymentRequest,
    SubscriptionPaymentRequestStatus,
)
from apps.users.constants import ProfileType


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


class SubscriptionConflict(APIException):
    status_code = 409
    default_code = "subscription_conflict"


class SubscriptionAccessDenied(APIException):
    status_code = 403
    default_code = "subscription_access_denied"

    def __init__(self, code):
        super().__init__(
            {
                "code": code,
                "detail": SUBSCRIPTION_ACCESS_MESSAGES.get(
                    code, _("Subscription access denied.")
                ),
            }
        )


class SubscriptionPriceChanged(Exception):
    def __init__(self, pricing):
        self.pricing = pricing


@dataclass(frozen=True)
class PricingLine:
    item_type: str
    code: str
    name: str
    amount: Decimal

    def as_dict(self):
        return {
            "item_type": self.item_type,
            "code": self.code,
            "name": self.name,
            "amount": f"{self.amount:.2f}",
        }


@dataclass(frozen=True)
class SubscriptionPricingResult:
    duration: SubscriptionDurationOption
    pedigree_package: PedigreeTemplatePackage
    addons: list[SubscriptionAddon]
    ai_package: AIQuotaPackage | None
    selected_template_ids: list[int]
    currency: str
    lines: list[PricingLine]
    subtotal: Decimal
    total: Decimal
    entitlement_snapshot: dict

    @property
    def selected_addon_ids(self):
        return [addon.pk for addon in self.addons]

    def selection_snapshot(self):
        return {
            "duration_id": self.duration.pk,
            "pedigree_package_id": self.pedigree_package.pk,
            "addon_ids": self.selected_addon_ids,
            "ai_package_id": self.ai_package.pk if self.ai_package else None,
            "template_ids": self.selected_template_ids,
        }

    def quote_payload(self):
        return {
            "selection": self.selection_snapshot(),
            "total": f"{self.total:.2f}",
            "currency": self.currency,
            "issued_at": timezone.now().isoformat(),
        }

    def pricing_snapshot(self):
        return {
            "currency": self.currency,
            "lines": [line.as_dict() for line in self.lines],
            "subtotal": f"{self.subtotal:.2f}",
            "total": f"{self.total:.2f}",
            "entitlement_snapshot": self.entitlement_snapshot,
        }


@dataclass(frozen=True)
class CheckoutResult:
    pricing: SubscriptionPricingResult
    invoice: SubscriptionInvoice | None = None
    payment_request: SubscriptionPaymentRequest | None = None
    subscription: LoftSubscription | None = None
    dry_run: bool = False
    quote_token: str | None = None
    quote_expires_at: object | None = None


class SubscriptionPricingService:
    @classmethod
    def calculate(cls, *, profile, validated_selection):
        cls._validate_owner_profile(profile)
        duration = cls._get_duration(validated_selection["duration_id"])
        pedigree_package = cls._get_pedigree_package(
            validated_selection["pedigree_package_id"]
        )
        addon_ids = list(validated_selection.get("addon_ids") or [])
        template_ids = list(validated_selection.get("template_ids") or [])
        ai_package_id = validated_selection.get("ai_package_id")

        cls._validate_unique_positive_ids("addon_ids", addon_ids)
        selected_template_ids = cls._validate_unique_positive_ids(
            "template_ids",
            template_ids,
        )
        addons = cls._get_addons(addon_ids)
        ai_package = cls._get_ai_package(ai_package_id)

        currencies = {
            duration.currency,
            pedigree_package.currency,
            *(addon.currency for addon in addons),
        }
        if ai_package:
            currencies.add(ai_package.currency)
        if currencies != {LOFT_SUBSCRIPTION_CURRENCY}:
            raise ValidationError(_("All subscription catalog prices must use EGP."))

        cls._validate_template_selection(
            duration=duration,
            pedigree_package=pedigree_package,
            template_ids=selected_template_ids,
        )
        cls._validate_trial_selection(
            profile=profile,
            duration=duration,
            pedigree_package=pedigree_package,
            addons=addons,
            ai_package=ai_package,
            template_ids=selected_template_ids,
        )

        lines = [
            PricingLine(
                "duration", duration.code, duration.name, money(duration.price)
            ),
            PricingLine(
                "pedigree_package",
                pedigree_package.code,
                pedigree_package.name,
                money(pedigree_package.price),
            ),
        ]
        lines.extend(
            PricingLine("addon", addon.code, addon.name, money(addon.price))
            for addon in addons
        )
        if ai_package:
            lines.append(
                PricingLine(
                    "ai_package",
                    ai_package.code,
                    ai_package.name,
                    money(ai_package.price),
                )
            )

        subtotal = money(sum((line.amount for line in lines), Decimal("0.00")))
        entitlement_snapshot = {
            "is_trial": duration.is_trial,
            "pedigree_template_limit": pedigree_package.template_limit,
            "pedigree_unlimited": pedigree_package.is_unlimited,
            "selected_template_ids": selected_template_ids,
            "addon_codes": [addon.code for addon in addons],
            "included_ai_operations_per_month": INCLUDED_AI_OPERATIONS_PER_MONTH,
            "purchased_ai_operations": ai_package.operation_count if ai_package else 0,
        }
        if duration.is_trial:
            entitlement_snapshot["included_ai_operations_total"] = (
                TRIAL_AI_OPERATIONS_TOTAL
            )
            entitlement_snapshot["included_ai_operations_per_month"] = 0

        return SubscriptionPricingResult(
            duration=duration,
            pedigree_package=pedigree_package,
            addons=addons,
            ai_package=ai_package,
            selected_template_ids=selected_template_ids,
            currency=LOFT_SUBSCRIPTION_CURRENCY,
            lines=lines,
            subtotal=subtotal,
            total=subtotal,
            entitlement_snapshot=entitlement_snapshot,
        )

    @staticmethod
    def _validate_owner_profile(profile):
        if profile.type not in {ProfileType.CUSTOMER, ProfileType.SELLER}:
            raise ValidationError(_("Only customer or seller profiles can subscribe."))

    @staticmethod
    def _get_duration(duration_id):
        try:
            return SubscriptionDurationOption.objects.get(
                pk=duration_id,
                is_active=True,
            )
        except SubscriptionDurationOption.DoesNotExist as exc:
            raise ValidationError(
                {"duration_id": _("Invalid subscription duration.")}
            ) from exc

    @staticmethod
    def _get_pedigree_package(package_id):
        try:
            return PedigreeTemplatePackage.objects.get(
                pk=package_id,
                is_active=True,
            )
        except PedigreeTemplatePackage.DoesNotExist as exc:
            raise ValidationError(
                {"pedigree_package_id": _("Invalid pedigree package.")}
            ) from exc

    @staticmethod
    def _get_addons(addon_ids):
        if not addon_ids:
            return []
        addons = list(
            SubscriptionAddon.objects.filter(
                pk__in=addon_ids,
                is_active=True,
                is_implemented=True,
            ).order_by("sort_order", "id")
        )
        if len(addons) != len(addon_ids):
            raise ValidationError({"addon_ids": _("Invalid subscription add-on.")})
        return sorted(addons, key=lambda addon: addon_ids.index(addon.pk))

    @staticmethod
    def _get_ai_package(ai_package_id):
        if ai_package_id is None:
            return None
        try:
            return AIQuotaPackage.objects.get(pk=ai_package_id, is_active=True)
        except AIQuotaPackage.DoesNotExist as exc:
            raise ValidationError(
                {"ai_package_id": _("Invalid AI quota package.")}
            ) from exc

    @staticmethod
    def _validate_unique_positive_ids(field, values):
        unique_values = []
        seen = set()
        for value in values:
            if value <= 0:
                raise ValidationError(
                    {field: _("Identifiers must be positive integers.")}
                )
            if value in seen:
                raise ValidationError(
                    {field: _("Duplicate identifiers are not allowed.")}
                )
            seen.add(value)
            unique_values.append(value)
        return unique_values

    @staticmethod
    def _validate_template_selection(*, duration, pedigree_package, template_ids):
        if duration.is_trial:
            if pedigree_package.code != "shapes_1" or len(template_ids) != 1:
                raise ValidationError(
                    {"template_ids": _("The trial requires exactly one template.")}
                )
            return

        if pedigree_package.is_unlimited:
            return

        limit = pedigree_package.template_limit or 0
        if pedigree_package.code == "shapes_1" and len(template_ids) != 1:
            raise ValidationError(
                {"template_ids": _("This package requires exactly one template.")}
            )
        if len(template_ids) > limit:
            raise ValidationError(
                {"template_ids": _("Selected templates exceed the package limit.")}
            )
        if not template_ids:
            raise ValidationError(
                {"template_ids": _("At least one template must be selected.")}
            )

    @staticmethod
    def _validate_trial_selection(
        *,
        profile,
        duration,
        pedigree_package,
        addons,
        ai_package,
        template_ids,
    ):
        if not duration.is_trial:
            return

        if LoftSubscriptionTrialUsage.objects.filter(user_id=profile.user_id).exists():
            raise ValidationError(
                {
                    "code": SubscriptionErrorCode.TRIAL_ALREADY_USED,
                    "detail": _(
                        "The free trial has already been used for this account."
                    ),
                }
            )
        if duration.price != Decimal("0.00") or pedigree_package.code != "shapes_1":
            raise ValidationError(_("Invalid trial catalog selection."))
        if addons:
            raise ValidationError(
                {"addon_ids": _("The trial cannot include paid add-ons.")}
            )
        if ai_package:
            raise ValidationError(
                {"ai_package_id": _("The trial cannot include paid AI quota.")}
            )
        if len(template_ids) != 1:
            raise ValidationError(
                {"template_ids": _("The trial requires exactly one template.")}
            )


class SubscriptionCheckoutService:
    @classmethod
    def execute(
        cls,
        *,
        profile,
        validated_selection,
        dry_run,
        quote_token=None,
        idempotency_key=None,
    ):
        pricing = SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection=validated_selection,
        )
        if dry_run:
            quote_token = signing.dumps(
                pricing.quote_payload(),
                salt=SUBSCRIPTION_QUOTE_SALT,
            )
            return CheckoutResult(
                pricing=pricing,
                dry_run=True,
                quote_token=quote_token,
                quote_expires_at=timezone.now()
                + timedelta(seconds=SUBSCRIPTION_QUOTE_MAX_AGE_SECONDS),
            )

        if not idempotency_key:
            raise ValidationError({"idempotency_key": _("This field is required.")})

        existing = (
            SubscriptionInvoice.objects.filter(idempotency_key=idempotency_key)
            .select_related("payment_request", "subscription")
            .first()
        )
        if existing is not None:
            if existing.owner_id != profile.pk:
                raise ValidationError(
                    {
                        "idempotency_key": _(
                            "This idempotency key belongs to another owner."
                        )
                    }
                )
            if existing.selection_snapshot != pricing.selection_snapshot():
                raise ValidationError(
                    {
                        "idempotency_key": _(
                            "This idempotency key was used for another selection."
                        )
                    }
                )
            return CheckoutResult(
                pricing=pricing,
                invoice=existing,
                payment_request=getattr(existing, "payment_request", None),
                subscription=getattr(existing, "subscription", None),
                dry_run=False,
            )

        cls._validate_quote_token(quote_token, pricing)

        with transaction.atomic():
            UserAccount.objects.select_for_update().get(pk=profile.user_id)
            pricing = SubscriptionPricingService.calculate(
                profile=profile,
                validated_selection=validated_selection,
            )
            cls._validate_quote_token(quote_token, pricing)

            try:
                invoice = cls._create_invoice(
                    profile=profile,
                    pricing=pricing,
                    idempotency_key=idempotency_key,
                )
            except IntegrityError as exc:
                raise ValidationError(
                    {"idempotency_key": _("This idempotency key is already used.")}
                ) from exc

            if pricing.duration.is_trial:
                subscription = SubscriptionActivationService.activate_trial_invoice(
                    invoice=invoice,
                    activated_at=timezone.now(),
                )
                return CheckoutResult(
                    pricing=pricing,
                    invoice=invoice,
                    subscription=subscription,
                    dry_run=False,
                )

            payment_request = SubscriptionPaymentRequest.objects.create(
                invoice=invoice,
                owner=profile,
                amount=invoice.total,
                status=SubscriptionPaymentRequestStatus.PENDING,
            )
            return CheckoutResult(
                pricing=pricing,
                invoice=invoice,
                payment_request=payment_request,
                dry_run=False,
            )

    @staticmethod
    def _validate_quote_token(quote_token, pricing):
        if not quote_token:
            raise ValidationError(
                {
                    "code": SubscriptionErrorCode.INVALID_SUBSCRIPTION_QUOTE,
                    "quote_token": _("This field is required."),
                }
            )
        try:
            payload = signing.loads(
                quote_token,
                salt=SUBSCRIPTION_QUOTE_SALT,
                max_age=SUBSCRIPTION_QUOTE_MAX_AGE_SECONDS,
            )
        except signing.SignatureExpired as exc:
            raise ValidationError(
                {
                    "code": SubscriptionErrorCode.INVALID_SUBSCRIPTION_QUOTE,
                    "quote_token": _("Subscription quote has expired."),
                }
            ) from exc
        except signing.BadSignature as exc:
            raise ValidationError(
                {
                    "code": SubscriptionErrorCode.INVALID_SUBSCRIPTION_QUOTE,
                    "quote_token": _("Invalid subscription quote."),
                }
            ) from exc

        expected = pricing.quote_payload()
        if (
            payload.get("selection") != expected["selection"]
            or payload.get("currency") != expected["currency"]
        ):
            raise ValidationError(
                {
                    "code": SubscriptionErrorCode.INVALID_SUBSCRIPTION_QUOTE,
                    "quote_token": _(
                        "Subscription quote does not match the checkout selection."
                    ),
                }
            )
        if payload.get("total") != expected["total"]:
            raise SubscriptionPriceChanged(pricing)

    @staticmethod
    def _create_invoice(*, profile, pricing, idempotency_key):
        expires_at = None
        if not pricing.duration.is_trial:
            expires_at = timezone.now() + timedelta(days=1)
        invoice = SubscriptionInvoice.objects.create(
            owner=profile,
            status=(
                SubscriptionInvoiceStatus.PAID
                if pricing.duration.is_trial
                else SubscriptionInvoiceStatus.PENDING
            ),
            currency=pricing.currency,
            subtotal=pricing.subtotal,
            total=pricing.total,
            duration=pricing.duration,
            pedigree_package=pricing.pedigree_package,
            ai_package=pricing.ai_package,
            selected_template_ids=pricing.selected_template_ids,
            addon_codes=pricing.entitlement_snapshot["addon_codes"],
            selection_snapshot=pricing.selection_snapshot(),
            pricing_snapshot=pricing.pricing_snapshot(),
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            paid_at=timezone.now() if pricing.duration.is_trial else None,
        )
        SubscriptionInvoiceLine.objects.bulk_create(
            [
                SubscriptionInvoiceLine(
                    invoice=invoice,
                    item_type=line.item_type,
                    item_code=line.code,
                    item_name=line.name,
                    quantity=1,
                    unit_amount=line.amount,
                    total_amount=line.amount,
                )
                for line in pricing.lines
            ]
        )
        return subscription_invoice_queryset().get(pk=invoice.pk)


class SubscriptionActivationService:
    @classmethod
    @transaction.atomic
    def activate_paid_invoice(cls, *, invoice_id, confirmed_by, confirmed_at=None):
        confirmed_at = confirmed_at or timezone.now()
        invoice = cls._get_locked_invoice(invoice_id)

        if invoice.total <= 0:
            raise ValidationError(
                _("Only paid subscription invoices use payment confirmation.")
            )

        payment_request = SubscriptionPaymentRequest.objects.select_for_update().get(
            invoice_id=invoice.pk
        )
        if payment_request.status == SubscriptionPaymentRequestStatus.APPROVED:
            return getattr(invoice, "subscription", None) or cls._create_subscription(
                invoice=invoice,
                starts_from=confirmed_at,
            )
        if payment_request.status != SubscriptionPaymentRequestStatus.PENDING:
            raise ValidationError(
                _("Only pending subscription payment requests can be confirmed.")
            )

        payment_request.status = SubscriptionPaymentRequestStatus.APPROVED
        payment_request.approved_by = confirmed_by
        payment_request.approved_at = confirmed_at
        payment_request.save(
            update_fields=["status", "approved_by", "approved_at", "modified"]
        )

        invoice.status = SubscriptionInvoiceStatus.PAID
        invoice.paid_at = confirmed_at
        invoice.save(update_fields=["status", "paid_at", "modified"])
        return cls._create_subscription(invoice=invoice, starts_from=confirmed_at)

    @classmethod
    @transaction.atomic
    def activate_trial_invoice(cls, *, invoice, activated_at):
        invoice = cls._get_locked_invoice(invoice.pk)
        user = UserAccount.objects.select_for_update().get(pk=invoice.owner.user_id)
        if LoftSubscriptionTrialUsage.objects.filter(user=user).exists():
            raise ValidationError(
                {
                    "code": SubscriptionErrorCode.TRIAL_ALREADY_USED,
                    "detail": _(
                        "The free trial has already been used for this account."
                    ),
                }
            )
        subscription = cls._create_subscription(
            invoice=invoice,
            starts_from=activated_at,
        )
        LoftSubscriptionTrialUsage.objects.create(
            user=user,
            invoice=invoice,
            subscription=subscription,
            consumed_at=activated_at,
        )
        return subscription

    @classmethod
    def _create_subscription(cls, *, invoice, starts_from):
        if hasattr(invoice, "subscription"):
            return invoice.subscription

        active_subscription = (
            LoftSubscription.objects.select_for_update()
            .filter(
                owner_id=invoice.owner_id,
                status=LoftSubscriptionStatus.ACTIVE,
                ends_at__gt=starts_from,
            )
            .order_by("-ends_at")
            .first()
        )
        starts_at = active_subscription.ends_at if active_subscription else starts_from
        status = (
            LoftSubscriptionStatus.SCHEDULED
            if active_subscription
            else LoftSubscriptionStatus.ACTIVE
        )
        ends_at = cls._calculate_ends_at(invoice=invoice, starts_at=starts_at)
        snapshot = invoice.pricing_snapshot.get("entitlement_snapshot") or {}
        subscription = LoftSubscription.objects.create(
            owner=invoice.owner,
            invoice=invoice,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            is_trial=bool(snapshot.get("is_trial")),
            pedigree_template_limit=snapshot.get("pedigree_template_limit"),
            pedigree_unlimited=bool(snapshot.get("pedigree_unlimited")),
            selected_template_ids=snapshot.get("selected_template_ids") or [],
            addon_codes=snapshot.get("addon_codes") or [],
            included_ai_operations_per_month=snapshot.get(
                "included_ai_operations_per_month",
                INCLUDED_AI_OPERATIONS_PER_MONTH,
            ),
            purchased_ai_operations=snapshot.get("purchased_ai_operations", 0),
        )
        invoice.activated_at = starts_from
        invoice.save(update_fields=["activated_at", "modified"])
        return subscription

    @staticmethod
    def _calculate_ends_at(*, invoice, starts_at):
        if invoice.duration.duration_days:
            return starts_at + timedelta(days=invoice.duration.duration_days)
        return starts_at + relativedelta(months=invoice.duration.duration_months)

    @staticmethod
    def _get_locked_invoice(invoice_id):
        try:
            return (
                SubscriptionInvoice.objects.select_for_update()
                .select_related("owner", "duration")
                .get(pk=invoice_id)
            )
        except SubscriptionInvoice.DoesNotExist as exc:
            raise NotFound(_("Subscription invoice was not found.")) from exc


class SubscriptionPaymentConfirmationService:
    @classmethod
    @transaction.atomic
    def confirm(cls, *, payment_request_id, confirmed_by):
        try:
            payment_request = (
                SubscriptionPaymentRequest.objects.select_for_update()
                .select_related("invoice")
                .get(pk=payment_request_id)
            )
        except SubscriptionPaymentRequest.DoesNotExist as exc:
            raise NotFound(_("Subscription payment request was not found.")) from exc
        subscription = SubscriptionActivationService.activate_paid_invoice(
            invoice_id=payment_request.invoice_id,
            confirmed_by=confirmed_by,
        )
        payment_request = subscription_payment_request_queryset().get(
            pk=payment_request.pk
        )
        return payment_request, subscription


class SubscriptionAccessService:
    @staticmethod
    def get_current_subscription(profile):
        now = timezone.now()
        subscription = (
            LoftSubscription.objects.filter(
                owner_id=profile.pk,
                status=LoftSubscriptionStatus.ACTIVE,
                starts_at__lte=now,
                ends_at__gt=now,
            )
            .select_related("invoice")
            .order_by("-starts_at")
            .first()
        )
        return subscription

    @classmethod
    def require_active_subscription(cls, profile):
        subscription = cls.get_current_subscription(profile)
        if subscription is None:
            raise SubscriptionAccessDenied(SubscriptionErrorCode.SUBSCRIPTION_REQUIRED)
        return subscription

    @classmethod
    def require_addon(cls, profile, addon_code):
        subscription = cls.require_active_subscription(profile)
        if addon_code not in (subscription.addon_codes or []):
            raise SubscriptionAccessDenied(
                SubscriptionErrorCode.SUBSCRIPTION_ADDON_REQUIRED
            )
        return subscription

    @classmethod
    def require_pedigree_template(cls, profile, pdf_id):
        subscription = cls.require_active_subscription(profile)
        if subscription.pedigree_unlimited:
            return subscription
        if pdf_id not in (subscription.selected_template_ids or []):
            raise SubscriptionAccessDenied(
                SubscriptionErrorCode.PEDIGREE_TEMPLATE_NOT_ALLOWED
            )
        return subscription

    @staticmethod
    def get_ai_balance(profile):
        return SubscriptionAIQuotaService.get_balance(profile)


class SubscriptionAIQuotaService:
    @classmethod
    def get_balance(cls, profile):
        subscription = SubscriptionAccessService.get_current_subscription(profile)
        if subscription is None:
            return {
                "included_remaining": 0,
                "purchased_remaining": 0,
                "period_start": None,
                "period_end": None,
            }
        return cls._balance_for_subscription(subscription)

    @classmethod
    @transaction.atomic
    def consume(cls, *, profile, operation_code, idempotency_key):
        subscription = SubscriptionAccessService.require_active_subscription(profile)
        subscription = LoftSubscription.objects.select_for_update().get(
            pk=subscription.pk
        )
        existing = SubscriptionAIUsage.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            if existing.owner_id != profile.pk:
                raise ValidationError(
                    {
                        "idempotency_key": _(
                            "This idempotency key belongs to another owner."
                        )
                    }
                )
            return cls._balance_for_subscription(subscription)

        balance = cls._balance_for_subscription(subscription)
        period_start = balance["period_start"]
        period_end = balance["period_end"]
        quota_type = None
        if balance["included_remaining"] > 0:
            quota_type = "included"
        elif balance["purchased_remaining"] > 0:
            quota_type = "purchased"
            period_start = None
            period_end = None
        else:
            raise SubscriptionAccessDenied(SubscriptionErrorCode.AI_QUOTA_EXHAUSTED)

        SubscriptionAIUsage.objects.create(
            subscription=subscription,
            owner=profile,
            operation_code=operation_code,
            idempotency_key=idempotency_key,
            quota_type=quota_type,
            allowance_period_start=period_start,
            allowance_period_end=period_end,
        )
        return cls._balance_for_subscription(subscription)

    @classmethod
    def _balance_for_subscription(cls, subscription):
        if subscription.is_trial:
            used = SubscriptionAIUsage.objects.filter(
                subscription=subscription,
                quota_type="included",
            ).count()
            return {
                "included_remaining": max(TRIAL_AI_OPERATIONS_TOTAL - used, 0),
                "purchased_remaining": 0,
                "period_start": subscription.starts_at,
                "period_end": subscription.ends_at,
            }

        period_start, period_end = cls._current_period(subscription)
        included_used = SubscriptionAIUsage.objects.filter(
            subscription=subscription,
            quota_type="included",
            allowance_period_start=period_start,
            allowance_period_end=period_end,
        ).count()
        purchased_used = SubscriptionAIUsage.objects.filter(
            subscription=subscription,
            quota_type="purchased",
        ).count()
        return {
            "included_remaining": max(
                subscription.included_ai_operations_per_month - included_used,
                0,
            ),
            "purchased_remaining": max(
                subscription.purchased_ai_operations - purchased_used,
                0,
            ),
            "period_start": period_start,
            "period_end": period_end,
        }

    @staticmethod
    def _current_period(subscription):
        now = timezone.now()
        period_start = subscription.starts_at
        while period_start + relativedelta(months=1) <= now:
            period_start = period_start + relativedelta(months=1)
        period_end = min(period_start + relativedelta(months=1), subscription.ends_at)
        return period_start, period_end


def subscription_invoice_queryset():
    return (
        SubscriptionInvoice.objects.select_related(
            "owner",
            "duration",
            "pedigree_package",
            "ai_package",
            "payment_request",
            "subscription",
        )
        .prefetch_related("lines")
        .order_by("-created")
    )


def subscription_payment_request_queryset():
    return SubscriptionPaymentRequest.objects.select_related(
        "owner",
        "invoice",
        "approved_by",
    ).order_by("-created")
