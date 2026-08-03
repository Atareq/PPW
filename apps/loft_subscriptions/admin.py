from django.contrib import admin

from apps.loft_subscriptions.models import (
    AIQuotaPackage,
    LoftSubscription,
    LoftSubscriptionTrialUsage,
    PedigreeTemplatePackage,
    SubscriptionAddon,
    SubscriptionAIUsage,
    SubscriptionDurationOption,
    SubscriptionInvoice,
    SubscriptionInvoiceLine,
    SubscriptionPaymentRequest,
)


@admin.register(SubscriptionDurationOption)
class SubscriptionDurationOptionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "code",
        "name",
        "price",
        "is_trial",
        "is_active",
        "sort_order",
    ]
    list_filter = ["is_trial", "is_active", "currency"]
    search_fields = ["code", "name"]


@admin.register(PedigreeTemplatePackage)
class PedigreeTemplatePackageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "code",
        "name",
        "template_limit",
        "is_unlimited",
        "price",
        "is_active",
    ]
    list_filter = ["is_unlimited", "is_active", "currency"]
    search_fields = ["code", "name"]


@admin.register(SubscriptionAddon)
class SubscriptionAddonAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "code",
        "name",
        "price",
        "is_active",
        "is_implemented",
        "sort_order",
    ]
    list_filter = ["is_active", "is_implemented", "currency"]
    search_fields = ["code", "name"]


@admin.register(AIQuotaPackage)
class AIQuotaPackageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "code",
        "name",
        "operation_count",
        "price",
        "is_active",
        "sort_order",
    ]
    list_filter = ["is_active", "currency"]
    search_fields = ["code", "name"]


class SubscriptionInvoiceLineInline(admin.TabularInline):
    model = SubscriptionInvoiceLine
    extra = 0
    readonly_fields = [
        "item_type",
        "item_code",
        "item_name",
        "quantity",
        "unit_amount",
        "total_amount",
        "created",
        "modified",
    ]
    can_delete = False


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "owner",
        "status",
        "currency",
        "total",
        "paid_at",
        "activated_at",
        "created",
    ]
    list_filter = ["status", "currency"]
    search_fields = ["idempotency_key", "owner__user__username"]
    readonly_fields = [
        "selection_snapshot",
        "pricing_snapshot",
        "paid_at",
        "activated_at",
        "created",
        "modified",
    ]
    inlines = [SubscriptionInvoiceLineInline]


@admin.register(SubscriptionInvoiceLine)
class SubscriptionInvoiceLineAdmin(admin.ModelAdmin):
    list_display = ["id", "invoice", "item_type", "item_code", "total_amount"]
    list_filter = ["item_type"]
    search_fields = ["item_code", "item_name", "invoice__idempotency_key"]
    readonly_fields = ["created", "modified"]


@admin.register(LoftSubscription)
class LoftSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["id", "owner", "status", "is_trial", "starts_at", "ends_at"]
    list_filter = ["status", "is_trial", "pedigree_unlimited"]
    search_fields = ["owner__user__username", "invoice__idempotency_key"]
    readonly_fields = [
        "invoice",
        "is_trial",
        "pedigree_template_limit",
        "pedigree_unlimited",
        "selected_template_ids",
        "addon_codes",
        "included_ai_operations_per_month",
        "purchased_ai_operations",
        "created",
        "modified",
    ]


@admin.register(LoftSubscriptionTrialUsage)
class LoftSubscriptionTrialUsageAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "invoice", "subscription", "consumed_at"]
    search_fields = ["user__username", "invoice__idempotency_key"]
    readonly_fields = [
        "user",
        "invoice",
        "subscription",
        "consumed_at",
        "created",
        "modified",
    ]


@admin.register(SubscriptionPaymentRequest)
class SubscriptionPaymentRequestAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "invoice",
        "owner",
        "amount",
        "status",
        "approved_at",
        "created",
    ]
    list_filter = ["status"]
    search_fields = ["invoice__idempotency_key", "owner__user__username"]
    readonly_fields = ["amount", "approved_at", "approved_by", "created", "modified"]


@admin.register(SubscriptionAIUsage)
class SubscriptionAIUsageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "subscription",
        "owner",
        "operation_code",
        "quota_type",
        "created",
    ]
    list_filter = ["quota_type", "operation_code"]
    search_fields = ["idempotency_key", "owner__user__username"]
    readonly_fields = [
        "subscription",
        "owner",
        "operation_code",
        "idempotency_key",
        "quota_type",
        "allowance_period_start",
        "allowance_period_end",
        "created",
        "modified",
    ]
