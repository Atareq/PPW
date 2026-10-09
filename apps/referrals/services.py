from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, ValidationError

from apps.referrals.models import (
    LinkSharingCode,
    Referral,
    ReferralChallenge,
    ReferralChallengePrize,
    ReferralChallengePrizeCode,
    ReferralChallengeStatus,
    ReferralChallengeWinner,
    ReferralStatus,
)
from apps.users.models import Profile


REFERRAL_CODE_TTL_HOURS = 24
REFERRAL_NEW_ACCOUNT_WINDOW_HOURS = 2
REFERRAL_CHALLENGE_LEADERBOARD_SIZE = 10
REFERRAL_CHALLENGE_WINNER_COUNT = 3
DEFAULT_REFERRAL_CHALLENGE_PRIZES = (
    (1, ReferralChallengePrizeCode.IPHONE, "iPhone"),
    (2, ReferralChallengePrizeCode.AIRPODS, "AirPods"),
    (
        3,
        ReferralChallengePrizeCode.ANNUAL_LOFT_SUBSCRIPTION,
        "Annual Loft subscription",
    ),
)


class ReferralService:
    """Referral codes, pending referrals, and manager review.

    Redeeming a code consumes it and creates a ``pending`` referral; nothing is
    paid. Only a manager approval emits ``REFERRAL_APPROVED``, which the reward
    engine turns into the inviter reward.
    """

    @staticmethod
    @transaction.atomic
    def create_code(owner_profile):
        """Create a short-lived share code for the active profile."""

        owner = Profile.objects.select_related("user").get(pk=owner_profile.pk)
        expires_at = timezone.now() + timezone.timedelta(hours=REFERRAL_CODE_TTL_HOURS)
        for _ in range(5):
            try:
                with transaction.atomic():
                    code = LinkSharingCode.objects.create(
                        owner_profile=owner,
                        code=uuid4().hex[:12],
                        expires_at=expires_at,
                    )
                return code
            except IntegrityError:
                continue
        raise ValidationError({"code": _("Could not create a unique referral code.")})

    @staticmethod
    @transaction.atomic
    def redeem_code(active_profile, raw_code):
        """Consume one code for a new account and create its pending referral."""

        code_value = str(raw_code or "").strip()
        if not code_value:
            raise ValidationError({"code": _("This field is required.")})

        redeemer = Profile.objects.select_related("user").get(pk=active_profile.pk)
        account_age = timezone.now() - redeemer.user.date_joined
        if account_age > timezone.timedelta(hours=REFERRAL_NEW_ACCOUNT_WINDOW_HOURS):
            raise ValidationError(
                {"code": _("Referral code is only valid for new accounts.")}
            )

        try:
            code = (
                LinkSharingCode.objects.select_for_update()
                .select_related("owner_profile", "owner_profile__user")
                .get(code=code_value)
            )
        except LinkSharingCode.DoesNotExist as exc:
            raise ValidationError({"code": _("Referral code was not found.")}) from exc

        if code.is_used:
            raise ValidationError({"code": _("Referral code was already used.")})
        if code.expires_at <= timezone.now():
            raise ValidationError({"code": _("Referral code has expired.")})
        if (
            code.owner_profile_id == redeemer.pk
            or code.owner_profile.user_id == redeemer.user_id
        ):
            raise ValidationError({"code": _("Cannot redeem your own referral code.")})
        # One referral per account, whatever its outcome: no referral shopping.
        if Referral.all_objects.filter(referred_user_id=redeemer.user_id).exists():
            raise ValidationError(
                {"code": _("This account has already redeemed a referral code.")}
            )

        now = timezone.now()
        code.is_used = True
        code.used_by_profile = redeemer
        code.used_at = now
        code.save(update_fields=["is_used", "used_by_profile", "used_at", "modified"])
        try:
            with transaction.atomic():
                return Referral.objects.create(
                    code=code,
                    inviter_user_id=code.owner_profile.user_id,
                    inviter_profile=code.owner_profile,
                    referred_user_id=redeemer.user_id,
                    referred_profile=redeemer,
                    status=ReferralStatus.PENDING,
                    redeemed_at=now,
                )
        except IntegrityError as exc:
            raise ValidationError(
                {"code": _("This account has already redeemed a referral code.")}
            ) from exc

    @staticmethod
    @transaction.atomic
    def approve(referral_id, reviewer):
        """Approve a pending referral and emit ``REFERRAL_APPROVED`` once.

        Approving an already approved referral returns it unchanged (no second
        event); a rejected referral cannot be approved.
        """

        referral = ReferralService._get_locked(referral_id)
        if referral.status == ReferralStatus.APPROVED:
            return referral
        if referral.status != ReferralStatus.PENDING:
            raise ValidationError(
                {"status": _("Only pending referrals can be approved.")}
            )
        referral.status = ReferralStatus.APPROVED
        referral.reviewed_at = timezone.now()
        referral.reviewed_by = reviewer
        referral.save(
            update_fields=["status", "reviewed_at", "reviewed_by", "modified"]
        )
        ReferralService._emit_referral_approved(referral, reviewer)
        return referral

    @staticmethod
    @transaction.atomic
    def reject(referral_id, reviewer, reason=""):
        """Reject a pending referral; it is never reward-eligible afterwards."""

        referral = ReferralService._get_locked(referral_id)
        if referral.status == ReferralStatus.REJECTED:
            return referral
        if referral.status != ReferralStatus.PENDING:
            raise ValidationError(
                {"status": _("Only pending referrals can be rejected.")}
            )
        referral.status = ReferralStatus.REJECTED
        referral.reviewed_at = timezone.now()
        referral.reviewed_by = reviewer
        referral.rejection_reason = reason or ""
        referral.save(
            update_fields=[
                "status",
                "reviewed_at",
                "reviewed_by",
                "rejection_reason",
                "modified",
            ]
        )
        return referral

    @staticmethod
    def approved_count(inviter_profile):
        return Referral.objects.filter(
            inviter_profile_id=getattr(inviter_profile, "pk", inviter_profile),
            status=ReferralStatus.APPROVED,
        ).count()

    @staticmethod
    def _get_locked(referral_id):
        try:
            return Referral.objects.select_for_update().get(pk=referral_id)
        except Referral.DoesNotExist as exc:
            raise NotFound(_("Referral was not found.")) from exc

    @staticmethod
    def _emit_referral_approved(referral, reviewer):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.REFERRAL_APPROVED,
            actor=referral.inviter_profile,
            profile=referral.inviter_profile,
            context={"referral": referral},
        )

    @staticmethod
    def share_url(code):
        return f"{settings.PUBLIC_FRONTEND_BASE_URL}/referrals/{code.code}"


