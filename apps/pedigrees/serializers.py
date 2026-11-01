from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.pedigrees.constants import (
    PEDIGREE_MAX_ANCESTOR_PATH_LENGTH,
    PEDIGREE_MAX_GENERATION_DEPTH,
    PEDIGREE_REPORT_COLOR_PATTERN,
)
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdActivityStatus,
    PedigreeBirdColour,
    PedigreeBirdJob,
    PedigreeBirdLifeStatus,
    PedigreeBirdOwnershipStatus,
    PedigreeBirdSex,
    PedigreeDocument,
    RegisteredPedigree,
)
from apps.pedigrees.services import (
    PedigreeBirdOwnershipService,
    PedigreeBirdValidator,
)
from apps.users.constants import ProfileType
from apps.users.models import Profile


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


class PedigreeSpreadsheetImportSerializer(
    RejectUnknownFieldsMixin, serializers.Serializer
):
    file = serializers.FileField()
    user = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.all())

    def validate_file(self, value):
        if not value.name.lower().endswith(".xlsx"):
            raise serializers.ValidationError("A valid XLSX file is required.")
        return value


class PedigreeSpreadsheetImportResponseSerializer(serializers.Serializer):
    rows_processed = serializers.IntegerField(read_only=True)
    birds_created = serializers.IntegerField(read_only=True)
    birds_reused = serializers.IntegerField(read_only=True)
    relationships_created = serializers.IntegerField(read_only=True)
    relationships_unchanged = serializers.IntegerField(read_only=True)
    ignored_legacy_values = serializers.IntegerField(read_only=True)


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
        duplicate_paths = sorted({path for path in paths if paths.count(path) > 1})
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
    subject_bird_id = serializers.IntegerField(read_only=True)
    document = PedigreeDocumentDetailSerializer(read_only=True)
    path_to_bird_id = serializers.DictField(
        child=serializers.IntegerField(),
        read_only=True,
    )
    created_count = serializers.IntegerField(read_only=True)
    reused_count = serializers.IntegerField(read_only=True)
    updated_count = serializers.IntegerField(read_only=True)


class PedigreeBirdSerializer(serializers.ModelSerializer):
    """`registered_by` is server-assigned; `owner` is optional ownership.

    Parent and owner choices are scoped in `get_fields()`, so another
    registrant's bird is indistinguishable from a missing one.
    """

    owner = serializers.PrimaryKeyRelatedField(
        queryset=Profile.objects.none(),
        required=False,
        allow_null=True,
    )
    registered_by = serializers.IntegerField(
        source="registered_by_id", read_only=True
    )
    father = serializers.PrimaryKeyRelatedField(
        queryset=PedigreeBird.objects.none(),
        required=False,
        allow_null=True,
    )
    mother = serializers.PrimaryKeyRelatedField(
        queryset=PedigreeBird.objects.none(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = PedigreeBird
        fields = [
            "id",
            "owner",
            "registered_by",
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
            "default_color",
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
            "registered_by",
            "default_color",
            "public_id",
            "normalized_ring_number",
            "created",
            "modified",
        ]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return fields

        registration_scope = self._registration_scope()
        parents = PedigreeBird.objects.filter(registered_by=registration_scope)
        fields["father"].queryset = parents
        fields["mother"].queryset = parents

        active_profile = request.user.active_profile
        if active_profile.type == ProfileType.MANAGER:
            fields["owner"].queryset = Profile.objects.filter(
                type__in=PedigreeBirdOwnershipService.OWNER_PROFILE_TYPES
            )
        else:
            fields["owner"].queryset = Profile.objects.filter(pk=active_profile.pk)
        return fields

    def _registration_scope(self):
        if isinstance(self.instance, PedigreeBird):
            return self.instance.registered_by
        return self.context["request"].user.active_profile

    def validate(self, attrs):
        if self.instance is None:
            if "ownership_status" not in attrs:
                attrs["ownership_status"] = (
                    PedigreeBirdOwnershipStatus.OWNED
                    if attrs.get("owner")
                    else PedigreeBirdOwnershipStatus.UNKNOWN
                )
        elif "owner" in attrs:
            new_owner = attrs["owner"]
            PedigreeBirdOwnershipService.validate_owner_change(
                actor_profile=self.context["request"].user.active_profile,
                bird=self.instance,
                new_owner=new_owner,
            )
            new_owner_id = new_owner.pk if new_owner else None
            if (
                new_owner_id != self.instance.owner_id
                and "ownership_status" not in attrs
            ):
                attrs["ownership_status"] = (
                    PedigreeBirdOwnershipService.status_for_owner(
                        new_owner,
                        self.instance.ownership_status,
                    )
                )

        return PedigreeBirdValidator.validate(
            attrs=attrs,
            registered_by=self._registration_scope(),
            instance=self.instance,
        )

    def create(self, validated_data):
        validated_data["registered_by"] = self._registration_scope()
        return super().create(validated_data)


class PedigreeBirdOwnershipSerializer(serializers.ModelSerializer):
    """Ownership summary returned by claim/release; no pedigree data."""

    owner = serializers.IntegerField(
        source="owner_id", read_only=True, allow_null=True
    )
    registered_by = serializers.IntegerField(
        source="registered_by_id", read_only=True
    )

    class Meta:
        model = PedigreeBird
        fields = ["id", "public_id", "owner", "registered_by", "ownership_status"]
        read_only_fields = fields


from django.utils.translation import gettext_lazy as _


class RegisteredPedigreeSerializer(serializers.ModelSerializer):
    """User-facing view; never exposes other accounts' assignments."""

    access_type = serializers.SerializerMethodField()

    class Meta:
        model = RegisteredPedigree
        fields = ["id", "pedigree_id", "is_public", "access_type"]
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=["public", "custom"]))
    def get_access_type(self, obj) -> str:
        return "public" if obj.is_public else "custom"


