from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "recipient_profile",
        "actor_profile",
        "notification_type",
        "is_read",
        "created",
    ]
    list_filter = [
        "notification_type",
        "is_read",
        "created",
    ]
    search_fields = [
        "recipient_profile__id",
        "recipient_profile__user__username",
        "actor_profile__id",
        "actor_profile__user__username",
        "title",
        "body",
        "idempotency_key",
        "object_type",
    ]
    readonly_fields = [
        "created",
        "modified",
        "read_at",
    ]

