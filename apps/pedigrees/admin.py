from django.contrib import admin

from apps.pedigrees.models import PedigreeBird, PedigreeDocument


@admin.register(PedigreeBird)
class PedigreeBirdAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "owner",
        "ring_number",
        "sex",
        "life_status",
        "ownership_status",
        "activity_status",
        "job",
        "created",
    ]
    list_filter = [
        "sex",
        "life_status",
        "ownership_status",
        "activity_status",
        "job",
    ]
    search_fields = [
        "ring_number",
        "normalized_ring_number",
        "name",
        "ring_code",
    ]
    readonly_fields = [
        "public_id",
        "normalized_ring_number",
        "created",
        "modified",
    ]


@admin.register(PedigreeDocument)
class PedigreeDocumentAdmin(admin.ModelAdmin):
    list_display = ["id", "owner", "uploaded_by", "bird", "status", "created"]
    list_filter = ["status"]
    search_fields = ["extracted_bird_ring_number", "reviewed_bird_ring_number"]