class RegisteredPedigreeAdminSerializer(serializers.ModelSerializer):
    """Manager CRUD; `user_ids` is the full desired private-access set."""

    access_type = serializers.SerializerMethodField()
    user_ids = serializers.PrimaryKeyRelatedField(
        source="users",
        many=True,
        queryset=get_user_model().objects.all(),
        required=False,
    )

    class Meta:
        model = RegisteredPedigree
        fields = [
            "id",
            "pedigree_id",
            "is_public",
            "access_type",
            "user_ids",
            "created",
            "modified",
        ]
        read_only_fields = ["id", "access_type", "created", "modified"]

    @extend_schema_field(serializers.ChoiceField(choices=["public", "custom"]))
    def get_access_type(self, obj) -> str:
        return "public" if obj.is_public else "custom"

    @transaction.atomic
    def create(self, validated_data):
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        # ModelSerializer only calls users.set() when user_ids was sent.
        return super().update(instance, validated_data)


class PedigreeReportGenerateSerializer(serializers.Serializer):
    subject_bird_id = serializers.IntegerField()
    pdf_id = serializers.IntegerField(min_value=1, required=False)
    pedigree_id = serializers.IntegerField(min_value=1, required=False)
    generation_depth = serializers.IntegerField(
        min_value=1,
        max_value=PEDIGREE_MAX_GENERATION_DEPTH,
    )
    report = serializers.DictField()
    owner = serializers.DictField(required=False)
    customization = serializers.DictField(required=False)
    color = serializers.RegexField(
        PEDIGREE_REPORT_COLOR_PATTERN,
        required=False,
        allow_null=True,
        help_text=(
            "Report colour `#RRGGBB`. Omitted or null uses the subject's "
            "`default_color`, then the backend fallback; an explicit value is "
            "saved as `default_color` after a successful generation."
        ),
    )

    def validate_color(self, value):
        return value.upper() if value else None

    def validate(self, attrs):
        pdf_id = attrs.get("pdf_id") or attrs.get("pedigree_id")
        if not pdf_id:
            raise serializers.ValidationError({"pdf_id": _("This field is required.")})
        if (
            attrs.get("pdf_id")
            and attrs.get("pedigree_id")
            and attrs["pdf_id"] != attrs["pedigree_id"]
        ):
            raise serializers.ValidationError(
                {"pedigree_id": _("pedigree_id must match pdf_id.")}
            )
        attrs["pdf_id"] = pdf_id
        return attrs

    def validate_report(self, value):
        parameters = value.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise serializers.ValidationError(
                "Report parameters must be an object when provided."
            )
        return value


class PedigreeReportMetadataQuerySerializer(serializers.Serializer):
    generation = serializers.IntegerField(
        min_value=2,
        max_value=PEDIGREE_MAX_GENERATION_DEPTH,
    )
