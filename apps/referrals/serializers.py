from rest_framework import serializers

from apps.referrals.models import LinkSharingCode
from apps.referrals.services import ReferralService


class LinkSharingCodeSerializer(serializers.ModelSerializer):
    owner_profile_id = serializers.IntegerField(read_only=True)
    used_by_profile_id = serializers.IntegerField(read_only=True, allow_null=True)
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = LinkSharingCode
        fields = [
            "id",
            "owner_profile_id",
            "code",
            "share_url",
            "expires_at",
            "is_used",
            "used_by_profile_id",
            "used_at",
            "created",
        ]
        read_only_fields = fields

    def get_share_url(self, obj):
        return ReferralService.share_url(obj)


class ReferralRedeemSerializer(serializers.Serializer):
    code = serializers.CharField()
