from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.authentication.views import (
    CustomerRegisterAPIView,
    LoginAPIView,
    ManagerRegisterAPIView,
    SwitchProfileAPIView,
)

urlpatterns = [
    path("register/customer/", CustomerRegisterAPIView.as_view()),
    path("register/manager/", ManagerRegisterAPIView.as_view()),
    path("login/", LoginAPIView.as_view()),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("switch-profile/", SwitchProfileAPIView.as_view()),
]
