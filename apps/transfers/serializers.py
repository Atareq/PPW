from rest_framework import serializers

from apps.transfers.models import OwnershipTransfer
from apps.transfers.services import OwnershipTransferService
from apps.users.constants import ProfileType


class _ProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    phone = serializers.SerializerMethodField()
    nickname = serializers.SerializerMethodField()
    type = serializers.CharField()

    def get_phone(self, profile):
        return str(profile.user.phone_number)

    def get_nickname(self, profile):
        if profile.type == ProfileType.SELLER:
            sp = getattr(profile, "sellerprofile", None)
            return sp.nickname if sp else None
        return None


class OwnershipTransferSerializer(serializers.ModelSerializer):
    asset_title = serializers.CharField(source="asset.title", read_only=True)
    from_profile = _ProfileSerializer(read_only=True)
    to_profile = _ProfileSerializer(read_only=True)

    class Meta:
        model = OwnershipTransfer
        fields = [
            "id",
            "asset_id",
            "asset_title",
            "from_profile",
            "to_profile",
            "transfer_type",
            "note",
            "created",
        ]
        read_only_fields = fields


class CreateOwnershipTransferSerializer(serializers.Serializer):
    asset_id = serializers.IntegerField()
    to_profile_id = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def create(self, validated_data):
        from_profile = self.context["request"].user.active_profile
        return OwnershipTransferService.create_direct(
            asset_id=validated_data["asset_id"],
            from_profile=from_profile,
            to_profile_id=validated_data["to_profile_id"],
            note=validated_data.get("note", ""),
        )
