from django.urls import path

from apps.referrals.views import (
    AdminReferralApproveViewSet,
    AdminReferralChallengeFinalizeViewSet,
    AdminReferralChallengeViewSet,
    AdminReferralRejectViewSet,
    AdminReferralViewSet,
    CurrentReferralChallengeViewSet,
    LinkSharingCodeViewSet,
    MyReferralViewSet,
    ReferralRedeemViewSet,
)

CREATE_ACTION = {"post": "create"}
LIST_ACTION = {"get": "list"}
RETRIEVE_ACTION = {"get": "retrieve"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
RETRIEVE_PARTIAL_UPDATE_ACTION = {"get": "retrieve", "patch": "partial_update"}

urlpatterns = [
    path(
        "codes/",
        LinkSharingCodeViewSet.as_view(CREATE_ACTION),
        name="referral-code-create",
    ),
    path(
        "redeem/",
        ReferralRedeemViewSet.as_view(CREATE_ACTION),
        name="referral-redeem",
    ),
    path(
        "my/",
        MyReferralViewSet.as_view(LIST_ACTION),
        name="my-referral-list",
    ),
    path(
        "admin/",
        AdminReferralViewSet.as_view(LIST_ACTION),
        name="admin-referral-list",
    ),
    path(
        "admin/<int:pk>/approve/",
        AdminReferralApproveViewSet.as_view(CREATE_ACTION),
        name="admin-referral-approve",
    ),
    path(
        "admin/<int:pk>/reject/",
        AdminReferralRejectViewSet.as_view(CREATE_ACTION),
        name="admin-referral-reject",
    ),
    path(
        "challenge/current/",
        CurrentReferralChallengeViewSet.as_view(RETRIEVE_ACTION),
        name="referral-challenge-current",
    ),
    path(
        "admin/challenges/",
        AdminReferralChallengeViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-referral-challenge-list",
    ),
    path(
        "admin/challenges/<int:pk>/",
        AdminReferralChallengeViewSet.as_view(RETRIEVE_PARTIAL_UPDATE_ACTION),
        name="admin-referral-challenge-detail",
    ),
    path(
        "admin/challenges/<int:pk>/finalize/",
        AdminReferralChallengeFinalizeViewSet.as_view(CREATE_ACTION),
        name="admin-referral-challenge-finalize",
    ),
]
