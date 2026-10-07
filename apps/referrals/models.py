from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel


class LinkSharingCode(BaseModel):
    owner_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="referral_codes",
    )
    code = models.CharField(max_length=32, unique=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_by_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="used_referral_codes",
        null=True,
        blank=True,
    )
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["owner_profile", "-created"], name="ref_owner_created_idx"
            ),
            models.Index(fields=["code"], name="ref_code_idx"),
            models.Index(fields=["expires_at"], name="ref_expires_idx"),
        ]

    def __str__(self):
        return self.code


class ReferralStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")


class Referral(BaseModel):
    """Verified (or to-be-verified) inviter -> referred account relationship.

    Created as ``pending`` when a new account redeems a share code; a manager
    approves or rejects it. Separate from ``LinkSharingCode.is_used``, which
    only means the code was consumed. Each code yields at most one referral and
    each referred account can be referred only once, ever (rejection included).
    """

    code = models.OneToOneField(
        LinkSharingCode,
        on_delete=models.PROTECT,
        related_name="referral",
    )
    inviter_user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        related_name="referrals_made",
    )
    inviter_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="referrals_made",
    )
    referred_user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        related_name="referrals_received",
    )
    referred_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.PROTECT,
        related_name="referrals_received",
    )
    status = models.CharField(
        max_length=20,
        choices=ReferralStatus.choices,
        default=ReferralStatus.PENDING,
    )
    redeemed_at = models.DateTimeField()
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="referrals_reviewed",
        null=True,
        blank=True,
    )
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-redeemed_at", "-id"]
        indexes = [
            models.Index(
                fields=["inviter_user", "status"],
                name="ref_inviter_status_idx",
            ),
            models.Index(
                fields=["status", "redeemed_at"],
                name="ref_status_redeemed_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["referred_user"],
                name="referrals_unique_referred_user",
            ),
            models.CheckConstraint(
                check=~Q(inviter_user=F("referred_user")),
                name="referrals_not_self",
            ),
        ]

    def __str__(self):
        return f"Referral #{self.pk} ({self.status})"


class ReferralChallengeStatus(models.TextChoices):
    """Effective lifecycle, derived from the dates and ``finalized_at``."""

    DRAFT = "draft", _("Draft")
    ACTIVE = "active", _("Active")
    REVIEW = "review", _("Review")
    FINALIZED = "finalized", _("Finalized")


class ReferralChallengePrizeCode(models.TextChoices):
    IPHONE = "IPHONE", _("iPhone")
    AIRPODS = "AIRPODS", _("AirPods")
    ANNUAL_LOFT_SUBSCRIPTION = (
        "ANNUAL_LOFT_SUBSCRIPTION",
        _("Annual Loft subscription"),
    )


class ReferralChallenge(BaseModel):
    """Monthly referral competition over ``[starts_at, ends_at)``.

    Its score is the number of approved referrals an inviter account redeemed
    inside the window; nothing here is PP. The lifecycle is derived from the
    dates (no scheduled job): draft, active, review (window over, pending
    referrals still being reviewed), finalized (winners frozen).
    """

    name = models.CharField(max_length=255)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="referral_challenges_created",
        null=True,
        blank=True,
    )
    finalized_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="referral_challenges_finalized",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-starts_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=Q(starts_at__lt=F("ends_at")),
                name="ref_chal_starts_before_ends",
            ),
        ]

    def status_at(self, now):
        if self.finalized_at is not None:
            return ReferralChallengeStatus.FINALIZED
        if now < self.starts_at:
            return ReferralChallengeStatus.DRAFT
        if now < self.ends_at:
            return ReferralChallengeStatus.ACTIVE
        return ReferralChallengeStatus.REVIEW

    def __str__(self):
        return self.name


class ReferralChallengePrize(BaseModel):
    """Generic prize definition for one winning rank (no fulfillment data)."""

    challenge = models.ForeignKey(
        ReferralChallenge,
        on_delete=models.CASCADE,
        related_name="prizes",
    )
    rank = models.PositiveSmallIntegerField()
    prize_code = models.CharField(
        max_length=40,
        choices=ReferralChallengePrizeCode.choices,
    )
    title = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["rank", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["challenge", "rank"],
                condition=Q(deleted__isnull=True),
                name="ref_chal_prize_rank_uniq",
            ),
            models.CheckConstraint(
                check=Q(rank__gte=1, rank__lte=3),
                name="ref_chal_prize_rank_range",
            ),
        ]

    def __str__(self):
        return f"{self.challenge_id} #{self.rank} {self.prize_code}"


class ReferralChallengeWinner(BaseModel):
    """Frozen Top 3 result, written once when a challenge is finalized.

    Score, tie-break time, and prize are copied so later data changes cannot
    rewrite history. Rows cannot be changed or deleted.
    """

    challenge = models.ForeignKey(
        ReferralChallenge,
        on_delete=models.PROTECT,
        related_name="winners",
    )
    rank = models.PositiveSmallIntegerField()
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.PROTECT,
        related_name="referral_challenge_wins",
    )
    score = models.PositiveIntegerField()
    score_achieved_at = models.DateTimeField()
    prize = models.ForeignKey(
        ReferralChallengePrize,
        on_delete=models.PROTECT,
        related_name="winners",
        null=True,
        blank=True,
    )
    prize_code = models.CharField(max_length=40, blank=True)
    prize_title = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["rank", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["challenge", "rank"],
                name="ref_chal_winner_rank_uniq",
            ),
            models.UniqueConstraint(
                fields=["challenge", "user"],
                name="ref_chal_winner_user_uniq",
            ),
            models.CheckConstraint(
                check=Q(rank__gte=1, rank__lte=3),
                name="ref_chal_winner_rank_range",
            ),
            models.CheckConstraint(
                check=Q(score__gte=1),
                name="ref_chal_winner_score_gte_1",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_("Referral challenge winners cannot be changed."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Referral challenge winners cannot be deleted."))

    def __str__(self):
        return f"{self.challenge_id} #{self.rank} user {self.user_id}"
