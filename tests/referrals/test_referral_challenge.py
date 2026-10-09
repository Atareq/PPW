"""BE-REF-03: monthly referral challenge scored by approved referrals."""

import importlib
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import override_settings
from django.utils import timezone, translation
from django.utils.dateparse import parse_datetime
from freezegun import freeze_time
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.auctions.services import BiddingService
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.loyalty.constants import RewardSourceType
from apps.loyalty.models import PointTransaction
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
from apps.referrals.services import ReferralChallengeService, ReferralService
from apps.users.constants import ProfileType
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    ManagerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db

ADMIN_URL = "/api/referrals/admin/challenges/"
CURRENT_URL = "/api/referrals/challenge/current/"
backfill_module = importlib.import_module(
    "apps.referrals.migrations.0003_backfill_legacy_referrals"
)


# --- Helpers -----------------------------------------------------------------------


def new_customer():
    return UserAccountFactory(with_profile=ProfileType.CUSTOMER)


def new_seller():
    return UserAccountFactory(with_profile=ProfileType.SELLER)


def manager(permissions=None):
    user = UserAccountFactory(with_profile=False)
    kwargs = {"user": user, "is_active": True}
    if permissions is not None:
        kwargs["permissions"] = permissions
    ManagerProfileFactory(**kwargs)
    return user


def client_for(user):
    user.refresh_from_db()
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {build_token_for_user(user).access_token}"
    )
    return client


def make_challenge(starts_at, ends_at, **kwargs):
    return ReferralChallengeService.create_challenge(
        name=kwargs.pop("name", "Challenge"),
        starts_at=starts_at,
        ends_at=ends_at,
        created_by=None,
        **kwargs,
    )


def active_challenge(**kwargs):
    now = timezone.now()
    return make_challenge(now - timedelta(days=10), now + timedelta(days=10), **kwargs)


def review_challenge(**kwargs):
    now = timezone.now()
    return make_challenge(now - timedelta(days=40), now - timedelta(days=10), **kwargs)


def make_referral(inviter, *, at, status=ReferralStatus.APPROVED, profile=None):
    """A referral redeemed at ``at``; ``profile`` is the code-owning mode."""

    profile = profile or inviter.active_profile
    referred = new_customer()
    code = LinkSharingCode.objects.create(
        owner_profile=profile,
        code=uuid4().hex[:12],
        expires_at=at + timedelta(hours=1),
        is_used=True,
        used_by_profile=referred.active_profile,
        used_at=at,
    )
    return Referral.objects.create(
        code=code,
        inviter_user=inviter,
        inviter_profile=profile,
        referred_user=referred,
        referred_profile=referred.active_profile,
        status=status,
        redeemed_at=at,
        reviewed_at=None if status == ReferralStatus.PENDING else at,
    )


def give_score(inviter, challenge, count, *, first_at=None):
    first_at = first_at or challenge.starts_at + timedelta(hours=1)
    return [
        make_referral(inviter, at=first_at + timedelta(minutes=index))
        for index in range(count)
    ]


def score_of(challenge, user):
    return ReferralChallengeService.progress(challenge, user)["challenge_score"]


def board(challenge):
    return [
        (row["user_id"], row["challenge_score"])
        for row in ReferralChallengeService.leaderboard(challenge)
    ]


# --- Scoring -----------------------------------------------------------------------


def test_approved_referral_in_window_scores_one():
    challenge = active_challenge()
    inviter = new_customer()
    give_score(inviter, challenge, 1)

    assert score_of(challenge, inviter) == 1


@pytest.mark.parametrize(
    "referral_status", [ReferralStatus.PENDING, ReferralStatus.REJECTED]
)
def test_pending_and_rejected_referrals_do_not_score(referral_status):
    challenge = active_challenge()
    inviter = new_customer()
    make_referral(
        inviter,
        at=challenge.starts_at + timedelta(hours=1),
        status=referral_status,
    )

    assert score_of(challenge, inviter) == 0
    assert board(challenge) == []


