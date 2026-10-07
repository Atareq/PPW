from django.urls import path

from apps.promotions.views import (
    ActivePromotionCampaignViewSet,
    AdminCashbackOfferViewSet,
    AdminCashbackTransactionViewSet,
    AdminDiscountOfferViewSet,
    AdminPromotionCampaignActivationViewSet,
    AdminPromotionCampaignEffectViewSet,
    AdminPromotionCampaignViewSet,
    CashbackAdjustmentViewSet,
    CashbackBalanceViewSet,
    CashbackOfferActivationViewSet,
    CurrentCashbackOfferViewSet,
    CurrentDiscountOfferViewSet,
    DiscountOfferActivationViewSet,
    MyCashbackTransactionViewSet,
    MyPromotionGrantViewSet,
    OnboardingHighlightBenefitViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
RETRIEVE_ACTION = {"get": "retrieve"}
DETAIL_UPDATE_ACTION = {"get": "retrieve", "patch": "partial_update"}
DETAIL_UPDATE_DESTROY_ACTION = {
    "get": "retrieve",
    "patch": "partial_update",
    "delete": "destroy",
}

urlpatterns = [
    path(
        "active/",
        ActivePromotionCampaignViewSet.as_view(LIST_ACTION),
        name="active-promotion-campaign-list",
    ),
    path(
        "discount-offers/current/",
        CurrentDiscountOfferViewSet.as_view(LIST_ACTION),
        name="current-discount-offer-list",
    ),
    path(
        "cashback-offers/current/",
        CurrentCashbackOfferViewSet.as_view(LIST_ACTION),
        name="current-cashback-offer-list",
    ),
    path(
        "cashback/my/",
        MyCashbackTransactionViewSet.as_view(LIST_ACTION),
        name="my-cashback-transaction-list",
    ),
    path(
        "my-grants/",
        MyPromotionGrantViewSet.as_view(LIST_ACTION),
        name="my-promotion-grant-list",
    ),
    path(
        "onboarding/highlight-benefit/",
        OnboardingHighlightBenefitViewSet.as_view(RETRIEVE_ACTION),
        name="onboarding-highlight-benefit",
    ),
    path(
        "cashback/balance/",
        CashbackBalanceViewSet.as_view(RETRIEVE_ACTION),
        name="cashback-balance",
    ),
    path(
        "admin/campaigns/",
        AdminPromotionCampaignViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-promotion-campaign-list",
    ),
    path(
        "admin/campaigns/<int:pk>/",
        AdminPromotionCampaignViewSet.as_view(DETAIL_UPDATE_ACTION),
        name="admin-promotion-campaign-detail",
    ),
    path(
        "admin/campaigns/<int:pk>/enable/",
        AdminPromotionCampaignActivationViewSet.as_view(
            CREATE_ACTION,
            enable=True,
        ),
        name="admin-promotion-campaign-enable",
    ),
    path(
        "admin/campaigns/<int:pk>/disable/",
        AdminPromotionCampaignActivationViewSet.as_view(
            CREATE_ACTION,
            enable=False,
        ),
        name="admin-promotion-campaign-disable",
    ),
    path(
        "admin/campaigns/<int:campaign_id>/effects/",
        AdminPromotionCampaignEffectViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-promotion-campaign-effect-list",
    ),
    path(
        "admin/campaigns/<int:campaign_id>/effects/<int:pk>/",
        AdminPromotionCampaignEffectViewSet.as_view(DETAIL_UPDATE_DESTROY_ACTION),
        name="admin-promotion-campaign-effect-detail",
    ),
    path(
        "admin/discount-offers/",
        AdminDiscountOfferViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-discount-offer-list",
    ),
    path(
        "admin/discount-offers/<int:pk>/activate/",
        DiscountOfferActivationViewSet.as_view(
            CREATE_ACTION,
            activate=True,
        ),
        name="admin-discount-offer-activate",
    ),
    path(
        "admin/discount-offers/<int:pk>/deactivate/",
        DiscountOfferActivationViewSet.as_view(
            CREATE_ACTION,
            activate=False,
        ),
        name="admin-discount-offer-deactivate",
    ),
    path(
        "admin/cashback-offers/",
        AdminCashbackOfferViewSet.as_view(LIST_CREATE_ACTION),
        name="admin-cashback-offer-list",
    ),
    path(
        "admin/cashback-offers/<int:pk>/activate/",
        CashbackOfferActivationViewSet.as_view(
            CREATE_ACTION,
            activate=True,
        ),
        name="admin-cashback-offer-activate",
    ),
    path(
        "admin/cashback-offers/<int:pk>/deactivate/",
        CashbackOfferActivationViewSet.as_view(
            CREATE_ACTION,
            activate=False,
        ),
        name="admin-cashback-offer-deactivate",
    ),
    path(
        "admin/cashback-transactions/",
        AdminCashbackTransactionViewSet.as_view(LIST_ACTION),
        name="admin-cashback-transaction-list",
    ),
    path(
        "admin/cashback-adjustments/",
        CashbackAdjustmentViewSet.as_view(CREATE_ACTION),
        name="admin-cashback-adjustment-create",
    ),
]
