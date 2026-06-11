from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.complaints.models import Complaint, ComplaintType


class ComplaintCreateSerializer(serializers.Serializer):
    payment_request_id = serializers.IntegerField()
    complaint_type = serializers.ChoiceField(
        choices=ComplaintType.choices,
        required=False,
    )
    title = serializers.CharField(
        max_length=255,
        allow_blank=False,
        trim_whitespace=True,
    )
    description = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
    )


class ComplaintAdminActionSerializer(serializers.Serializer):
    admin_note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )


class ComplaintListSerializer(serializers.ModelSerializer):
    payment_request_id = serializers.IntegerField(read_only=True)
    created_by = serializers.SerializerMethodField()
    buyer = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()

    class Meta:
        model = Complaint
        fields = [
            "id",
            "payment_request_id",
            "complaint_type",
            "status",
            "title",
            "created_by",
            "buyer",
            "seller",
            "created",
            "modified",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField())
    def get_created_by(self, obj):
        return self._profile_summary(obj.created_by)

    @extend_schema_field(serializers.DictField())
    def get_buyer(self, obj):
        return self._profile_summary(obj.buyer)

    @extend_schema_field(serializers.DictField())
    def get_seller(self, obj):
        return self._profile_summary(obj.seller)

    def _profile_summary(self, profile):
        return {
            "id": profile.pk,
            "type": profile.type,
        }


class ComplaintDetailSerializer(ComplaintListSerializer):
    reviewed_by = serializers.SerializerMethodField()

    class Meta(ComplaintListSerializer.Meta):
        fields = ComplaintListSerializer.Meta.fields + [
            "description",
            "admin_note",
            "reviewed_by",
            "reviewed_at",
            "resolved_at",
            "cancelled_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_reviewed_by(self, obj):
        return self._profile_summary(obj.reviewed_by) if obj.reviewed_by_id else None