def test_consumed_codes_do_not_score():
    challenge = active_challenge()
    inviter = new_customer()
    at = challenge.starts_at + timedelta(hours=1)
    make_referral(inviter, at=at, status=ReferralStatus.PENDING)
    LinkSharingCode.objects.create(
        owner_profile=inviter.active_profile,
        code="CONSUMED",
        expires_at=at + timedelta(hours=1),
        is_used=True,
        used_at=at,
    )

    assert score_of(challenge, inviter) == 0


def test_inviter_modes_combine_into_one_account_score():
    challenge = active_challenge()
    inviter = new_customer()
    seller_mode = SellerProfileFactory(user=inviter, is_active=False)
    at = challenge.starts_at + timedelta(hours=1)
    make_referral(inviter, at=at)
    make_referral(inviter, at=at + timedelta(minutes=1), profile=seller_mode)

    assert board(challenge) == [(inviter.pk, 2)]


def test_window_is_half_open():
    challenge = active_challenge()
    before, at_start, at_end = new_customer(), new_customer(), new_customer()
    make_referral(before, at=challenge.starts_at - timedelta(microseconds=1))
    make_referral(at_start, at=challenge.starts_at)
    make_referral(at_end, at=challenge.ends_at)

    assert score_of(challenge, before) == 0
    assert score_of(challenge, at_start) == 1
    assert score_of(challenge, at_end) == 0


def test_late_approval_during_review_counts_for_the_redemption_window():
    challenge = review_challenge()
    inviter = new_customer()
    referral = make_referral(
        inviter,
        at=challenge.ends_at - timedelta(minutes=30),
        status=ReferralStatus.PENDING,
    )
    assert challenge.status_at(timezone.now()) == ReferralChallengeStatus.REVIEW

    ReferralService.approve(referral.pk, manager())

    assert score_of(challenge, inviter) == 1


def test_approval_time_does_not_choose_the_challenge():
    challenge = active_challenge()
    inviter = new_customer()
    referral = make_referral(
        inviter,
        at=challenge.starts_at - timedelta(days=1),
        status=ReferralStatus.PENDING,
    )

    ReferralService.approve(referral.pk, manager())

    assert score_of(challenge, inviter) == 0


def test_legacy_backfilled_referral_counts_by_redemption_time():
    challenge = active_challenge()
    inviter, referred = new_customer(), new_customer()
    code = LinkSharingCode.objects.create(
        owner_profile=inviter.active_profile,
        code="LEGACY",
        expires_at=challenge.starts_at + timedelta(days=1),
        is_used=True,
        used_by_profile=referred.active_profile,
        used_at=challenge.starts_at + timedelta(hours=2),
    )
    backfill_module.backfill_legacy_referrals(django_apps, None)

    assert Referral.objects.get(code=code).status == ReferralStatus.APPROVED
    assert score_of(challenge, inviter) == 1


# --- Leaderboard -------------------------------------------------------------------


def test_leaderboard_returns_only_the_top_10():
    challenge = active_challenge()
    inviters = [new_customer() for _ in range(12)]
    for score, inviter in enumerate(inviters, start=1):
        give_score(inviter, challenge, score)

    rows = board(challenge)

    assert len(rows) == 10
    assert rows == [(inviter.pk, score) for score, inviter in reversed(
        list(enumerate(inviters, start=1))[2:]
    )]


def test_zero_score_accounts_do_not_fill_the_leaderboard():
    challenge = active_challenge()
    scorer = new_customer()
    give_score(scorer, challenge, 1)
    for _ in range(3):
        new_customer()

    assert board(challenge) == [(scorer.pk, 1)]


