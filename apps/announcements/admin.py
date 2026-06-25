from django.contrib import admin

from apps.announcements.models import Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "is_active", "created_by", "created"]
    list_filter = ["is_active", "created"]
    search_fields = ["title", "subtitle", "details"]
    readonly_fields = ["created", "modified"]
