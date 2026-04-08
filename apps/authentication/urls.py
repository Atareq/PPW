from django.urls import path

from apps.authentication.views import (
    CustomerRegisterAPIView,
    LoginAPIView,
    ManagerRegisterAPIView,
    SwitchProfileAPIView,
)

urlpatterns = [
    path("customer/sign-up/", CustomerRegisterAPIView.as_view()),
    path("manager/sign-up/", ManagerRegisterAPIView.as_view()),
    path("login/", LoginAPIView.as_view()),
    path("switch-profile/", SwitchProfileAPIView.as_view()),
]