def test_tie_goes_to_the_account_that_reached_the_score_first():
    challenge = active_challenge()
    # The earlier achiever has the higher id, so only achievement time ranks it.
    late, early = new_customer(), new_customer()
    assert early.pk > late.pk
    start = challenge.starts_at + timedelta(hours=1)
    give_score(late, challenge, 2, first_at=start)
    make_referral(late, at=start + timedelta(days=2))
    give_score(early, challenge, 3, first_at=start + timedelta(hours=1))

    assert board(challenge) == [(early.pk, 3), (late.pk, 3)]
    assert ReferralChallengeService.progress(challenge, early)["rank"] == 1
    assert ReferralChallengeService.progress(challenge, late)["rank"] == 2


def test_exact_tie_goes_to_the_lower_account_id():
    challenge = active_challenge()
    first, second = sorted([new_customer(), new_customer()], key=lambda user: user.pk)
    at = challenge.starts_at + timedelta(hours=1)
    make_referral(second, at=at)
    make_referral(first, at=at)

    assert board(challenge) == [(first.pk, 1), (second.pk, 1)]


def test_personal_progress_against_the_leader():
    challenge = active_challenge()
    leader, follower = new_customer(), new_customer()
    give_score(leader, challenge, 20)
    give_score(follower, challenge, 15)

    assert ReferralChallengeService.progress(challenge, follower) == {
        "challenge_score": 15,
        "rank": 2,
        "leader_score": 20,
        "gap_to_first": 5,
    }
    assert ReferralChallengeService.progress(challenge, leader) == {
        "challenge_score": 20,
        "rank": 1,
        "leader_score": 20,
        "gap_to_first": 0,
    }


def test_progress_rank_matches_the_leaderboard_under_ties():
    challenge = active_challenge()
    users = sorted([new_customer() for _ in range(3)], key=lambda user: user.pk)
    at = challenge.starts_at + timedelta(hours=1)
    for user in users:
        make_referral(user, at=at)

    ranks = [
        ReferralChallengeService.progress(challenge, user)["rank"] for user in users
    ]

    assert ranks == [1, 2, 3]


def test_zero_score_progress_has_no_rank():
    challenge = active_challenge()
    give_score(new_customer(), challenge, 4)

    assert ReferralChallengeService.progress(challenge, new_customer()) == {
        "challenge_score": 0,
        "rank": None,
        "leader_score": 4,
        "gap_to_first": 4,
    }


# --- Lifecycle and countdown -------------------------------------------------------


def test_status_is_derived_from_dates_and_finalization():
    now = timezone.now()
    challenge = ReferralChallenge(starts_at=now, ends_at=now + timedelta(days=30))

    before = now - timedelta(seconds=1)
    assert challenge.status_at(before) == ReferralChallengeStatus.DRAFT
    assert challenge.status_at(now) == ReferralChallengeStatus.ACTIVE
    assert challenge.status_at(challenge.ends_at) == ReferralChallengeStatus.REVIEW
    challenge.finalized_at = challenge.ends_at
    assert challenge.status_at(challenge.ends_at) == ReferralChallengeStatus.FINALIZED


def test_countdown_by_status():
    now = timezone.now()
    challenge = ReferralChallenge(
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(days=1),
    )

    draft = ReferralChallengeService.countdown(challenge, now)
    active = ReferralChallengeService.countdown(challenge, now + timedelta(hours=3))
    review = ReferralChallengeService.countdown(challenge, now + timedelta(days=2))

    assert draft == {
        "server_time": now,
        "seconds_remaining": 0,
        "seconds_until_start": 7200,
    }
    assert active["seconds_remaining"] == 21 * 3600
    assert active["seconds_until_start"] == 0
    assert review["seconds_remaining"] == review["seconds_until_start"] == 0


