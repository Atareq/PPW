from django.urls import path

from apps.referrals.views import LinkSharingCodeViewSet, ReferralRedeemViewSet

CREATE_ACTION = {"post": "create"}

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
]
