from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeCertificate,
    PedigreeTemplate,
)
from apps.pedigrees.models import PedigreeDocument
from apps.pedigrees.services import PedigreeBirdValidator, PedigreeCertificateService


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


class PedigreeBirdSerializer(serializers.ModelSerializer):
    owner = serializers.IntegerField(source="owner_id", read_only=True)
    father = serializers.PrimaryKeyRelatedField(
        queryset=PedigreeBird.objects.all(),
        required=False,
        allow_null=True,
    )
    mother = serializers.PrimaryKeyRelatedField(
        queryset=PedigreeBird.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = PedigreeBird
        fields = [
            "id",
            "owner",
            "public_id",
            "ring_number",
            "normalized_ring_number",
            "ring_code",
            "name",
            "description",
            "record_date",
            "birthday",
            "sex",
            "colour",
            "life_status",
            "ownership_status",
            "activity_status",
            "job",
            "external_owner_name",
            "father",
            "mother",
            "created",
            "modified",
        ]
        read_only_fields = [
            "owner",
            "public_id",
            "normalized_ring_number",
            "created",
            "modified",
        ]

    def validate(self, attrs):
        request = self.context.get("request")
        owner = request.user.active_profile
        return PedigreeBirdValidator.validate(
            attrs=attrs,
            owner=owner,
            instance=self.instance,
        )

    def create(self, validated_data):
        validated_data["owner"] = self.context["request"].user.active_profile
        return super().create(validated_data)


class PedigreeTemplateListSerializer(serializers.ModelSerializer):
    preview_image_url = serializers.SerializerMethodField()
    availability = serializers.SerializerMethodField()

    class Meta:
        model = PedigreeTemplate
        fields = [
            "id",
            "name",
            "generation",
            "preview_image_url",
            "is_premium",
            "availability",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_preview_image_url(self, obj):
        if not obj.preview_image:
            return ""
        request = self.context.get("request")
        if request and obj.preview_image.startswith("/"):
            return request.build_absolute_uri(obj.preview_image)
        return obj.preview_image

    @extend_schema_field(serializers.CharField())
    def get_availability(self, obj):
        return "available" if obj.is_configured else "unavailable"


class PedigreeCertificateTemplateSummarySerializer(serializers.ModelSerializer):
    preview_image_url = serializers.SerializerMethodField()

    class Meta:
        model = PedigreeTemplate
        fields = ["id", "name", "generation", "preview_image_url", "is_premium"]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_preview_image_url(self, obj):
        return PedigreeTemplateListSerializer(
            obj,
            context=self.context,
        ).data["preview_image_url"]


class PedigreeCertificateCreateSerializer(serializers.Serializer):
    subject_bird_id = serializers.PrimaryKeyRelatedField(
        source="subject_bird",
        queryset=PedigreeBird.objects.select_related("owner", "father", "mother"),
    )
    template_id = serializers.PrimaryKeyRelatedField(
        source="template",
        queryset=PedigreeTemplate.objects.all(),
    )
    owner = serializers.JSONField(required=False)
    customization = serializers.JSONField(required=False)

    def create(self, validated_data):
        request = self.context["request"]
        return PedigreeCertificateService.create_certificate(
            requested_by=request.user,
            owner=request.user.active_profile,
            subject_bird=validated_data["subject_bird"],
            template=validated_data["template"],
            owner_data=validated_data.get("owner") or {},
            customization=validated_data.get("customization") or {},
        )


class PedigreeCertificateCreateResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedigreeCertificate
        fields = ["id", "status", "generation_revision"]
        read_only_fields = fields


class PedigreeCertificateDetailSerializer(serializers.ModelSerializer):
    owner = serializers.IntegerField(source="owner_id", read_only=True)
    requested_by = serializers.IntegerField(source="requested_by_id", read_only=True)
    subject_bird_id = serializers.IntegerField(read_only=True)
    template = PedigreeCertificateTemplateSummarySerializer(read_only=True)
    generated_file_url = serializers.SerializerMethodField()

    class Meta:
        model = PedigreeCertificate
        fields = [
            "id",
            "owner",
            "requested_by",
            "status",
            "subject_bird_id",
            "template",
            "editable_data",
            "tree_snapshot",
            "external_request_payload",
            "generated_file_url",
            "mime_type",
            "generated_filename",
            "error_code",
            "error_message",
            "generation_revision",
            "generated_at",
            "created",
            "modified",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_generated_file_url(self, obj):
        if not obj.generated_file:
            return ""
        try:
            url = obj.generated_file.url
        except ValueError:
            return ""
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(url)
        return url


class PedigreeCertificateRegenerateSerializer(serializers.Serializer):
    template_id = serializers.PrimaryKeyRelatedField(
        source="template",
        queryset=PedigreeTemplate.objects.all(),
    )
    editable_data = serializers.JSONField()

    def create(self, validated_data):
        certificate = self.context["certificate"]
        request = self.context["request"]
        return PedigreeCertificateService.regenerate_certificate(
            certificate=certificate,
            template=validated_data["template"],
            editable_data=validated_data["editable_data"],
            actor_profile=request.user.active_profile,
        )