def test_current_endpoint_returns_countdown_top_10_progress_and_prizes():
    with freeze_time("2026-10-15 12:00:00"):
        now = timezone.now()
        challenge = make_challenge(
            now - timedelta(days=14), now + timedelta(hours=1), name="October"
        )
        leader, me = new_customer(), new_customer()
        give_score(leader, challenge, 3)
        give_score(me, challenge, 1)

        response = client_for(me).get(CURRENT_URL)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == challenge.pk
    assert data["status"] == ReferralChallengeStatus.ACTIVE
    assert data["seconds_remaining"] == 3600
    assert data["seconds_until_start"] == 0
    assert parse_datetime(data["server_time"]) == now
    top = [
        (row["rank"], row["challenge_score"], row["is_me"]) for row in data["top_10"]
    ]
    assert top == [(1, 3, False), (2, 1, True)]
    assert data["me"] == {
        "challenge_score": 1,
        "rank": 2,
        "leader_score": 3,
        "gap_to_first": 2,
    }
    assert data["prizes"] == [
        {"rank": 1, "prize_code": "IPHONE", "title": "iPhone"},
        {"rank": 2, "prize_code": "AIRPODS", "title": "AirPods"},
        {
            "rank": 3,
            "prize_code": "ANNUAL_LOFT_SUBSCRIPTION",
            "title": "Annual Loft subscription",
        },
    ]


def test_current_endpoint_exposes_only_public_identity():
    challenge = active_challenge()
    leader = new_customer()
    give_score(leader, challenge, 1)

    response = client_for(new_customer()).get(CURRENT_URL)

    row = response.json()["top_10"][0]
    assert set(row) == {"rank", "username", "challenge_score", "is_me"}
    assert row["username"] == leader.username
    body = response.content.decode()
    assert str(leader.phone_number) not in body
    if leader.email:
        assert leader.email not in body
    assert "user_id" not in body and "national_id" not in body
    assert "metadata" not in body


def test_current_endpoint_falls_back_to_the_next_upcoming_challenge():
    now = timezone.now()
    make_challenge(now - timedelta(days=40), now - timedelta(days=10))
    later = make_challenge(now + timedelta(days=20), now + timedelta(days=50))
    upcoming = make_challenge(now + timedelta(days=1), now + timedelta(days=20))

    data = client_for(new_customer()).get(CURRENT_URL).json()

    assert data["id"] == upcoming.pk != later.pk
    assert data["status"] == ReferralChallengeStatus.DRAFT
    assert data["seconds_until_start"] > 0
    assert data["top_10"] == []


def test_current_endpoint_never_returns_a_review_or_finalized_challenge():
    challenge = review_challenge()
    client = client_for(new_customer())

    assert client.get(CURRENT_URL).status_code == status.HTTP_404_NOT_FOUND
    ReferralChallengeService.finalize(challenge.pk, manager())
    assert client.get(CURRENT_URL).status_code == status.HTTP_404_NOT_FOUND


def test_account_score_is_the_same_in_every_mode():
    challenge = active_challenge()
    inviter = new_customer()
    SellerProfileFactory(user=inviter, is_active=False)
    give_score(inviter, challenge, 2)

    as_customer = client_for(inviter).get(CURRENT_URL).json()["me"]
    assert switch_user_profile(inviter, ProfileType.SELLER)[0] is True
    as_seller = client_for(inviter).get(CURRENT_URL).json()["me"]

    assert as_customer == as_seller
    assert as_seller["challenge_score"] == 2


# --- Periods -----------------------------------------------------------------------


