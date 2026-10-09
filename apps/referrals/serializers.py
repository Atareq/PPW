from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.referrals.models import (
    LinkSharingCode,
    Referral,
    ReferralChallenge,
    ReferralChallengePrize,
    ReferralChallengeStatus,
    ReferralChallengeWinner,
    ReferralStatus,
)
from apps.referrals.services import (
    REFERRAL_CHALLENGE_WINNER_COUNT,
    ReferralChallengeService,
    ReferralService,
)


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


class ReferralStatusSerializer(serializers.ModelSerializer):
    """User-facing referral state; no reviewer details."""

    referred_profile_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Referral
        fields = ["id", "status", "referred_profile_id", "redeemed_at"]
        read_only_fields = fields


class ReferralRedeemResponseSerializer(LinkSharingCodeSerializer):
    referral = serializers.SerializerMethodField()

    class Meta(LinkSharingCodeSerializer.Meta):
        fields = LinkSharingCodeSerializer.Meta.fields + ["referral"]
        read_only_fields = fields

    def get_referral(self, obj):
        return ReferralStatusSerializer(obj.referral).data


class AdminReferralSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="code.code", read_only=True)
    inviter_user_id = serializers.IntegerField(read_only=True)
    inviter_profile_id = serializers.IntegerField(read_only=True)
    inviter_username = serializers.CharField(
        source="inviter_user.username",
        read_only=True,
    )
    referred_user_id = serializers.IntegerField(read_only=True)
    referred_profile_id = serializers.IntegerField(read_only=True)
    referred_username = serializers.CharField(
        source="referred_user.username",
        read_only=True,
    )
    reviewed_by_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Referral
        fields = [
            "id",
            "code",
            "inviter_user_id",
            "inviter_profile_id",
            "inviter_username",
            "referred_user_id",
            "referred_profile_id",
            "referred_username",
            "status",
            "redeemed_at",
            "reviewed_at",
            "reviewed_by_id",
            "rejection_reason",
        ]
        read_only_fields = fields


class ReferralRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


# --- Monthly referral challenge ------------------------------------------------


class ReferralChallengePrizeSerializer(serializers.ModelSerializer):
    """Public prize definition: rank, generic code, and title only."""

    class Meta:
        model = ReferralChallengePrize
        fields = ["rank", "prize_code", "title"]
        read_only_fields = fields


class ReferralChallengeLeaderboardRowSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    username = serializers.CharField()
    challenge_score = serializers.IntegerField()
    is_me = serializers.BooleanField()


class ReferralChallengeProgressSerializer(serializers.Serializer):
    challenge_score = serializers.IntegerField()
    rank = serializers.IntegerField(allow_null=True)
    leader_score = serializers.IntegerField()
    gap_to_first = serializers.IntegerField()


class CurrentReferralChallengeSerializer(serializers.Serializer):
    """Current challenge with Top 10, countdown, and the caller's progress.

    ``challenge_score`` counts approved referrals; it is not PP.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=ReferralChallengeStatus.choices)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    server_time = serializers.DateTimeField()
    seconds_remaining = serializers.IntegerField()
    seconds_until_start = serializers.IntegerField()
    top_10 = ReferralChallengeLeaderboardRowSerializer(many=True)
    me = ReferralChallengeProgressSerializer()
    prizes = ReferralChallengePrizeSerializer(many=True)


class AdminReferralChallengePrizeSerializer(serializers.ModelSerializer):
    rank = serializers.IntegerField(
        min_value=1,
        max_value=REFERRAL_CHALLENGE_WINNER_COUNT,
    )

    class Meta:
        model = ReferralChallengePrize
        fields = ["rank", "prize_code", "title", "metadata"]


class AdminReferralChallengeWinnerSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = ReferralChallengeWinner
        fields = [
            "rank",
            "user_id",
            "username",
            "score",
            "score_achieved_at",
            "prize_code",
            "prize_title",
            "created",
        ]
        read_only_fields = fields


class AdminReferralChallengeLeaderboardRowSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    challenge_score = serializers.IntegerField()
    score_achieved_at = serializers.DateTimeField()


class AdminReferralChallengeSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    created_by_id = serializers.IntegerField(read_only=True, allow_null=True)
    finalized_by_id = serializers.IntegerField(read_only=True, allow_null=True)
    prizes = AdminReferralChallengePrizeSerializer(many=True, read_only=True)
    winners = AdminReferralChallengeWinnerSerializer(many=True, read_only=True)

    class Meta:
        model = ReferralChallenge
        fields = [
            "id",
            "name",
            "status",
            "starts_at",
            "ends_at",
            "finalized_at",
            "finalized_by_id",
            "created_by_id",
            "created",
            "modified",
            "prizes",
            "winners",
        ]
        read_only_fields = fields

    @extend_schema_field(
        serializers.ChoiceField(choices=ReferralChallengeStatus.choices)
    )
    def get_status(self, obj):
        return obj.status_at(timezone.now())


class AdminReferralChallengeDetailSerializer(AdminReferralChallengeSerializer):
    """Adds the live leaderboard (until finalized) and the review backlog."""

    leaderboard = serializers.SerializerMethodField()
    pending_referrals_count = serializers.SerializerMethodField()

    class Meta(AdminReferralChallengeSerializer.Meta):
        fields = AdminReferralChallengeSerializer.Meta.fields + [
            "leaderboard",
            "pending_referrals_count",
        ]
        read_only_fields = fields

    @extend_schema_field(AdminReferralChallengeLeaderboardRowSerializer(many=True))
    def get_leaderboard(self, obj):
        if obj.finalized_at is not None:
            return None
        return AdminReferralChallengeLeaderboardRowSerializer(
            ReferralChallengeService.leaderboard(obj),
            many=True,
        ).data

    @extend_schema_field(serializers.IntegerField())
    def get_pending_referrals_count(self, obj):
        return Referral.objects.filter(
            status=ReferralStatus.PENDING,
            redeemed_at__gte=obj.starts_at,
            redeemed_at__lt=obj.ends_at,
        ).count()


class AdminReferralChallengeWriteSerializer(serializers.Serializer):
    """Create or edit a challenge; supplied ``prizes`` replace the whole set."""

    name = serializers.CharField(max_length=255)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    prizes = AdminReferralChallengePrizeSerializer(many=True, required=False)

    def validate_prizes(self, value):
        ranks = [prize["rank"] for prize in value]
        if len(ranks) != len(set(ranks)):
            raise serializers.ValidationError(_("Each prize rank can be used once."))
        return value

    def validate(self, attrs):
        starts_at = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends_at = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts_at and ends_at and starts_at >= ends_at:
            raise serializers.ValidationError(
                {"ends_at": _("The challenge must end after it starts.")}
            )
        return attrs

    def to_representation(self, instance):
        return AdminReferralChallengeDetailSerializer(
            instance,
            context=self.context,
        ).data
