from django.contrib import admin

from apps.prizes.models import PrizeAward, PrizeClaim


@admin.register(PrizeAward)
class PrizeAwardAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "profile",
        "source_type",
        "source_id",
        "fulfillment_type",
        "status",
        "title",
        "awarded_at",
        "fulfilled_at",
    )
    list_filter = ("source_type", "fulfillment_type", "status")
    search_fields = ("title", "source_id", "idempotency_key", "user__username")
    readonly_fields = (
        "user",
        "profile",
        "source_type",
        "source_id",
        "fulfillment_type",
        "status",
        "title",
        "description",
        "prize_snapshot",
        "idempotency_key",
        "awarded_at",
        "fulfilled_at",
        "metadata",
        "created",
        "modified",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrizeClaim)
class PrizeClaimAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "award",
        "full_name",
        "status",
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
        "delivered_at",
        "delivered_by",
    )
    list_filter = ("status", "award__source_type")
    search_fields = (
        "full_name",
        "identity_number",
        "award__title",
        "award__user__username",
    )
    readonly_fields = (
        "award",
        "full_name",
        "address",
        "identity_number",
        "status",
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
        "delivered_at",
        "delivered_by",
        "created",
        "modified",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