def test_overlapping_challenge_is_rejected():
    challenge = active_challenge()

    with pytest.raises(ValidationError):
        make_challenge(
            challenge.ends_at - timedelta(days=1),
            challenge.ends_at + timedelta(days=30),
        )
    response = client_for(manager()).post(
        ADMIN_URL,
        {
            "name": "Overlap",
            "starts_at": challenge.starts_at - timedelta(days=1),
            "ends_at": challenge.starts_at + timedelta(days=1),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert ReferralChallenge.objects.count() == 1


def test_adjacent_periods_are_allowed():
    challenge = active_challenge()

    following = make_challenge(
        challenge.ends_at, challenge.ends_at + timedelta(days=30)
    )
    previous = make_challenge(
        challenge.starts_at - timedelta(days=31), challenge.starts_at
    )

    assert following.starts_at == challenge.ends_at
    assert previous.ends_at == challenge.starts_at


def test_invalid_period_is_rejected():
    now = timezone.now()
    client = client_for(manager())

    for ends_at in (now, now - timedelta(days=1)):
        response = client.post(
            ADMIN_URL,
            {"name": "Bad", "starts_at": now, "ends_at": ends_at},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    with pytest.raises(IntegrityError), transaction.atomic():
        ReferralChallenge.objects.create(name="Bad", starts_at=now, ends_at=now)


def test_create_applies_default_prizes_and_records_the_creator():
    creator = manager()
    now = timezone.now()

    response = client_for(creator).post(
        ADMIN_URL,
        {
            "name": "November",
            "starts_at": now + timedelta(days=1),
            "ends_at": now + timedelta(days=31),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["status"] == ReferralChallengeStatus.DRAFT
    assert data["created_by_id"] == creator.pk
    assert [(prize["rank"], prize["prize_code"]) for prize in data["prizes"]] == [
        (1, "IPHONE"),
        (2, "AIRPODS"),
        (3, "ANNUAL_LOFT_SUBSCRIPTION"),
    ]


def test_prize_ranks_must_be_unique_and_within_the_top_3():
    now = timezone.now()
    client = client_for(manager())
    base = {"name": "Prizes", "starts_at": now, "ends_at": now + timedelta(days=30)}

    duplicate = client.post(
        ADMIN_URL,
        {**base, "prizes": [
            {"rank": 1, "prize_code": "IPHONE", "title": "A"},
            {"rank": 1, "prize_code": "AIRPODS", "title": "B"},
        ]},
        format="json",
    )
    fourth = client.post(
        ADMIN_URL,
        {**base, "prizes": [{"rank": 4, "prize_code": "IPHONE", "title": "A"}]},
        format="json",
    )
    unknown = client.post(
        ADMIN_URL,
        {**base, "prizes": [{"rank": 1, "prize_code": "CAR", "title": "A"}]},
        format="json",
    )

    assert duplicate.status_code == fourth.status_code == unknown.status_code == 400
    assert not ReferralChallenge.objects.exists()


# --- Editing -----------------------------------------------------------------------


def test_draft_challenge_dates_and_prizes_can_be_changed():
    now = timezone.now()
    challenge = make_challenge(now + timedelta(days=1), now + timedelta(days=31))

    response = client_for(manager()).patch(
        f"{ADMIN_URL}{challenge.pk}/",
        {
            "name": "Renamed",
            "ends_at": now + timedelta(days=32),
            "prizes": [{"rank": 1, "prize_code": "AIRPODS", "title": "AirPods"}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    challenge.refresh_from_db()
    assert challenge.name == "Renamed"
    assert challenge.ends_at == now + timedelta(days=32)
    assert [(p["rank"], p["prize_code"]) for p in response.json()["prizes"]] == [
        (1, "AIRPODS")
    ]
    assert ReferralChallengePrize.all_objects.filter(challenge=challenge).count() == 4


def test_draft_update_cannot_create_an_overlap():
    challenge = active_challenge()
    draft = make_challenge(
        challenge.ends_at + timedelta(days=1),
        challenge.ends_at + timedelta(days=30),
    )

    response = client_for(manager()).patch(
        f"{ADMIN_URL}{draft.pk}/",
        {"starts_at": challenge.ends_at - timedelta(hours=1)},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize(
    "changes",
    [
        {"starts_at": "2020-01-01T00:00:00Z"},
        {"ends_at": "2099-01-01T00:00:00Z"},
        {"prizes": []},
    ],
)
def test_started_challenge_dates_and_prizes_are_frozen(changes):
    challenge = active_challenge()

    response = client_for(manager()).patch(
        f"{ADMIN_URL}{challenge.pk}/", changes, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    reloaded = ReferralChallenge.objects.get(pk=challenge.pk)
    assert reloaded.starts_at == challenge.starts_at
    assert reloaded.ends_at == challenge.ends_at
    assert ReferralChallengeService.prizes(challenge).count() == 3


def test_started_challenge_name_can_still_change():
    challenge = active_challenge()

    response = client_for(manager()).patch(
        f"{ADMIN_URL}{challenge.pk}/", {"name": "October final"}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["name"] == "October final"


def test_finalized_challenge_is_read_only():
    challenge = review_challenge()
    ReferralChallengeService.finalize(challenge.pk, manager())

    response = client_for(manager()).patch(
        f"{ADMIN_URL}{challenge.pk}/", {"name": "Changed"}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    challenge.refresh_from_db()
    assert challenge.name == "Challenge"


# --- Finalization ------------------------------------------------------------------


def finalize(challenge, user=None):
    return client_for(user or manager()).post(
        f"{ADMIN_URL}{challenge.pk}/finalize/",
        HTTP_ACCEPT_LANGUAGE="en",
    )


def test_active_challenge_cannot_be_finalized():
    challenge = active_challenge()

    response = finalize(challenge)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not ReferralChallengeWinner.objects.exists()


def test_pending_referral_in_window_blocks_finalization():
    challenge = review_challenge()
    make_referral(
        new_customer(),
        at=challenge.ends_at - timedelta(minutes=1),
        status=ReferralStatus.PENDING,
    )

    response = finalize(challenge)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Challenge has pending referrals awaiting review."
    }
    challenge.refresh_from_db()
    assert challenge.finalized_at is None


def test_pending_referral_outside_window_does_not_block():
    challenge = review_challenge()
    make_referral(new_customer(), at=challenge.ends_at, status=ReferralStatus.PENDING)
    make_referral(
        new_customer(),
        at=challenge.starts_at - timedelta(seconds=1),
        status=ReferralStatus.PENDING,
    )

    assert finalize(challenge).status_code == status.HTTP_200_OK


def test_finalization_after_reviews_snapshots_the_top_3_with_prizes():
    challenge = review_challenge()
    reviewer = manager()
    users = [new_customer() for _ in range(4)]
    for score, user in zip((5, 4, 3, 2), users):
        give_score(user, challenge, score)
    pending = make_referral(
        users[3],
        at=challenge.ends_at - timedelta(minutes=1),
        status=ReferralStatus.PENDING,
    )
    assert finalize(challenge).status_code == status.HTTP_400_BAD_REQUEST
    ReferralService.reject(pending.pk, reviewer)

    response = finalize(challenge, reviewer)

    assert response.status_code == status.HTTP_200_OK
    winners = list(ReferralChallengeWinner.objects.filter(challenge=challenge))
    assert [(w.rank, w.user_id, w.score) for w in winners] == [
        (1, users[0].pk, 5),
        (2, users[1].pk, 4),
        (3, users[2].pk, 3),
    ]
    assert [w.prize_code for w in winners] == [
        ReferralChallengePrizeCode.IPHONE,
        ReferralChallengePrizeCode.AIRPODS,
        ReferralChallengePrizeCode.ANNUAL_LOFT_SUBSCRIPTION,
    ]
    assert [w.prize.rank for w in winners] == [1, 2, 3]
    data = response.json()
    assert data["status"] == ReferralChallengeStatus.FINALIZED
    assert data["finalized_by_id"] == reviewer.pk
    assert data["leaderboard"] is None
    assert [w["username"] for w in data["winners"]] == [u.username for u in users[:3]]


def test_fewer_entrants_create_only_real_winners():
    challenge = review_challenge()
    only = new_customer()
    give_score(only, challenge, 1)
    new_customer()

    finalize(challenge)

    assert list(
        ReferralChallengeWinner.objects.values_list("rank", "user_id", "score")
    ) == [(1, only.pk, 1)]


def test_challenge_without_entrants_finalizes_with_no_winners():
    challenge = review_challenge()

    assert finalize(challenge).status_code == status.HTTP_200_OK
    assert not ReferralChallengeWinner.objects.exists()


def test_finalizing_twice_keeps_one_result():
    challenge = review_challenge()
    give_score(new_customer(), challenge, 2)
    give_score(new_customer(), challenge, 1)

    first = finalize(challenge)
    second = finalize(challenge)

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert first.json()["winners"] == second.json()["winners"]
    assert first.json()["finalized_at"] == second.json()["finalized_at"]
    assert ReferralChallengeWinner.objects.count() == 2


def test_winner_snapshots_do_not_follow_later_data_changes():
    challenge = review_challenge()
    winner, runner_up = new_customer(), new_customer()
    winner_referrals = give_score(winner, challenge, 2)
    give_score(runner_up, challenge, 1)
    finalize(challenge)

    Referral.objects.filter(pk__in=[r.pk for r in winner_referrals]).update(
        status=ReferralStatus.REJECTED
    )
    give_score(runner_up, challenge, 5)
    ReferralChallengePrize.objects.filter(challenge=challenge, rank=1).update(
        title="Changed"
    )

    row = ReferralChallengeWinner.objects.get(rank=1)
    assert (row.user_id, row.score, row.prize_title) == (winner.pk, 2, "iPhone")
    detail = client_for(manager()).get(f"{ADMIN_URL}{challenge.pk}/").json()
    assert [(w["rank"], w["username"], w["score"]) for w in detail["winners"]] == [
        (1, winner.username, 2),
        (2, runner_up.username, 1),
    ]
    with pytest.raises(ModelValidationError):
        row.save()
    with pytest.raises(ModelValidationError):
        row.delete()


def test_challenge_scoring_and_finalization_create_no_points():
    challenge = review_challenge()
    give_score(new_customer(), challenge, 3)
    before = PointTransaction.objects.count()

    ReferralChallengeService.leaderboard(challenge)
    ReferralChallengeService.progress(challenge, new_customer())
    finalize(challenge)

    assert PointTransaction.objects.count() == before == 0


# --- Independence from referral rewards --------------------------------------------


def test_approval_pays_referral_pp_and_adds_one_score_independently():
    challenge = active_challenge()
    inviter, referred = new_customer(), new_customer()
    code = ReferralService.create_code(inviter.active_profile)
    referral = ReferralService.redeem_code(referred.active_profile, code.code)
    assert score_of(challenge, inviter) == 0

    ReferralService.approve(referral.pk, manager())

    approval_rows = PointTransaction.objects.filter(
        user=inviter, source_type=RewardSourceType.REFERRAL_APPROVED
    )
    assert approval_rows.get().points == 30
    assert score_of(challenge, inviter) == 1


def test_first_bid_reward_is_independent_of_the_challenge_score():
    challenge = active_challenge()
    inviter, referred = new_customer(), new_customer()
    code = ReferralService.create_code(inviter.active_profile)
    referral = ReferralService.redeem_code(referred.active_profile, code.code)
    ReferralService.approve(referral.pk, manager())
    item = AuctionItemFactory(auction=AuctionFactory())

    BiddingService.place_bid(
        item,
        referred.active_profile,
        item.current_price + Decimal("1.00"),
    )

    first_bid = PointTransaction.objects.filter(
        user=inviter, source_type=RewardSourceType.REFERRAL_FIRST_BID
    )
    assert first_bid.get().points == 200
    assert score_of(challenge, inviter) == 1


# --- Permissions -------------------------------------------------------------------


def test_customers_and_sellers_cannot_manage_challenges():
    challenge = active_challenge()
    payload = {
        "name": "X",
        "starts_at": challenge.ends_at,
        "ends_at": challenge.ends_at + timedelta(days=30),
    }
    for user in (new_customer(), new_seller()):
        client = client_for(user)
        assert client.get(ADMIN_URL).status_code == status.HTTP_403_FORBIDDEN
        assert client.post(ADMIN_URL, payload, format="json").status_code == 403
        assert client.get(f"{ADMIN_URL}{challenge.pk}/").status_code == 403
        assert client.patch(
            f"{ADMIN_URL}{challenge.pk}/", {"name": "X"}, format="json"
        ).status_code == 403
        assert client.post(f"{ADMIN_URL}{challenge.pk}/finalize/").status_code == 403
    assert ReferralChallenge.objects.count() == 1


def test_managers_need_the_exact_permission_for_each_action():
    challenge = review_challenge()
    lister = client_for(manager(permissions=["referral_challenges.list"]))
    payload = {
        "name": "X",
        "starts_at": challenge.ends_at + timedelta(days=60),
        "ends_at": challenge.ends_at + timedelta(days=90),
    }

    assert lister.get(ADMIN_URL).status_code == status.HTTP_200_OK
    assert lister.get(f"{ADMIN_URL}{challenge.pk}/").status_code == 403
    assert lister.post(ADMIN_URL, payload, format="json").status_code == 403
    assert lister.patch(
        f"{ADMIN_URL}{challenge.pk}/", {"name": "X"}, format="json"
    ).status_code == 403
    assert lister.post(f"{ADMIN_URL}{challenge.pk}/finalize/").status_code == 403

    finisher = client_for(manager(permissions=["referral_challenges.finalize"]))
    assert finisher.post(f"{ADMIN_URL}{challenge.pk}/finalize/").status_code == 200
    creator = client_for(manager(permissions=["referral_challenges.create"]))
    assert creator.post(ADMIN_URL, payload, format="json").status_code == 201


def test_admin_detail_shows_live_leaderboard_and_review_backlog():
    challenge = review_challenge()
    scorer = new_customer()
    give_score(scorer, challenge, 2)
    make_referral(
        new_customer(),
        at=challenge.starts_at + timedelta(days=1),
        status=ReferralStatus.PENDING,
    )

    data = client_for(manager()).get(f"{ADMIN_URL}{challenge.pk}/").json()

    assert data["status"] == ReferralChallengeStatus.REVIEW
    assert data["pending_referrals_count"] == 1
    leaderboard = [
        (row["rank"], row["user_id"], row["challenge_score"])
        for row in data["leaderboard"]
    ]
    assert leaderboard == [(1, scorer.pk, 2)]
    assert data["winners"] == []


def test_admin_list_includes_prizes_and_status():
    active_challenge()

    response = client_for(manager()).get(ADMIN_URL)

    assert response.status_code == status.HTTP_200_OK
    row = response.json()["results"][0]
    assert row["status"] == ReferralChallengeStatus.ACTIVE
    assert len(row["prizes"]) == 3
    assert "leaderboard" not in row


# --- Migrations --------------------------------------------------------------------


@override_settings(MIGRATION_MODULES={})
def test_referral_migrations_match_the_models():
    loader = MigrationLoader(connection)
    assert ("referrals", "0004_referral_challenge") in loader.graph.nodes
    # Like makemigrations, compare untranslated labels.
    with translation.override(None):
        changes = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(django_apps),
        ).changes(graph=loader.graph)

    assert "referrals" not in changes
    state = loader.project_state(("referrals", "0004_referral_challenge"))
    winner = state.models["referrals", "referralchallengewinner"]
    assert {c.name for c in winner.options["constraints"]} == {
        "ref_chal_winner_rank_uniq",
        "ref_chal_winner_user_uniq",
        "ref_chal_winner_rank_range",
        "ref_chal_winner_score_gte_1",
    }
