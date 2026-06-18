from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.pedigrees.models import PedigreeDocument


class PedigreeDocumentUploadSerializer(serializers.Serializer):
    original_file = serializers.FileField()
    bird_id = serializers.IntegerField(
        required=False,
        allow_null=True,
    )


class PedigreeDocumentReviewSerializer(serializers.Serializer):
    bird_ring_number = serializers.CharField(
        max_length=100,
        allow_blank=False,
        trim_whitespace=True,
    )
    description = serializers.CharField(
        allow_blank=True,
        trim_whitespace=True,
    )


class PedigreeDocumentDetailSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    uploaded_by = serializers.IntegerField(
        source="uploaded_by_id",
        read_only=True,
    )
    owner = serializers.IntegerField(source="owner_id", read_only=True)
    bird_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = PedigreeDocument
        fields = [
            "id",
            "file_url",
            "uploaded_by",
            "owner",
            "bird_id",
            "status",
            "raw_ocr_text",
            "extracted_boxes",
            "extracted_bird_ring_number",
            "extracted_description",
            "reviewed_bird_ring_number",
            "reviewed_description",
            "processing_error",
            "reviewed_at",
            "created",
            "modified",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_file_url(self, obj):
        if not obj.original_file:
            return ""
        try:
            url = obj.original_file.url
        except ValueError:
            return ""

        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(url)
        return url