class ReferralChallengeService:
    """Monthly referral challenge: scoring, leaderboard, and finalization.

    The score of an inviter account is the number of its ``approved``
    referrals with ``starts_at <= redeemed_at < ends_at``. The redemption time
    decides which challenge a referral belongs to; approval (possibly during
    review) decides whether it counts. Scores are not PP and never create
    point transactions. Ranking: higher score, then the earlier time the
    account reached that score (its latest counted ``redeemed_at``), then the
    lower account id.
    """

    @staticmethod
    def scoring_referrals(challenge):
        return Referral.objects.filter(
            status=ReferralStatus.APPROVED,
            redeemed_at__gte=challenge.starts_at,
            redeemed_at__lt=challenge.ends_at,
        )

    @classmethod
    def ranked_scores(cls, challenge):
        """Scoring accounts in rank order; accounts with no score are absent."""

        return (
            cls.scoring_referrals(challenge)
            .values("inviter_user_id", "inviter_user__username")
            .annotate(score=Count("id"), achieved_at=Max("redeemed_at"))
            .order_by("-score", "achieved_at", "inviter_user_id")
        )

    @classmethod
    def leaderboard(cls, challenge, limit=REFERRAL_CHALLENGE_LEADERBOARD_SIZE):
        return [
            {
                "rank": rank,
                "user_id": row["inviter_user_id"],
                "username": row["inviter_user__username"],
                "challenge_score": row["score"],
                "score_achieved_at": row["achieved_at"],
            }
            for rank, row in enumerate(cls.ranked_scores(challenge)[:limit], start=1)
        ]

    @classmethod
    def progress(cls, challenge, user):
        """The account's score, rank, and gap to the leader (rank None at 0)."""

        user_id = getattr(user, "pk", user)
        mine = cls.scoring_referrals(challenge).filter(
            inviter_user_id=user_id
        ).aggregate(score=Count("id"), achieved_at=Max("redeemed_at"))
        leader = cls.ranked_scores(challenge).first()
        leader_score = leader["score"] if leader else 0
        score = mine["score"]
        rank = None
        if score:
            ahead = cls.ranked_scores(challenge).filter(
                Q(score__gt=score)
                | Q(score=score, achieved_at__lt=mine["achieved_at"])
                | Q(
                    score=score,
                    achieved_at=mine["achieved_at"],
                    inviter_user_id__lt=user_id,
                )
            )
            rank = ahead.count() + 1
        return {
            "challenge_score": score,
            "rank": rank,
            "leader_score": leader_score,
            "gap_to_first": max(leader_score - score, 0),
        }

    @staticmethod
    def countdown(challenge, now):
        status = challenge.status_at(now)
        seconds_remaining = 0
        if status == ReferralChallengeStatus.ACTIVE:
            seconds_remaining = int((challenge.ends_at - now).total_seconds())
        seconds_until_start = int((challenge.starts_at - now).total_seconds())
        return {
            "server_time": now,
            "seconds_remaining": max(seconds_remaining, 0),
            "seconds_until_start": max(seconds_until_start, 0),
        }

    @staticmethod
    def get_current(now=None):
        """The active challenge, else the nearest upcoming one; never a past one."""

        now = now or timezone.now()
        live = ReferralChallenge.objects.filter(finalized_at__isnull=True)
        challenge = (
            live.filter(starts_at__lte=now, ends_at__gt=now).first()
            or live.filter(starts_at__gt=now).order_by("starts_at", "id").first()
        )
        if challenge is None:
            raise NotFound(_("No referral challenge is running or scheduled."))
        return challenge

    @classmethod
    def current_overview(cls, user, now=None):
        now = now or timezone.now()
        challenge = cls.get_current(now)
        user_id = getattr(user, "pk", user)
        top = [
            {
                "rank": row["rank"],
                "username": row["username"],
                "challenge_score": row["challenge_score"],
                "is_me": row["user_id"] == user_id,
            }
            for row in cls.leaderboard(challenge)
        ]
        return {
            "id": challenge.pk,
            "name": challenge.name,
            "status": challenge.status_at(now),
            "starts_at": challenge.starts_at,
            "ends_at": challenge.ends_at,
            **cls.countdown(challenge, now),
            "top_10": top,
            "me": cls.progress(challenge, user_id),
            "prizes": list(cls.prizes(challenge)),
        }

    @staticmethod
    def prizes(challenge):
        return ReferralChallengePrize.objects.filter(challenge=challenge).order_by(
            "rank",
            "id",
        )

    @classmethod
    @transaction.atomic
    def create_challenge(cls, *, name, starts_at, ends_at, created_by, prizes=None):
        """Create a challenge; without ``prizes`` the default Top 3 prizes apply."""

        cls._lock_challenges()
        cls._validate_no_overlap(starts_at, ends_at)
        challenge = ReferralChallenge.objects.create(
            name=name,
            starts_at=starts_at,
            ends_at=ends_at,
            created_by=created_by,
        )
        if prizes is None:
            prizes = [
                {"rank": rank, "prize_code": code, "title": title}
                for rank, code, title in DEFAULT_REFERRAL_CHALLENGE_PRIZES
            ]
        cls._create_prizes(challenge, prizes)
        return challenge

    @classmethod
    @transaction.atomic
    def update_challenge(cls, challenge_id, changes, now=None):
        """Edit a challenge. Dates and prizes are editable only before it starts.

        Supplied ``prizes`` replace the whole set. Finalized challenges are
        read-only.
        """

        cls._lock_challenges()
        challenge = cls._get_locked(challenge_id)
        status = challenge.status_at(now or timezone.now())
        if status == ReferralChallengeStatus.FINALIZED:
            raise ValidationError(
                {"detail": _("Finalized referral challenges cannot be changed.")}
            )
        frozen = {"starts_at", "ends_at", "prizes"} & set(changes)
        if frozen and status != ReferralChallengeStatus.DRAFT:
            raise ValidationError(
                {
                    field: _(
                        "Dates and prizes cannot be changed after the challenge "
                        "starts."
                    )
                    for field in sorted(frozen)
                }
            )
        starts_at = changes.get("starts_at", challenge.starts_at)
        ends_at = changes.get("ends_at", challenge.ends_at)
        if starts_at >= ends_at:
            raise ValidationError(
                {"ends_at": _("The challenge must end after it starts.")}
            )
        cls._validate_no_overlap(starts_at, ends_at, exclude_id=challenge.pk)
        for field in ("name", "starts_at", "ends_at"):
            if field in changes:
                setattr(challenge, field, changes[field])
        challenge.save()
        if "prizes" in changes:
            cls.prizes(challenge).delete()
            cls._create_prizes(challenge, changes["prizes"])
        return challenge

    @classmethod
    @transaction.atomic
    def finalize(cls, challenge_id, finalized_by, now=None):
        """Freeze the Top 3 of an ended challenge into winner snapshots.

        Refused before ``ends_at`` and while any referral redeemed in the
        window is still pending. Finalizing again returns the existing result.
        """

        challenge = cls._get_locked(challenge_id)
        if challenge.finalized_at is not None:
            return challenge
        now = now or timezone.now()
        if now < challenge.ends_at:
            raise ValidationError(
                {
                    "detail": _(
                        "A referral challenge can be finalized only after it ends."
                    )
                }
            )
        has_pending = Referral.objects.filter(
            status=ReferralStatus.PENDING,
            redeemed_at__gte=challenge.starts_at,
            redeemed_at__lt=challenge.ends_at,
        ).exists()
        if has_pending:
            raise ValidationError(
                {"detail": _("Challenge has pending referrals awaiting review.")}
            )
        prizes = {prize.rank: prize for prize in cls.prizes(challenge)}
        top = cls.leaderboard(challenge, limit=REFERRAL_CHALLENGE_WINNER_COUNT)
        ReferralChallengeWinner.objects.bulk_create(
            [
                ReferralChallengeWinner(
                    challenge=challenge,
                    rank=row["rank"],
                    user_id=row["user_id"],
                    score=row["challenge_score"],
                    score_achieved_at=row["score_achieved_at"],
                    prize=prizes.get(row["rank"]),
                    prize_code=getattr(prizes.get(row["rank"]), "prize_code", ""),
                    prize_title=getattr(prizes.get(row["rank"]), "title", ""),
                )
                for row in top
            ]
        )
        cls._record_winner_prize_awards(challenge, now=now)
        challenge.finalized_at = now
        challenge.finalized_by = finalized_by
        challenge.save(update_fields=["finalized_at", "finalized_by", "modified"])
        return challenge

    CLAIM_REQUIRED_REFERRAL_PRIZE_CODES = frozenset(
        {
            ReferralChallengePrizeCode.IPHONE,
            ReferralChallengePrizeCode.AIRPODS,
        }
    )

    @staticmethod
    def winner_prize_award_idempotency_key(winner_id: int) -> str:
        return f"prize:source:referral_challenge:winner:{winner_id}"

    @classmethod
    def _record_winner_prize_awards(cls, challenge, *, now=None):
        """Record `CLAIM_REQUIRED` (`PENDING`) `PrizeAward` rows for canonical in-kind referral winners."""

        from apps.prizes.models import PrizeAwardSourceType
        from apps.prizes.services import PrizeAwardService

        effective_now = now or challenge.finalized_at or timezone.now()
        winners = (
            ReferralChallengeWinner.objects.filter(challenge=challenge)
            .select_related("user", "prize")
            .order_by("rank", "id")
        )
        awards = []
        for winner in winners:
            if winner.prize_code not in cls.CLAIM_REQUIRED_REFERRAL_PRIZE_CODES:
                continue
            title = winner.prize_title or winner.prize_code
            award = PrizeAwardService.record_claim_required(
                user=winner.user,
                profile=None,
                source_type=PrizeAwardSourceType.REFERRAL_CHALLENGE,
                source_id=str(winner.pk),
                title=title,
                description=f"{challenge.name} - Rank #{winner.rank}",
                prize_snapshot={
                    "challenge_id": challenge.pk,
                    "challenge_name": challenge.name,
                    "winner_id": winner.pk,
                    "rank": winner.rank,
                    "score": winner.score,
                    "prize_id": winner.prize_id,
                    "prize_code": winner.prize_code,
                    "prize_title": winner.prize_title,
                },
                idempotency_key=cls.winner_prize_award_idempotency_key(
                    winner.pk
                ),
                awarded_at=effective_now,
                metadata={
                    "challenge_id": challenge.pk,
                    "winner_id": winner.pk,
                    "rank": winner.rank,
                    "prize_code": winner.prize_code,
                },
            )
            awards.append(award)
        return awards

    @staticmethod
    def _create_prizes(challenge, prizes):
        ReferralChallengePrize.objects.bulk_create(
            [
                ReferralChallengePrize(
                    challenge=challenge,
                    rank=prize["rank"],
                    prize_code=prize["prize_code"],
                    title=prize["title"],
                    metadata=prize.get("metadata") or {},
                )
                for prize in prizes
            ]
        )

    @staticmethod
    def _lock_challenges():
        """Serialize challenge writes so overlap checks see each other's rows."""

        list(ReferralChallenge.objects.select_for_update().values_list("pk", flat=True))

    @staticmethod
    def _validate_no_overlap(starts_at, ends_at, exclude_id=None):
        overlapping = ReferralChallenge.objects.filter(
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
        )
        if exclude_id is not None:
            overlapping = overlapping.exclude(pk=exclude_id)
        if overlapping.exists():
            raise ValidationError(
                {"starts_at": _("Referral challenge periods cannot overlap.")}
            )

    @staticmethod
    def _get_locked(challenge_id):
        try:
            return ReferralChallenge.objects.select_for_update().get(pk=challenge_id)
        except ReferralChallenge.DoesNotExist as exc:
            raise NotFound(_("Referral challenge was not found.")) from exc
