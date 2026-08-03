from django.urls import path

from apps.loft_subscriptions.views import (
    CurrentSubscriptionViewSet,
    SubscriptionCatalogViewSet,
    SubscriptionCheckoutViewSet,
    SubscriptionInvoiceViewSet,
    SubscriptionPaymentConfirmViewSet,
    SubscriptionPaymentRequestViewSet,
    SubscriptionReviseViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
RETRIEVE_ACTION = {"get": "retrieve"}

app_name = "loft_subscriptions"

urlpatterns = [
    path(
        "catalog/",
        SubscriptionCatalogViewSet.as_view(LIST_ACTION),
        name="catalog",
    ),
    path(
        "revise/",
        SubscriptionReviseViewSet.as_view(CREATE_ACTION),
        name="revise",
    ),
    path(
        "checkout/",
        SubscriptionCheckoutViewSet.as_view(CREATE_ACTION),
        name="checkout",
    ),
    path(
        "current/",
        CurrentSubscriptionViewSet.as_view(LIST_ACTION),
        name="current",
    ),
    path(
        "invoices/",
        SubscriptionInvoiceViewSet.as_view(LIST_ACTION),
        name="invoice-list",
    ),
    path(
        "invoices/<int:pk>/",
        SubscriptionInvoiceViewSet.as_view(RETRIEVE_ACTION),
        name="invoice-detail",
    ),
    path(
        "payment-requests/",
        SubscriptionPaymentRequestViewSet.as_view(LIST_ACTION),
        name="payment-request-list",
    ),
    path(
        "payment-requests/<int:pk>/",
        SubscriptionPaymentRequestViewSet.as_view(RETRIEVE_ACTION),
        name="payment-request-detail",
    ),
    path(
        "payment-requests/<int:pk>/confirm/",
        SubscriptionPaymentConfirmViewSet.as_view(CREATE_ACTION),
        name="payment-request-confirm",
    ),
]
