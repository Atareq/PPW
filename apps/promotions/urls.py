from django.urls import path

from apps.promotions.views import (
    AdminCashbackOfferViewSet,
    AdminCashbackTransactionViewSet,
    AdminDiscountOfferViewSet,
    CashbackAdjustmentViewSet,
    CashbackBalanceViewSet,
    CashbackOfferActivationViewSet,
    CurrentCashbackOfferViewSet,
    CurrentDiscountOfferViewSet,
    DiscountOfferActivationViewSet,
    MyCashbackTransactionViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
RETRIEVE_ACTION = {"get": "retrieve"}

urlpatterns = [
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
        "cashback/balance/",
        CashbackBalanceViewSet.as_view(RETRIEVE_ACTION),
        name="cashback-balance",
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
