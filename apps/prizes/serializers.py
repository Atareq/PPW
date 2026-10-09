from rest_framework import serializers

from apps.prizes.models import PrizeAward, PrizeClaim


def _get_award_claim(award: PrizeAward) -> PrizeClaim | None:
    try:
        claim = award.claim
    except PrizeClaim.DoesNotExist:
        return None
    if getattr(claim, "deleted", None) is not None:
        return None
    return claim


class MyPrizeAwardSerializer(serializers.ModelSerializer):
    """Public/user-facing read serializer for the authenticated user's prize awards.

    Hides `idempotency_key`, internal `metadata`, recipient foreign keys, and claim PII
    (`identity_number`, `address`, `full_name`), exposing only safe claim status/timestamps.
    """

    claim_status = serializers.SerializerMethodField()
    claim_submitted_at = serializers.SerializerMethodField()
    claim_reviewed_at = serializers.SerializerMethodField()
    claim_delivered_at = serializers.SerializerMethodField()

    class Meta:
        model = PrizeAward
        fields = [
            "id",
            "source_type",
            "source_id",
            "fulfillment_type",
            "status",
            "title",
            "description",
            "prize_snapshot",
            "awarded_at",
            "fulfilled_at",
            "claim_status",
            "claim_submitted_at",
            "claim_reviewed_at",
            "claim_delivered_at",
            "created",
        ]
        read_only_fields = fields

    def get_claim_status(self, obj: PrizeAward) -> str | None:
        claim = _get_award_claim(obj)
        return claim.status if claim is not None else None

    def get_claim_submitted_at(self, obj: PrizeAward):
        claim = _get_award_claim(obj)
        return claim.submitted_at if claim is not None else None

    def get_claim_reviewed_at(self, obj: PrizeAward):
        claim = _get_award_claim(obj)
        return claim.reviewed_at if claim is not None else None

    def get_claim_delivered_at(self, obj: PrizeAward):
        claim = _get_award_claim(obj)
        return claim.delivered_at if claim is not None else None


class AdminPrizeAwardSerializer(serializers.ModelSerializer):
    """Manager monitoring read serializer for cross-domain prize awards."""

    user_id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    profile_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = PrizeAward
        fields = [
            "id",
            "user_id",
            "username",
            "profile_id",
            "source_type",
            "source_id",
            "fulfillment_type",
            "status",
            "title",
            "description",
            "prize_snapshot",
            "idempotency_key",
            "awarded_at",
            "fulfilled_at",
            "metadata",
            "created",
            "modified",
        ]
        read_only_fields = fields


class PrizeClaimSubmitSerializer(serializers.Serializer):
    """Input serializer for user claim submission on a CLAIM_REQUIRED award."""

    full_name = serializers.CharField(max_length=255, trim_whitespace=True)
    address = serializers.CharField(trim_whitespace=True)
    identity_number = serializers.CharField(
        max_length=100, trim_whitespace=True
    )


class MyPrizeClaimResponseSerializer(serializers.ModelSerializer):
    """Safe user-facing response serializer after claim submission (never echoes PII)."""

    award_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = PrizeClaim
        fields = [
            "id",
            "award_id",
            "status",
            "submitted_at",
        ]
        read_only_fields = fields


class AdminPrizeClaimSerializer(serializers.ModelSerializer):
    """Manager review/delivery serializer for PrizeClaim records."""

    award_id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(source="award.user_id", read_only=True)
    profile_id = serializers.IntegerField(
        source="award.profile_id", read_only=True, allow_null=True
    )
    source_type = serializers.CharField(
        source="award.source_type", read_only=True
    )
    source_id = serializers.CharField(source="award.source_id", read_only=True)
    award_status = serializers.CharField(source="award.status", read_only=True)
    award_fulfilled_at = serializers.DateTimeField(
        source="award.fulfilled_at", read_only=True, allow_null=True
    )
    title = serializers.CharField(source="award.title", read_only=True)
    prize_snapshot = serializers.JSONField(
        source="award.prize_snapshot", read_only=True
    )
    reviewed_by_id = serializers.IntegerField(read_only=True, allow_null=True)
    delivered_by_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = PrizeClaim
        fields = [
            "id",
            "award_id",
            "user_id",
            "profile_id",
            "source_type",
            "source_id",
            "award_status",
            "award_fulfilled_at",
            "title",
            "prize_snapshot",
            "full_name",
            "address",
            "identity_number",
            "status",
            "submitted_at",
            "reviewed_at",
            "reviewed_by_id",
            "delivered_at",
            "delivered_by_id",
            "created",
            "modified",
        ]
        read_only_fields = fields


class AdminPrizeClaimActionSerializer(serializers.Serializer):
    """Empty command request serializer for manager claim review and delivery."""


