from django.contrib import admin

from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeCertificate,
    PedigreeDocument,
    PedigreeTemplate,
)


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
    search_fields = ["ring_number", "normalized_ring_number", "name", "ring_code"]
    readonly_fields = ["public_id", "normalized_ring_number", "created", "modified"]


@admin.register(PedigreeTemplate)
class PedigreeTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "external_pdf_id",
        "pedigree_type",
        "report_name",
        "generation",
        "is_active",
        "is_premium",
        "sort_order",
    ]
    list_filter = ["is_active", "is_premium", "generation", "external_pdf_id"]
    search_fields = ["name", "report_name", "pedigree_type"]


@admin.register(PedigreeCertificate)
class PedigreeCertificateAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "owner",
        "subject_bird",
        "template",
        "status",
        "generation_revision",
        "generated_at",
        "error_code",
        "created",
    ]
    list_filter = ["status", "template", "generated_at"]
    search_fields = [
        "generated_filename",
        "error_code",
        "subject_bird__ring_number",
        "template__name",
    ]
    readonly_fields = [
        "tree_snapshot",
        "external_request_payload",
        "created",
        "modified",
    ]


@admin.register(PedigreeDocument)
class PedigreeDocumentAdmin(admin.ModelAdmin):
    list_display = ["id", "owner", "uploaded_by", "bird", "status", "created"]
    list_filter = ["status"]
    search_fields = ["extracted_bird_ring_number", "reviewed_bird_ring_number"]
