from django.urls import path

from apps.payments.views import (
    PaymentRequestCreateViewSet,
    PaymentRequestDecisionViewSet,
    PaymentRequestViewSet,
)

LIST_ACTION = {"get": "list"}
CREATE_ACTION = {"post": "create"}
PARTIAL_UPDATE_ACTION = {"patch": "partial_update"}

app_name = "payments"

urlpatterns = [
    path(
        "requests/",
        PaymentRequestViewSet.as_view(LIST_ACTION),
        name="payment-request-list",
    ),
    path(
        "requests/auction-item/",
        PaymentRequestCreateViewSet.as_view(
            CREATE_ACTION,
            source_type="auction",
        ),
        name="auction-payment-request-create",
    ),
    path(
        "requests/order-item/",
        PaymentRequestCreateViewSet.as_view(
            CREATE_ACTION,
            source_type="market",
        ),
        name="market-payment-request-create",
    ),
    path(
        "requests/<int:pk>/",
        PaymentRequestViewSet.as_view(PARTIAL_UPDATE_ACTION),
        name="payment-request-update",
    ),
    path(
        "requests/<int:pk>/approve/",
        PaymentRequestDecisionViewSet.as_view(
            CREATE_ACTION,
            decision="approve",
        ),
        name="payment-request-approve",
    ),
    path(
        "requests/<int:pk>/reject/",
        PaymentRequestDecisionViewSet.as_view(
            CREATE_ACTION,
            decision="reject",
        ),
        name="payment-request-reject",
    ),
]
