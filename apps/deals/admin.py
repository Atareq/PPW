from django.contrib import admin

from apps.deals.models import CompletedDeal


@admin.register(CompletedDeal)
class CompletedDealAdmin(admin.ModelAdmin):
    """Read-only inspection; completed deals are immutable business history."""

    list_display = [
        "id",
        "deal_type",
        "source_type",
        "source_id",
        "buyer_user",
        "buyer_profile",
        "seller_user",
        "seller_profile",
        "amount",
        "currency",
        "completed_at",
    ]
    list_filter = ["deal_type", "currency", "completed_at"]
    search_fields = [
        "buyer_user__username",
        "seller_user__username",
        "source_id",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
