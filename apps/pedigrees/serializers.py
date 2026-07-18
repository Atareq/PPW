from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.pedigrees.constants import (
    PEDIGREE_MAX_ANCESTOR_PATH_LENGTH,
    PEDIGREE_MAX_GENERATION_DEPTH,
)
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdActivityStatus,
    PedigreeBirdColour,
    PedigreeBirdJob,
    PedigreeBirdLifeStatus,
    PedigreeBirdOwnershipStatus,
    PedigreeBirdSex,
    PedigreeCertificate,
    PedigreeDocument,
    PedigreeTemplate,
)
from apps.pedigrees.services import (
    PedigreeBirdValidator,
    PedigreeCertificateService,
)


class RejectUnknownFieldsMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {
                        field: ["This field is not supported."]
                        for field in sorted(unknown)
                    }
                )
        return super().to_internal_value(data)


class PedigreeDocumentUploadSerializer(serializers.Serializer):
    original_file = serializers.FileField()
    bird_id = serializers.IntegerField(
        required=False,
        allow_null=True,
    )


class PedigreeReviewedBirdNodeSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    path = serializers.CharField(
        required=True,
        allow_blank=True,
        trim_whitespace=False,
        max_length=PEDIGREE_MAX_ANCESTOR_PATH_LENGTH,
    )
    box_id = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=100,
    )
    ring_number = serializers.CharField(
        required=True,
        max_length=100,
        allow_blank=False,
        trim_whitespace=True,
    )
    ring_code = serializers.CharField(
        required=False,
        max_length=50,
        allow_blank=True,
        trim_whitespace=True,
    )
    name = serializers.CharField(
        required=False,
        max_length=255,
        allow_blank=True,
        trim_whitespace=True,
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    record_date = serializers.DateField(required=False, allow_null=True)
    birthday = serializers.DateField(required=False, allow_null=True)
    sex = serializers.ChoiceField(
        required=False,
        choices=PedigreeBirdSex.choices,
    )
    colour = serializers.ChoiceField(
        required=False,
        choices=PedigreeBirdColour.choices,
    )
    life_status = serializers.ChoiceField(
        required=False,
        choices=PedigreeBirdLifeStatus.choices,
    )
    ownership_status = serializers.ChoiceField(
        required=False,
        choices=PedigreeBirdOwnershipStatus.choices,
    )
    activity_status = serializers.ChoiceField(
        required=False,
        choices=PedigreeBirdActivityStatus.choices,
    )
    job = serializers.ChoiceField(
        required=False,
        choices=PedigreeBirdJob.choices,
    )
    external_owner_name = serializers.CharField(
        required=False,
        max_length=255,
        allow_blank=True,
        trim_whitespace=True,
    )

    def validate_path(self, value):
        if any(character not in {"F", "M"} for character in value):
            raise serializers.ValidationError(
                "Path may contain only F and M characters."
            )
        if len(value) > PEDIGREE_MAX_ANCESTOR_PATH_LENGTH:
            raise serializers.ValidationError(
                "Path exceeds the supported pedigree generation depth."
            )
        return value


class PedigreeDocumentReviewSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    birds = PedigreeReviewedBirdNodeSerializer(many=True, allow_empty=False)

    def validate_birds(self, birds):
        paths = [bird["path"] for bird in birds]
        duplicate_paths = sorted(
            {path for path in paths if paths.count(path) > 1}
        )
        if duplicate_paths:
            duplicate_path_text = ", ".join(duplicate_paths)
            raise serializers.ValidationError(
                f"Duplicate pedigree paths are not allowed: {duplicate_path_text}"
            )
        if "" not in paths:
            raise serializers.ValidationError(
                "A subject bird with path '' is required."
            )

        path_set = set(paths)
        orphan_paths = sorted(
            path for path in path_set if path and path[:-1] not in path_set
        )
        if orphan_paths:
            raise serializers.ValidationError(
                f"Pedigree path prefixes are missing: {', '.join(orphan_paths)}"
            )
        return birds


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


class PedigreeDocumentReviewResponseSerializer(serializers.Serializer):
    document = PedigreeDocumentDetailSerializer(read_only=True)
    subject_bird_id = serializers.IntegerField(read_only=True)
    path_to_bird_id = serializers.DictField(
        child=serializers.IntegerField(),
        read_only=True,
    )
    created_count = serializers.IntegerField(read_only=True)
    reused_count = serializers.IntegerField(read_only=True)
    updated_count = serializers.IntegerField(read_only=True)


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


class PedigreeCertificateTemplateSummarySerializer(
    serializers.ModelSerializer
):
    preview_image_url = serializers.SerializerMethodField()

    class Meta:
        model = PedigreeTemplate
        fields = [
            "id",
            "name",
            "generation",
            "preview_image_url",
            "is_premium",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_preview_image_url(self, obj):
        return PedigreeTemplateListSerializer(
            obj,
            context=self.context,
        ).data["preview_image_url"]


class PedigreeCertificateCreateSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    subject_bird_id = serializers.PrimaryKeyRelatedField(
        source="subject_bird",
        queryset=PedigreeBird.objects.select_related(
            "owner", "father", "mother"
        ),
    )
    template_id = serializers.PrimaryKeyRelatedField(
        source="template",
        queryset=PedigreeTemplate.objects.all(),
    )
    generation_depth = serializers.IntegerField(
        min_value=1,
        max_value=PEDIGREE_MAX_GENERATION_DEPTH,
    )
    owner = serializers.JSONField(required=False)
    customization = serializers.JSONField(required=False)

    def validate_owner(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Owner data must be an object.")
        return value

    def validate_customization(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "Customization must be an object."
            )
        return value

    def validate(self, attrs):
        template = attrs["template"]
        generation_depth = attrs["generation_depth"]
        if template.generation and generation_depth > template.generation:
            raise serializers.ValidationError(
                {
                    "generation_depth": (
                        "Generation depth cannot exceed the selected template "
                        "generation."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        return PedigreeCertificateService.create_certificate(
            requested_by=request.user,
            owner=request.user.active_profile,
            subject_bird=validated_data["subject_bird"],
            template=validated_data["template"],
            generation_depth=validated_data["generation_depth"],
            owner_data=validated_data.get("owner") or {},
            customization=validated_data.get("customization") or {},
        )


class PedigreeCertificateCreateResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedigreeCertificate
        fields = ["id", "status", "generation_revision", "generation_depth"]
        read_only_fields = fields


class PedigreeCertificateDetailSerializer(serializers.ModelSerializer):
    owner = serializers.IntegerField(source="owner_id", read_only=True)
    requested_by = serializers.IntegerField(
        source="requested_by_id", read_only=True
    )
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
            "generation_depth",
            "owner_data",
            "customization",
            "external_request_payload",
            "generated_file_url",
            "mime_type",
            "generated_filename",
            "external_status_code",
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


class PedigreeCertificateRegenerateSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    template_id = serializers.PrimaryKeyRelatedField(
        source="template",
        queryset=PedigreeTemplate.objects.all(),
        required=False,
    )
    generation_depth = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=PEDIGREE_MAX_GENERATION_DEPTH,
    )
    owner = serializers.JSONField(required=False)
    customization = serializers.JSONField(required=False)

    def validate_owner(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Owner data must be an object.")
        return value

    def validate_customization(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "Customization must be an object."
            )
        return value

    def validate(self, attrs):
        certificate = self.context["certificate"]
        template = attrs.get("template") or certificate.template
        generation_depth = attrs.get(
            "generation_depth", certificate.generation_depth
        )
        if template.generation and generation_depth > template.generation:
            raise serializers.ValidationError(
                {
                    "generation_depth": (
                        "Generation depth cannot exceed the selected template "
                        "generation."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        certificate = self.context["certificate"]
        request = self.context["request"]
        return PedigreeCertificateService.regenerate_certificate(
            certificate=certificate,
            template=validated_data.get("template"),
            generation_depth=validated_data.get("generation_depth"),
            owner_data=validated_data.get("owner"),
            customization=validated_data.get("customization"),
            actor_profile=request.user.active_profile,
        )
