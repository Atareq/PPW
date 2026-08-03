from rest_framework import serializers

from apps.loft_subscriptions.constants import (
    INCLUDED_AI_OPERATIONS_PER_MONTH,
    LOFT_SUBSCRIPTION_CURRENCY,
)
from apps.loft_subscriptions.models import (
    AIQuotaPackage,
    LoftSubscription,
    PedigreeTemplatePackage,
    SubscriptionAddon,
    SubscriptionDurationOption,
    SubscriptionInvoice,
    SubscriptionInvoiceLine,
    SubscriptionPaymentRequest,
)
from apps.loft_subscriptions.services import SubscriptionAIQuotaService


class RejectUnknownFieldsMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {
                        field: ["This field is not supported."]
                        for field in sorted(unknown)
                    }
                )
        return super().to_internal_value(data)


class CatalogDurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionDurationOption
        fields = [
            "id",
            "code",
            "name",
            "duration_days",
            "duration_months",
            "price",
            "currency",
            "is_trial",
        ]
        read_only_fields = fields


class CatalogPedigreeTemplatePackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedigreeTemplatePackage
        fields = [
            "id",
            "code",
            "name",
            "template_limit",
            "is_unlimited",
            "price",
            "currency",
        ]
        read_only_fields = fields


class CatalogAddonSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionAddon
        fields = ["id", "code", "name", "description", "price", "currency"]
        read_only_fields = fields


class CatalogAIQuotaPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIQuotaPackage
        fields = ["id", "code", "name", "operation_count", "price", "currency"]
        read_only_fields = fields


class SubscriptionSelectionSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    duration_id = serializers.IntegerField(min_value=1)
    pedigree_package_id = serializers.IntegerField(min_value=1)
    addon_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
    )
    ai_package_id = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        default=None,
    )
    template_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
    )


class SubscriptionCheckoutSerializer(SubscriptionSelectionSerializer):
    quote_token = serializers.CharField()
    idempotency_key = serializers.CharField(max_length=255)


class PricingResponseSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField()
    currency = serializers.CharField()
    lines = serializers.ListField(child=serializers.DictField())
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2)
    total = serializers.DecimalField(max_digits=10, decimal_places=2)
    selected_template_ids = serializers.ListField(child=serializers.IntegerField())
    included_ai_operations_per_month = serializers.IntegerField()
    purchased_ai_operations = serializers.IntegerField()
    quote_token = serializers.CharField(required=False, allow_null=True)
    quote_expires_at = serializers.DateTimeField(required=False, allow_null=True)


class SubscriptionInvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionInvoiceLine
        fields = [
            "id",
            "item_type",
            "item_code",
            "item_name",
            "quantity",
            "unit_amount",
            "total_amount",
        ]
        read_only_fields = fields


class SubscriptionPaymentRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPaymentRequest
        fields = [
            "id",
            "amount",
            "status",
            "buyer_note",
            "payment_proof",
            "approved_at",
            "approved_by_id",
            "created",
            "modified",
        ]
        read_only_fields = fields


class SubscriptionInvoiceListSerializer(serializers.ModelSerializer):
    payment_request_id = serializers.SerializerMethodField()
    subscription_id = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionInvoice
        fields = [
            "id",
            "owner_id",
            "status",
            "currency",
            "subtotal",
            "total",
            "selected_template_ids",
            "addon_codes",
            "idempotency_key",
            "expires_at",
            "paid_at",
            "activated_at",
            "payment_request_id",
            "subscription_id",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def get_payment_request_id(self, obj):
        return getattr(getattr(obj, "payment_request", None), "pk", None)

    def get_subscription_id(self, obj):
        return getattr(getattr(obj, "subscription", None), "pk", None)


class SubscriptionInvoiceDetailSerializer(SubscriptionInvoiceListSerializer):
    lines = SubscriptionInvoiceLineSerializer(many=True, read_only=True)
    payment_request = SubscriptionPaymentRequestSerializer(read_only=True)

    class Meta(SubscriptionInvoiceListSerializer.Meta):
        fields = SubscriptionInvoiceListSerializer.Meta.fields + [
            "duration_id",
            "pedigree_package_id",
            "ai_package_id",
            "selection_snapshot",
            "pricing_snapshot",
            "lines",
            "payment_request",
        ]
        read_only_fields = fields


class LoftSubscriptionSerializer(serializers.ModelSerializer):
    included_ai_remaining = serializers.SerializerMethodField()
    purchased_ai_remaining = serializers.SerializerMethodField()
    remaining_seconds = serializers.SerializerMethodField()

    class Meta:
        model = LoftSubscription
        fields = [
            "id",
            "owner_id",
            "invoice_id",
            "status",
            "starts_at",
            "ends_at",
            "is_trial",
            "remaining_seconds",
            "pedigree_template_limit",
            "pedigree_unlimited",
            "selected_template_ids",
            "addon_codes",
            "included_ai_operations_per_month",
            "included_ai_remaining",
            "purchased_ai_operations",
            "purchased_ai_remaining",
            "created",
            "modified",
        ]
        read_only_fields = fields

    def _balance(self, obj):
        cached = getattr(obj, "_ai_balance", None)
        if cached is None:
            cached = SubscriptionAIQuotaService._balance_for_subscription(obj)
            obj._ai_balance = cached
        return cached

    def get_included_ai_remaining(self, obj):
        return self._balance(obj)["included_remaining"]

    def get_purchased_ai_remaining(self, obj):
        return self._balance(obj)["purchased_remaining"]

    def get_remaining_seconds(self, obj):
        from django.utils import timezone

        return max(int((obj.ends_at - timezone.now()).total_seconds()), 0)


class CheckoutResponseSerializer(serializers.Serializer):
    invoice = SubscriptionInvoiceDetailSerializer()
    payment_request = SubscriptionPaymentRequestSerializer(
        required=False, allow_null=True
    )
    subscription = LoftSubscriptionSerializer(required=False, allow_null=True)
    pricing = PricingResponseSerializer()


def pricing_response_data(result):
    pricing = result.pricing
    return {
        "dry_run": result.dry_run,
        "currency": pricing.currency,
        "lines": [line.as_dict() for line in pricing.lines],
        "subtotal": pricing.subtotal,
        "total": pricing.total,
        "selected_template_ids": pricing.selected_template_ids,
        "included_ai_operations_per_month": pricing.entitlement_snapshot.get(
            "included_ai_operations_per_month",
            INCLUDED_AI_OPERATIONS_PER_MONTH,
        ),
        "purchased_ai_operations": pricing.entitlement_snapshot.get(
            "purchased_ai_operations",
            0,
        ),
        "quote_token": result.quote_token,
        "quote_expires_at": result.quote_expires_at,
    }


def catalog_response_data():
    return {
        "currency": LOFT_SUBSCRIPTION_CURRENCY,
        "durations": CatalogDurationSerializer(
            SubscriptionDurationOption.objects.filter(is_active=True),
            many=True,
        ).data,
        "pedigree_packages": CatalogPedigreeTemplatePackageSerializer(
            PedigreeTemplatePackage.objects.filter(is_active=True),
            many=True,
        ).data,
        "addons": CatalogAddonSerializer(
            SubscriptionAddon.objects.filter(
                is_active=True,
                is_implemented=True,
            ),
            many=True,
        ).data,
        "ai_packages": CatalogAIQuotaPackageSerializer(
            AIQuotaPackage.objects.filter(is_active=True),
            many=True,
        ).data,
        "included_ai_operations_per_month": INCLUDED_AI_OPERATIONS_PER_MONTH,
    }
