from django.contrib import admin

from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet


@admin.register(PointWallet)
class PointWalletAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "balance", "created", "modified"]
    search_fields = ["user__username", "user__phone_number"]
    readonly_fields = ["created", "modified"]


@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "profile",
        "transaction_type",
        "points",
        "balance_after",
        "source_type",
        "source_id",
        "created",
    ]
    list_filter = ["transaction_type", "source_type", "created"]
    search_fields = [
        "user__username",
        "profile__id",
        "reason",
        "source_id",
        "idempotency_key",
    ]
    readonly_fields = ["created", "modified"]


@admin.register(BadgeAward)
class BadgeAwardAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "profile",
        "badge_type",
        "awarded_at",
        "expires_at",
        "is_active",
        "source_type",
        "source_id",
        "idempotency_key",
    ]
    list_filter = ["badge_type", "expires_at", "source_type", "awarded_at"]
    search_fields = [
        "profile__id",
        "profile__user__username",
        "source_id",
        "idempotency_key",
    ]
    readonly_fields = ["created", "modified", "is_active"]
